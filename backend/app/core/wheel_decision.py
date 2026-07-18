"""Wheel 持仓动态决策树(量化阈值版)

所有形容词决策映射为数字阈值(见 POSITION_QUANT)。
输出 action_code + priority + confidence + reasons(含数字) + quant_thresholds。

优先级序(命中即停):
  1 深ITM/临期ITM → 2 提前行权 → 3 吃θ → 4 硬止盈/过高持有
  → 5 软止盈+低效 → 6 ≤21DTE处理 → 7 纯低效 → 8 超愿接/纪律否决
  → 9 浮亏OTM → 10 浅ITM观察 → 11 健康持有
"""
from typing import Any, Dict, List, Optional, Tuple

# action_code 枚举(前端建卡/按钮用,少做字符串匹配)
ACTION_CLOSE = "CLOSE"
ACTION_ROLL = "ROLL"
ACTION_ROLL_ADJUST = "ROLL_ADJUST"
ACTION_HOLD_THETA = "HOLD_THETA"
ACTION_REPLACE = "REPLACE"
ACTION_PREPARE_ASSIGN = "PREPARE_ASSIGN"
ACTION_NONE = "NONE"

# ── 量化默认(设置页 wheel_position 可覆盖) ──────────────────────────────────
# 单位: 百分比用「点」(50=50%), DTE/美元/小数 delta 见注释
POSITION_QUANT: Dict[str, float] = {
    "profit_target_pct": 50.0,          # 硬止盈
    "soft_profit_pct": 30.0,            # 软止盈
    "max_hold_profit_pct": 80.0,        # 过高持有 → 落袋
    "hold_theta_min_profit_pct": 40.0,  # 吃θ最低浮盈
    "hold_theta_max_dte": 14.0,         # 吃θ最长 DTE(配合剩余年化)
    "hold_theta_min_remaining_ann": 12.0,
    "hard_roll_dte": 21.0,              # 硬处理窗
    "gamma_warn_dte": 7.0,              # 临期/gamma
    "shallow_itm_pct": 1.5,             # 浅 ITM 价内%
    "deep_itm_moneyness_pct": 3.0,      # 深 ITM 价内%
    "deep_itm_delta": 0.50,             # 深 ITM Δ
    "shallow_itm_delta_max": 0.55,
    "thin_otm_buffer_pct": 1.5,         # 薄 OTM 垫%
    "threat_otm_buffer_pct": 5.0,       # 浮亏威胁垫% (≤hard_roll 内)
    "min_close_notional": 20.0,         # 买回名义过低 → 手续费陷阱 $
    "capital_tight_util_pct": 75.0,     # 资金紧利用率%
    "dividend_warn_days": 14.0,
    # CC 提前行权 Δ 门槛
    "early_assign_delta_deep": 0.80,
    "early_assign_delta_div": 0.55,
    "early_assign_delta_shallow_div": 0.45,
    "early_assign_delta_otm_div": 0.70,
    # would_open 薄垫 caution
    "open_caution_buffer_pct": 2.0,
}


def _cfg_num(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def merge_pos_quant(pos_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """合并量化默认与用户配置。"""
    out = dict(POSITION_QUANT)
    if pos_cfg:
        for k, v in pos_cfg.items():
            if k in out or k in (
                "profit_target_pct", "soft_profit_pct", "hard_roll_dte",
                "gamma_warn_dte", "hold_theta_min_profit_pct", "hold_theta_max_dte",
                "hold_theta_min_remaining_ann", "thin_otm_buffer_pct",
                "max_hold_profit_pct", "min_close_notional", "shallow_itm_pct",
                "deep_itm_moneyness_pct", "capital_tight_util_pct",
                "dividend_warn_days", "threat_otm_buffer_pct",
                "deep_itm_delta", "shallow_itm_delta_max",
                "early_assign_delta_deep", "early_assign_delta_div",
                "early_assign_delta_shallow_div", "early_assign_delta_otm_div",
                "open_caution_buffer_pct",
            ):
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


def remaining_annualized(close_px: float, strike: float, dte: Optional[int]) -> Optional[float]:
    if not strike or not dte or dte <= 0 or close_px <= 0:
        return None
    return round(close_px / strike * 365 / dte * 100, 2)


def residual_floor(min_annualized: float, pos_cfg: Optional[Dict[str, Any]] = None) -> float:
    """剩余年化「仍值得拿」的下限。"""
    q = merge_pos_quant(pos_cfg)
    floor = float(q["hold_theta_min_remaining_ann"])
    if min_annualized and min_annualized > 0:
        floor = min(floor, max(8.0, min_annualized * 0.5))
    return floor


def eval_hold_for_theta(
    *,
    itm: bool,
    deep_itm: bool,
    profit_pct: Optional[float],
    dte: Optional[int],
    remaining_ann: Optional[float],
    close_notional: float,
    min_annualized: float,
    pos_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一「是否吃 θ」判定,持仓树与 Roll 场景共用。"""
    q = merge_pos_quant(pos_cfg)
    hold_theta_min_profit = float(q["hold_theta_min_profit_pct"])
    hold_theta_max_dte = int(q["hold_theta_max_dte"])
    gamma_dte = int(q["gamma_warn_dte"])
    min_close_notional = float(q["min_close_notional"])
    rem_floor = residual_floor(min_annualized, pos_cfg)

    underwater = profit_pct is not None and profit_pct < 0
    fee_trap = bool(
        close_notional > 0
        and close_notional < min_close_notional
        and not itm
        and profit_pct is not None
        and profit_pct >= hold_theta_min_profit
    )
    # 仅浮盈仓:「剩余年化高」= 权利金还值得继续收
    # 浮亏仓:剩余权利金高 = 市场仍定价风险,不可当成健康收租信号
    residual_worth = bool(
        not underwater
        and remaining_ann is not None
        and remaining_ann >= rem_floor
    )
    hold = bool(
        not underwater
        and not itm
        and not deep_itm
        and profit_pct is not None
        and profit_pct >= hold_theta_min_profit
        and dte is not None
        and (
            fee_trap
            or dte <= gamma_dte
            or (dte <= hold_theta_max_dte and residual_worth)
        )
    )
    return {
        "hold_for_theta": hold,
        "fee_trap": fee_trap,
        "residual_worth_keeping": residual_worth,
        "underwater": underwater,
        "rem_floor": rem_floor,
        "hold_theta_min_profit": hold_theta_min_profit,
        "hold_theta_max_dte": hold_theta_max_dte,
        "gamma_dte": gamma_dte,
        "min_close_notional": min_close_notional,
    }


def otm_buffer_pct(side: Optional[str], spot: float, strike: float) -> Optional[float]:
    """距行权价的安全垫%(正数=仍 OTM 的距离)。"""
    if not spot or not strike or spot <= 0 or strike <= 0:
        return None
    if side == "PUT":
        # spot > strike → OTM
        return round((spot - strike) / spot * 100, 2)
    if side == "CALL":
        # strike > spot → OTM
        return round((strike - spot) / spot * 100, 2)
    return None


def eval_would_open_today(
    *,
    side: Optional[str],
    strike: float,
    spot: float,
    floor_price: Optional[float],
    strike_above_floor: bool,
    thin_otm: bool,
    buffer: Optional[float],
    itm: bool,
    deep_itm: bool,
    trend: Optional[str],
    capital_tight: bool,
    target_enabled: Optional[bool] = None,
    dte: Optional[int] = None,
    pos_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """反事实:以今天纪律还会不会新开这张腿。

    返回 would_open_today: yes|no|caution|unknown + reasons。
    轻量规则,不跑全量 admission。
    """
    q = merge_pos_quant(pos_cfg)
    caution_buf = float(q.get("open_caution_buffer_pct", 2.0))
    gamma_dte = int(q["gamma_warn_dte"])
    reasons: List[str] = []
    hard_no = False
    caution = False
    missing_key = False

    if target_enabled is False:
        hard_no = True
        reasons.append("标的已禁用,不会新开")

    if side == "PUT":
        if floor_price is None or floor_price <= 0:
            missing_key = True
            reasons.append("未设置愿接最高价(floor),无法完整校验开仓纪律")
        elif strike_above_floor or strike > floor_price:
            hard_no = True
            reasons.append(
                f"strike {strike:g} > 愿接最高价 {floor_price:g},超过愿接价不宜等接货/不会新开"
            )
        if trend == "DOWN":
            hard_no = True
            reasons.append("趋势 DOWN,按纪律不新开 Put")
        elif trend == "WEAK":
            caution = True
            reasons.append("趋势 WEAK,新开 Put 需谨慎")
        if capital_tight:
            caution = True
            reasons.append(
                f"组合资金偏紧(利用率≥{q['capital_tight_util_pct']:.0f}%),不宜再占担保开新 Put"
            )
        if thin_otm or (buffer is not None and buffer < caution_buf and not itm):
            caution = True
            reasons.append(
                f"安全垫{buffer if buffer is not None else '?'}% < {caution_buf}% ,新开同类 Put 风险高"
            )
        if itm or deep_itm:
            caution = True
            reasons.append("已 ITM,更不应以现价「新开」同结构")
    elif side == "CALL":
        if trend == "DOWN":
            caution = True
            reasons.append("趋势 DOWN,CC 需确认仍愿持股收租")
        if deep_itm or (itm and dte is not None and dte <= gamma_dte):
            caution = True
            reasons.append(f"Call 已深/临期 ITM(DTE≤{gamma_dte}),不会以当前结构新开")
        if capital_tight:
            caution = True
            reasons.append("资金紧时优先处理既有仓,不急新开 CC")
    else:
        missing_key = True
        reasons.append("缺少 side")

    if not spot or not strike:
        missing_key = True
        if "缺" not in " ".join(reasons):
            reasons.append("缺 spot/strike,无法完整判断")

    if hard_no:
        verdict = "no"
    elif missing_key and not caution and side == "PUT" and (floor_price is None or floor_price <= 0):
        verdict = "unknown"
    elif caution:
        verdict = "caution"
    elif missing_key:
        verdict = "unknown"
    else:
        verdict = "yes"
        if side == "PUT":
            reasons.append("strike 在底线内、无硬否决因子,纪律上仍可能新开同类 Put")
        else:
            reasons.append("无硬否决因子,纪律上仍可能新开同类 CC")

    return {
        "would_open_today": verdict,
        "would_open_reasons": reasons,
    }


def build_assign_checklist(
    *,
    side: Optional[str],
    strike: float,
    qty: float,
    size: float,
    floor_price: Optional[float],
    strike_above_floor: bool,
    itm: bool,
    deep_itm: bool,
    expiring: bool,
    early_assign: bool,
    share_cost: Optional[float] = None,
    cost_basis: Optional[float] = None,
    equity: Optional[float] = None,
    symbol_max_capital: Optional[float] = None,
    symbol_committed: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """接货/交货清单骨架。CSP 强调担保已覆盖,不恐吓「再付全额现金」。"""
    need = bool(itm or deep_itm or expiring or early_assign)
    if not need or not strike or not side:
        return None

    notional = round(float(strike) * float(qty) * float(size), 2)
    notes: List[str] = []
    floor_ok: Optional[bool] = None
    if side == "PUT":
        if floor_price is not None and floor_price > 0:
            floor_ok = not strike_above_floor and strike <= floor_price
            if not floor_ok:
                notes.append(
                    f"strike {strike:g} 高于愿接最高价 {floor_price:g},被指派不符合预设愿接价"
                )
        else:
            notes.append("未设愿接最高价(floor),请自行确认愿接价")
        notes.append("CSP 现金担保通常已覆盖行权名义:指派多为担保变正股,一般不必再掏同等现金")
        next_step = "接货后可按成本基础/现价扫描 Covered Call(Call 用成本底线,不用 floor)"
        collateral_covers = True
    else:
        floor_ok = None
        collateral_covers = None  # CC 不占 CSP 担保
        next_step = "被 call 后轮子可结束,或现金到位后重开 CSP"
        notes.append("被 call 走 = 按 strike 卖出持股,请确认愿意在此价交货")
        notes.append("Covered Call 用持股成本底线约束 strike,与 CSP 愿接价 floor 无关")
        cb = cost_basis if cost_basis is not None else share_cost
        if cb is not None and strike:
            pnl_ps = float(strike) - float(cb)
            notes.append(
                f"相对成本约 ${cb:g}:交货粗算每股 {'盈利' if pnl_ps >= 0 else '亏损'} ${abs(pnl_ps):.2f}"
                " (未计累计权利金)"
            )

    post_holding_pct = None
    over_symbol_cap = None
    if side == "PUT" and equity and equity > 0 and notional > 0:
        # 接货后该腿持股名义占净值(简化:用 assign notional)
        post_holding_pct = round(notional / float(equity) * 100, 1)
    if side == "PUT" and symbol_max_capital and symbol_max_capital > 0:
        post_committed = float(symbol_committed or 0)
        # 接货后占用近似 max(原占用, notional)——CSP 担保已计入时用 notional 作持股成本代理
        post_val = max(post_committed, notional)
        over_symbol_cap = post_val > float(symbol_max_capital) + 1e-6
        if over_symbol_cap:
            notes.append(
                f"接货后名义约 ${post_val:,.0f} 可能超过标的上限 ${symbol_max_capital:,.0f}"
            )

    return {
        "side": side,
        "strike": strike,
        "assign_notional": notional,
        "collateral_covers": collateral_covers,
        "floor_ok": floor_ok,
        "floor_price": floor_price,
        "post_holding_pct": post_holding_pct,
        "over_symbol_cap": over_symbol_cap,
        "next_step_hint": next_step,
        "notes": notes,
        "qty": qty,
        "contract_size": size,
    }


def decide_position(
    item: Dict[str, Any],
    min_annualized: float,
    profit_target: float,
    pos_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """输入 item 建议含:
      side/strike/spot/dte/current_price/buyback_ask/profit_pct/itm/delta/expiring
      qty(张,默认1)/contract_size(默认100)/days_to_ex_div(除息剩几天,可选)
      可选资本上下文: capital_util_pct / capital_tight / portfolio_put_blocked / symbol_headroom
      可选: trend(UP|WEAK|DOWN)/target_enabled/share_cost/cost_basis/equity/symbol_max_capital/symbol_committed

    返回增强字段见末尾 dict。
    """
    cfg = pos_cfg or {}
    q = merge_pos_quant(cfg)
    # 调用方 profit_target 优先(体检 API 传入),否则用量化默认
    profit_target = float(profit_target if profit_target is not None else q["profit_target_pct"])
    soft_profit = float(cfg.get("soft_profit_pct", q["soft_profit_pct"]))
    if soft_profit <= 0:
        soft_profit = max(profit_target * 0.6, 30.0)
    hard_dte = int(q["hard_roll_dte"])
    gamma_dte = int(q["gamma_warn_dte"])
    hold_theta_min_profit = float(q["hold_theta_min_profit_pct"])
    hold_theta_max_dte = int(q["hold_theta_max_dte"])
    shallow_itm_pct = float(q["shallow_itm_pct"])
    deep_moneyness_pct = float(q["deep_itm_moneyness_pct"])
    deep_itm_delta = float(q["deep_itm_delta"])
    shallow_delta_max = float(q["shallow_itm_delta_max"])
    thin_otm_pct = float(q["thin_otm_buffer_pct"])
    threat_buf = float(q["threat_otm_buffer_pct"])
    max_hold_profit_pct = float(q["max_hold_profit_pct"])
    capital_tight_util = float(q["capital_tight_util_pct"])
    div_warn_days = int(q["dividend_warn_days"])
    ea_deep = float(q["early_assign_delta_deep"])
    ea_div = float(q["early_assign_delta_div"])
    ea_shallow = float(q["early_assign_delta_shallow_div"])
    ea_otm = float(q["early_assign_delta_otm_div"])

    strike = item.get("strike") or 0
    spot = item.get("spot") or 0
    dte = item.get("dte")
    buyback = item.get("buyback_ask")
    last = item.get("current_price") or 0
    close_px = float(buyback) if buyback is not None and float(buyback) > 0 else float(last or 0)
    delta = abs(item.get("delta") or 0)
    itm = bool(item.get("itm"))
    profit_pct = item.get("profit_pct")
    profit_hit = profit_pct is not None and profit_pct >= profit_target
    soft_hit = profit_pct is not None and profit_pct >= soft_profit
    underwater = profit_pct is not None and profit_pct < 0
    side = item.get("side")
    floor_price = item.get("floor_price")
    try:
        floor_price = float(floor_price) if floor_price is not None else None
    except (TypeError, ValueError):
        floor_price = None
    strike_above_floor = bool(
        side == "PUT" and floor_price is not None and floor_price > 0 and strike > floor_price
    )
    expiring = bool(item.get("expiring")) or (dte is not None and dte <= gamma_dte)
    qty = float(item.get("qty") or 1)
    size = float(item.get("contract_size") or 100)
    close_notional = close_px * size * qty if close_px > 0 else 0.0
    days_to_div = item.get("days_to_ex_div")
    div_window = days_to_div is not None and int(days_to_div) >= 0 and int(days_to_div) <= div_warn_days

    capital_util_pct = item.get("capital_util_pct")
    try:
        capital_util_pct = float(capital_util_pct) if capital_util_pct is not None else None
    except (TypeError, ValueError):
        capital_util_pct = None
    portfolio_put_blocked = bool(item.get("portfolio_put_blocked"))
    symbol_headroom = item.get("symbol_headroom")
    try:
        symbol_headroom = float(symbol_headroom) if symbol_headroom is not None else None
    except (TypeError, ValueError):
        symbol_headroom = None
    if "capital_tight" in item and item.get("capital_tight") is not None:
        capital_tight = bool(item.get("capital_tight"))
    else:
        capital_tight = bool(
            capital_util_pct is not None and capital_util_pct >= capital_tight_util
        )

    remaining_ann = remaining_annualized(close_px, float(strike), dte if isinstance(dte, int) else None)

    low_yield = bool(
        not itm and remaining_ann is not None
        and min_annualized > 0 and remaining_ann < min_annualized
    )

    moneyness = 0.0
    if spot > 0 and strike > 0:
        moneyness = (strike - spot) / spot if side == "PUT" else (spot - strike) / spot
    moneyness_pct = moneyness * 100
    deep_itm = bool(itm and (delta > deep_itm_delta or moneyness_pct > deep_moneyness_pct))
    shallow_itm = bool(
        itm and not deep_itm
        and (moneyness_pct <= shallow_itm_pct and delta <= shallow_delta_max)
    )
    early_assign = bool(
        side == "CALL"
        and (
            (itm and (delta >= ea_deep or (div_window and delta >= ea_div)))
            or (itm and shallow_itm and div_window and delta >= ea_shallow)
            or (not itm and div_window and delta >= ea_otm)
        )
    )

    buffer = otm_buffer_pct(side, float(spot or 0), float(strike or 0))
    thin_otm = bool(not itm and buffer is not None and 0 <= buffer < thin_otm_pct)

    hold_meta = eval_hold_for_theta(
        itm=itm,
        deep_itm=deep_itm,
        profit_pct=profit_pct,
        dte=dte if isinstance(dte, int) else None,
        remaining_ann=remaining_ann,
        close_notional=close_notional,
        min_annualized=min_annualized,
        pos_cfg=cfg,
    )
    fee_trap = hold_meta["fee_trap"]
    residual_worth_keeping = hold_meta["residual_worth_keeping"]
    rem_floor = hold_meta["rem_floor"]
    hold_for_theta = hold_meta["hold_for_theta"]
    underwater = bool(hold_meta.get("underwater"))

    # 浮盈≥max_hold 且 DTE>gamma → 落袋优先于吃 θ
    profit_cap_close = bool(
        profit_pct is not None
        and profit_pct >= max_hold_profit_pct
        and dte is not None
        and dte > gamma_dte
        and not fee_trap
    )
    if profit_cap_close:
        hold_for_theta = False

    # 薄 OTM + 已止盈 + 非临期 → 不鼓励死拿 θ
    if thin_otm and hold_for_theta and dte is not None and dte > gamma_dte and profit_hit:
        hold_for_theta = False

    trend = item.get("trend")
    if isinstance(trend, dict):
        trend = trend.get("trend")
    trend = str(trend).upper() if trend else None
    if trend and trend not in ("UP", "WEAK", "DOWN"):
        trend = None
    target_enabled = item.get("target_enabled")
    if target_enabled is not None:
        target_enabled = bool(target_enabled)

    would_meta = eval_would_open_today(
        side=side,
        strike=float(strike or 0),
        spot=float(spot or 0),
        floor_price=floor_price,
        strike_above_floor=strike_above_floor,
        thin_otm=thin_otm,
        buffer=buffer,
        itm=itm,
        deep_itm=deep_itm,
        trend=trend,
        capital_tight=capital_tight,
        target_enabled=target_enabled,
        dte=dte if isinstance(dte, int) else None,
        pos_cfg=cfg,
    )
    would_open = would_meta["would_open_today"]
    would_open_reasons = list(would_meta["would_open_reasons"] or [])

    # 浮亏 OTM + ≤hard_roll + 垫薄 → Roll 防守
    threatened_underwater = bool(
        underwater and not itm and dte is not None and dte <= hard_dte
        and (thin_otm or (buffer is not None and buffer < threat_buf))
    )
    needs_roll_near = bool(
        dte is not None
        and dte <= hard_dte
        and not profit_hit
        and (
            itm
            or (not underwater and (low_yield or thin_otm or not residual_worth_keeping))
            or threatened_underwater
        )
    )
    roll_21dte = needs_roll_near

    reasons: List[str] = []
    if underwater:
        reasons.append(f"浮亏 {profit_pct}% (买回 ask > 开仓权利金)")
        if remaining_ann is not None:
            reasons.append(
                f"剩余权利金折年化 {remaining_ann}% ≥风险未消(非健康收租)"
            )
    if profit_hit:
        reasons.append(f"浮盈 {profit_pct}% ≥ 硬止盈 {profit_target:g}%")
    elif soft_hit and low_yield:
        reasons.append(
            f"浮盈 {profit_pct}% ≥ 软止盈 {soft_profit:g}% 且剩余年化 {remaining_ann}% < 目标 {min_annualized}%"
        )
    if deep_itm:
        reasons.append(
            f"深ITM:Δ{delta:.2f}> {deep_itm_delta:g} 或价内{moneyness_pct:.1f}%> {deep_moneyness_pct:g}%"
        )
    elif shallow_itm:
        reasons.append(
            f"浅ITM:价内{moneyness_pct:.1f}%≤{shallow_itm_pct:g}% 且Δ{delta:.2f}≤{shallow_delta_max:g}"
        )
    elif itm:
        reasons.append(f"ITM Δ{delta:.2f}" if delta else "ITM")
    if thin_otm and buffer is not None:
        reasons.append(f"OTM垫 {buffer}% < 薄垫阈值 {thin_otm_pct:g}%")
    if early_assign:
        if div_window:
            reasons.append(
                f"CC除息窗≤{div_warn_days}天(剩{days_to_div}天)且Δ门槛触发,提前行权风险↑"
            )
        else:
            reasons.append(f"CC Δ≥{ea_deep:g} 深ITM,提前行权风险↑")
    if needs_roll_near:
        reasons.append(f"DTE {dte} ≤ 硬处理窗 {hard_dte} 且未硬止盈")
    if low_yield:
        reasons.append(f"剩余年化 {remaining_ann}% < 目标 {min_annualized}% → 担保低效")
    if hold_for_theta and fee_trap:
        reasons.append(
            f"买回名义 ${close_notional:.0f} < ${hold_meta['min_close_notional']:.0f}(手续费陷阱)"
        )
    elif hold_for_theta and residual_worth_keeping and remaining_ann is not None:
        reasons.append(
            f"吃θ:浮盈≥{hold_theta_min_profit:g}% 且剩余年化 {remaining_ann}%≥{rem_floor:g}% (DTE {dte}≤{hold_theta_max_dte}或临期)"
        )
    elif hold_for_theta:
        reasons.append(
            f"吃θ:临期(DTE≤{gamma_dte}) OTM 且浮盈≥{hold_theta_min_profit:g}%"
        )
    if profit_cap_close:
        reasons.append(f"浮盈 {profit_pct}% ≥ 过高持有 {max_hold_profit_pct:g}% 且 DTE>{gamma_dte} → 落袋")
    if capital_tight and (low_yield or soft_hit):
        util_txt = f"{capital_util_pct:.0f}%" if capital_util_pct is not None else f"≥{capital_tight_util:g}%"
        reasons.append(f"资金紧(利用率 {util_txt}),优先释放低效担保")

    # ── 决策树(量化序,命中即停) ──
    code: str = ACTION_NONE
    hint: Optional[str] = None
    priority = 9
    prefer_card: Optional[str] = None
    secondary_hint: Optional[str] = None
    branch = "none"

    # 1 风险:深 ITM
    if deep_itm:
        branch = "deep_itm"
        code, priority = ACTION_ROLL_ADJUST, 1
        prefer_card = "adjust_strike"
        hint = (
            f"深ITM(Δ>{deep_itm_delta:g}/价内>{deep_moneyness_pct:g}%):Roll调strike或评估接货"
            if side == "PUT"
            else f"深ITM(Δ>{deep_itm_delta:g}/价内>{deep_moneyness_pct:g}%):Roll调strike或评估交货"
        )
    # 2 风险:临期 ITM
    elif itm and expiring:
        branch = "prepare_assign"
        code, priority = ACTION_PREPARE_ASSIGN, 1
        prefer_card = "adjust_strike"
        hint = (
            f"临期ITM(DTE≤{gamma_dte}):Roll 或准备接货"
            if side == "PUT"
            else f"临期ITM(DTE≤{gamma_dte}):Roll 或准备被call"
        )
    # 3 风险:CC 提前行权
    elif early_assign:
        branch = "early_assign"
        code, priority = ACTION_ROLL_ADJUST, 2
        prefer_card = "adjust_strike"
        hint = "提前行权/除息风险:Roll或平仓"
    # 4 效率:吃 θ(压过机械50%止盈)
    elif hold_for_theta:
        branch = "hold_theta"
        code, priority = ACTION_HOLD_THETA, 5
        prefer_card = "no_roll"
        if fee_trap:
            hint = f"吃θ(买回<${hold_meta['min_close_notional']:.0f})"
        elif residual_worth_keeping:
            hint = f"吃θ(剩余年化≥{rem_floor:g}%)"
        else:
            hint = f"吃θ(临期高浮盈≥{hold_theta_min_profit:g}%)"
        if profit_hit:
            secondary_hint = (
                f"已达硬止盈{profit_target:g}%;资金紧可平,否则可续吃θ"
                if capital_tight
                else f"已达硬止盈{profit_target:g}%;需腾仓仍可买回"
            )
    # 5 效率:硬止盈 / 过高持有
    elif profit_hit or profit_cap_close:
        branch = "close_profit"
        code, priority = ACTION_CLOSE, 2
        prefer_card = "no_roll"
        if profit_cap_close and not profit_hit:
            hint = f"过高持有(≥{max_hold_profit_pct:g}%)落袋"
        elif side == "CALL":
            hint = f"止盈(≥{profit_target:g}%)结束Call义务"
        else:
            hint = f"止盈(≥{profit_target:g}%)释放担保"
        if thin_otm:
            secondary_hint = f"OTM垫<{thin_otm_pct:g}%,落袋后择机再开"
    # 6 效率:软止盈+低效
    elif soft_hit and low_yield:
        branch = "replace_soft"
        code, priority = ACTION_REPLACE, 3
        prefer_card = "no_roll"
        hint = f"软止盈≥{soft_profit:g}%+剩余年化<{min_annualized}% → 换仓"
    # 7 ≤硬处理窗
    elif needs_roll_near and itm:
        branch = "roll_itm_near"
        code, priority = ACTION_ROLL_ADJUST, 2
        prefer_card = "adjust_strike"
        hint = (
            f"ITM且DTE≤{hard_dte}:Roll out/down"
            if side == "PUT"
            else f"ITM且DTE≤{hard_dte}:Roll out/up"
        )
    elif needs_roll_near:
        branch = "roll_near"
        code, priority = ACTION_ROLL, 3
        prefer_card = "roll_out"
        bits = [f"DTE≤{hard_dte}"]
        if thin_otm:
            bits.append(f"垫<{thin_otm_pct:g}%")
        if low_yield:
            bits.append("年化低")
        if threatened_underwater:
            bits.append("浮亏威胁")
        hint = "Roll out(" + ",".join(bits) + ")"
    # 8 纯低效
    elif low_yield:
        branch = "replace_low_yield"
        code, priority = ACTION_REPLACE, 4
        prefer_card = "no_roll"
        hint = f"剩余年化{remaining_ann}%<{min_annualized}% → 换仓"
    # 9 超愿接 / 纪律硬否决 → 平
    elif underwater and not itm and strike_above_floor:
        branch = "close_above_floor"
        code, priority = ACTION_CLOSE, 3
        prefer_card = "no_roll"
        hint = f"strike>{floor_price:g}(愿接):不宜等接货,止损/Roll"
        secondary_hint = f"若续做:Roll到 floor≤{floor_price:g} 再开"
        reasons.append(f"愿接{floor_price:g} < strike{strike:g},指派不符预设")
    elif underwater and not itm and would_open == "no":
        # 量化:纪律不会新开 → 不沉没成本硬扛,主动 CLOSE
        branch = "close_discipline_no"
        code, priority = ACTION_CLOSE, 3
        prefer_card = "no_roll"
        hint = "纪律否决新开:优先买回/Roll,勿沉没成本硬扛"
        secondary_hint = (
            "趋势/规则已否决同类新开 — 持有=主动偏离"
            if side == "PUT"
            else "纪律否决下不宜继续裸露 Call 义务"
        )
        reasons.append("would_open=no:以今日纪律不会新开此腿 → 建议 CLOSE")
    # 10 浮亏 OTM(仍愿接区内)
    elif underwater and not itm:
        branch = "underwater_hold"
        code, priority = ACTION_NONE, 6
        prefer_card = "no_roll"
        if side == "PUT":
            hint = "浮亏OTM:确认愿按strike接货再拿"
            secondary_hint = "不愿接:买回或 Roll out/down"
        else:
            hint = "浮亏OTM:确认愿按strike交货再拿"
            secondary_hint = "不愿被call:买回或 Roll out/up"
        reasons.append("θ仍有利但浮亏;仅愿接/愿交时持有")
        if would_open == "caution":
            priority = min(priority, 5)
            reasons.append("would_open=caution:持有需额外确认")
    # 11 浅 ITM 观察
    elif shallow_itm and side == "PUT":
        branch = "shallow_itm_put"
        code, priority = ACTION_NONE, 6
        prefer_card = None
        hint = f"浅ITM(≤{shallow_itm_pct:g}%):观察,设接货预案"
        reasons.append(f"价内≤{shallow_itm_pct:g}% 且Δ≤{shallow_delta_max:g},不强Roll")
    elif shallow_itm and side == "CALL":
        branch = "shallow_itm_call"
        code, priority = ACTION_NONE, 6
        prefer_card = None
        hint = f"浅ITM(≤{shallow_itm_pct:g}%):观察,设交货预案"
        reasons.append(f"价内≤{shallow_itm_pct:g}% 且Δ≤{shallow_delta_max:g},不强Roll")
    # 12 健康 OTM 临近
    elif dte is not None and dte <= hard_dte and not itm and residual_worth_keeping and not underwater:
        branch = "healthy_near"
        code, priority = ACTION_NONE, 7
        prefer_card = "no_roll"
        hint = f"OTM健康(DTE≤{hard_dte},年化尚可):持有"
        reasons.append(
            f"DTE{dte}≤{hard_dte} 但OTM且剩余年化≥{rem_floor:g}%,不强Roll"
        )
    else:
        branch = "idle"

    # 资金紧:低效/换仓腿升权
    if capital_tight and code == ACTION_REPLACE:
        priority = max(2, priority - 1)
        hint = (hint or "换仓") + f"·资金紧(≥{capital_tight_util:g}%)"
    elif capital_tight and code == ACTION_CLOSE and not (
        profit_hit or profit_cap_close or strike_above_floor or would_open == "no"
    ):
        priority = max(2, priority - 1)
    elif capital_tight and code == ACTION_HOLD_THETA and profit_hit:
        priority = min(priority, 4)

    # 决策置信度 0–100:规则越硬、证据越足越高
    confidence = 50
    if code in (ACTION_ROLL_ADJUST, ACTION_PREPARE_ASSIGN) and (deep_itm or (itm and expiring)):
        confidence = 90
    elif code == ACTION_CLOSE and (
        profit_hit or profit_cap_close or strike_above_floor or branch == "close_discipline_no"
    ):
        confidence = 88 if branch == "close_discipline_no" else (
            85 if strike_above_floor or profit_cap_close else 80
        )
    elif code == ACTION_HOLD_THETA and residual_worth_keeping:
        confidence = 78
    elif code == ACTION_HOLD_THETA:
        confidence = 70
    elif code == ACTION_ROLL and threatened_underwater:
        confidence = 75
    elif code == ACTION_ROLL:
        confidence = 68
    elif code == ACTION_REPLACE:
        confidence = 78 if capital_tight else 72
    elif code == ACTION_NONE and underwater:
        confidence = 55
    elif code == ACTION_NONE:
        confidence = 60
    elif early_assign:
        confidence = 82
    if thin_otm and code in (ACTION_HOLD_THETA, ACTION_NONE):
        confidence = max(40, confidence - 15)
    if would_open == "yes" and underwater and code == ACTION_NONE:
        confidence = min(70, confidence + 5)

    # 接货/交货清单(ITM/临期/提前行权相关)
    share_cost = item.get("share_cost")
    cost_basis = item.get("cost_basis")
    try:
        share_cost = float(share_cost) if share_cost is not None else None
    except (TypeError, ValueError):
        share_cost = None
    try:
        cost_basis = float(cost_basis) if cost_basis is not None else None
    except (TypeError, ValueError):
        cost_basis = None
    equity = item.get("equity")
    try:
        equity = float(equity) if equity is not None else None
    except (TypeError, ValueError):
        equity = None
    symbol_max_capital = item.get("symbol_max_capital")
    try:
        symbol_max_capital = float(symbol_max_capital) if symbol_max_capital is not None else None
    except (TypeError, ValueError):
        symbol_max_capital = None
    symbol_committed = item.get("symbol_committed")
    try:
        symbol_committed = float(symbol_committed) if symbol_committed is not None else None
    except (TypeError, ValueError):
        symbol_committed = None

    assign_checklist = build_assign_checklist(
        side=side,
        strike=float(strike or 0),
        qty=qty,
        size=size,
        floor_price=floor_price,
        strike_above_floor=strike_above_floor,
        itm=itm,
        deep_itm=deep_itm,
        expiring=expiring,
        early_assign=early_assign,
        share_cost=share_cost,
        cost_basis=cost_basis,
        equity=equity,
        symbol_max_capital=symbol_max_capital,
        symbol_committed=symbol_committed,
    )
    # PREPARE / 深 ITM 时 reasons 挂一条清单摘要
    if assign_checklist and code in (ACTION_PREPARE_ASSIGN, ACTION_ROLL_ADJUST) and (itm or deep_itm):
        if side == "PUT":
            reasons.append(
                f"接货名义约 ${assign_checklist['assign_notional']:,.0f}"
                + ("(担保通常已覆盖)" if assign_checklist.get("collateral_covers") else "")
            )
            if assign_checklist.get("floor_ok") is False:
                reasons.append("floor 校验未通过:被指派不符合愿接价")
        else:
            reasons.append(f"交货名义约 ${assign_checklist['assign_notional']:,.0f}(按 strike 卖股)")

    quant_used = {
        "profit_target_pct": profit_target,
        "soft_profit_pct": soft_profit,
        "max_hold_profit_pct": max_hold_profit_pct,
        "hold_theta_min_profit_pct": hold_theta_min_profit,
        "hold_theta_max_dte": hold_theta_max_dte,
        "hold_theta_min_remaining_ann": rem_floor,
        "hard_roll_dte": hard_dte,
        "gamma_warn_dte": gamma_dte,
        "shallow_itm_pct": shallow_itm_pct,
        "deep_itm_moneyness_pct": deep_moneyness_pct,
        "deep_itm_delta": deep_itm_delta,
        "thin_otm_buffer_pct": thin_otm_pct,
        "threat_otm_buffer_pct": threat_buf,
        "min_close_notional": hold_meta["min_close_notional"],
        "capital_tight_util_pct": capital_tight_util,
        "min_annualized": min_annualized,
    }

    tree = {
        "branch": branch,
        "profit_hit": profit_hit,
        "soft_profit_hit": soft_hit,
        "hold_for_theta": hold_for_theta,
        "fee_trap": fee_trap,
        "residual_worth_keeping": residual_worth_keeping,
        "underwater": underwater,
        "threatened_underwater": threatened_underwater,
        "strike_above_floor": strike_above_floor,
        "thin_otm": thin_otm,
        "profit_cap_close": profit_cap_close,
        "needs_roll_near": needs_roll_near,
        "shallow_itm": shallow_itm,
        "soft_profit_pct": soft_profit,
        "hard_roll_dte": hard_dte,
        "hold_theta_max_dte": hold_theta_max_dte,
        "hold_theta_min_remaining_ann": rem_floor,
        "min_close_notional": hold_meta["min_close_notional"],
        "close_notional": round(close_notional, 2),
        "close_px": close_px or None,
        "otm_buffer_pct": buffer,
        "floor_price": floor_price,
        "capital_tight": capital_tight,
        "capital_util_pct": capital_util_pct,
        "portfolio_put_blocked": portfolio_put_blocked,
        "symbol_headroom": symbol_headroom,
        "capital_tight_util_pct": capital_tight_util,
        "would_open_today": would_open,
        "trend": trend,
        "quant": quant_used,
    }

    return {
        "remaining_annualized": remaining_ann,
        "low_yield": low_yield,
        "roll_21dte": roll_21dte,
        "deep_itm": deep_itm,
        "shallow_itm": shallow_itm,
        "early_assign_risk": early_assign,
        "thin_otm": thin_otm,
        "otm_buffer_pct": buffer,
        "strike_above_floor": strike_above_floor,
        "capital_tight": capital_tight,
        "capital_util_pct": capital_util_pct,
        "portfolio_put_blocked": portfolio_put_blocked,
        "symbol_headroom": symbol_headroom,
        "would_open_today": would_open,
        "would_open_reasons": would_open_reasons,
        "assign_checklist": assign_checklist,
        "action_code": code,
        "action_hint": hint,
        "secondary_hint": secondary_hint,
        "action_priority": priority,
        "decision_confidence": confidence,
        "prefer_card": prefer_card,
        "decision_branch": branch,
        "quant_thresholds": quant_used,
        "reasons": reasons,
        "decision_tree": tree,
        "moneyness_pct": round(moneyness_pct, 2) if moneyness_pct else 0.0,
    }


def format_alert_line(item: Dict[str, Any]) -> str:
    """单条 Telegram 告警文案(短模板,委托 alert_engine)。"""
    try:
        from app.services.alert_engine import format_position_alert
        return format_position_alert(item, style="short")
    except Exception:
        hint = item.get("action_hint") or "关注"
        side = item.get("side") or ""
        sym = item.get("symbol") or ""
        dte = item.get("dte")
        profit = item.get("profit_pct")
        code = item.get("action_code") or ""
        parts = [f"⚠ {sym} {side}", hint]
        if code and code != ACTION_NONE:
            parts.append(code)
        if dte is not None:
            parts.append(f"DTE{dte}")
        if profit is not None:
            parts.append(f"浮盈{profit}%")
        if item.get("itm"):
            parts.append("ITM")
        return " · ".join(parts)
