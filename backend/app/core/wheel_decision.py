"""Wheel 持仓动态决策树

在 50% 止盈 / 21DTE 基础上,按浮盈、DTE、剩余年化、ITM 深度、
平仓真实成本(买回 ask)、手续费门槛、CSP/CC 分叉给出:

  action_code + action_hint + action_priority + prefer_card + reasons

供体检 API、今日管理卡、Telegram 告警、Roll 场景共用。
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


def _cfg_num(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def remaining_annualized(close_px: float, strike: float, dte: Optional[int]) -> Optional[float]:
    if not strike or not dte or dte <= 0 or close_px <= 0:
        return None
    return round(close_px / strike * 365 / dte * 100, 2)


def residual_floor(min_annualized: float, pos_cfg: Optional[Dict[str, Any]] = None) -> float:
    """剩余年化「仍值得拿」的下限。"""
    cfg = pos_cfg or {}
    floor = _cfg_num(cfg, "hold_theta_min_remaining_ann", 12.0)
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
    cfg = pos_cfg or {}
    hold_theta_min_profit = _cfg_num(cfg, "hold_theta_min_profit_pct", 40)
    hold_theta_max_dte = int(_cfg_num(cfg, "hold_theta_max_dte", 14))
    gamma_dte = int(_cfg_num(cfg, "gamma_warn_dte", 7))
    min_close_notional = _cfg_num(cfg, "min_close_notional", 20.0)
    rem_floor = residual_floor(min_annualized, cfg)

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
) -> Dict[str, Any]:
    """反事实:以今天纪律还会不会新开这张腿。

    返回 would_open_today: yes|no|caution|unknown + reasons。
    轻量规则,不跑全量 admission。
    """
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
            reasons.append("未设置接货底线,无法完整校验开仓纪律")
        elif strike_above_floor or strike > floor_price:
            hard_no = True
            reasons.append(f"strike {strike:g} > 接货底线 {floor_price:g},纪律不会新开此 Put")
        if trend == "DOWN":
            hard_no = True
            reasons.append("趋势 DOWN,按纪律不新开 Put")
        elif trend == "WEAK":
            caution = True
            reasons.append("趋势 WEAK,新开 Put 需谨慎")
        if capital_tight:
            caution = True
            reasons.append("组合资金偏紧,不宜再占担保开新 Put")
        if thin_otm or (buffer is not None and buffer < 2.0 and not itm):
            caution = True
            reasons.append("安全垫偏薄,新开同类 Put 风险高")
        if itm or deep_itm:
            caution = True
            reasons.append("已 ITM,更不应以现价「新开」同结构")
    elif side == "CALL":
        if trend == "DOWN":
            # 持股 CC 在下跌趋势仍可卖,但需谨慎
            caution = True
            reasons.append("趋势 DOWN,CC 需确认仍愿持股收租")
        if deep_itm or (itm and dte is not None and dte <= 7):
            caution = True
            reasons.append("Call 已深/临期 ITM,不会以当前结构新开")
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
        # 关键缺失且无硬否决 → unknown
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
                notes.append(f"strike {strike:g} 高于接货底线 {floor_price:g},被指派不符合预设愿接价")
        else:
            notes.append("未设接货底线,请自行确认愿接价")
        notes.append("CSP 现金担保通常已覆盖行权名义:指派多为担保变正股,一般不必再掏同等现金")
        next_step = "接货后可按成本基础/现价扫描 Covered Call,继续轮动"
        collateral_covers = True
    else:
        floor_ok = None
        collateral_covers = None  # CC 不占 CSP 担保
        next_step = "被 call 后轮子可结束,或现金到位后重开 CSP"
        notes.append("被 call 走 = 按 strike 卖出持股,请确认愿意在此价交货")
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
    soft_profit = float(cfg.get("soft_profit_pct", max(profit_target * 0.6, 30)))
    hard_dte = int(cfg.get("hard_roll_dte", 21))
    gamma_dte = int(cfg.get("gamma_warn_dte", 7))
    hold_theta_min_profit = float(cfg.get("hold_theta_min_profit_pct", 40))
    hold_theta_max_dte = int(cfg.get("hold_theta_max_dte", 14))
    shallow_itm_pct = float(cfg.get("shallow_itm_pct", 1.5))
    deep_moneyness_pct = float(cfg.get("deep_itm_moneyness_pct", 3.0))
    # OTM 安全垫过薄(默认 1.5%)时,不鼓励硬吃 θ/硬 roll 到无视风险
    thin_otm_pct = float(cfg.get("thin_otm_buffer_pct", 1.5))
    # 浮盈已很高(默认 80%)且 DTE 仍不短 → 倾向落袋,避免「永远拿着」
    max_hold_profit_pct = float(cfg.get("max_hold_profit_pct", 80.0))
    capital_tight_util = float(cfg.get("capital_tight_util_pct", 75.0))

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
    # 浮亏(买回价 > 开仓权利金):「剩余年化高」=市场仍定价风险,不是「健康收租」
    underwater = profit_pct is not None and profit_pct < 0
    side = item.get("side")
    floor_price = item.get("floor_price")
    try:
        floor_price = float(floor_price) if floor_price is not None else None
    except (TypeError, ValueError):
        floor_price = None
    # CSP: 行权价高于接货底线 → 被指派也不符合个人风控
    strike_above_floor = bool(
        side == "PUT" and floor_price is not None and floor_price > 0 and strike > floor_price
    )
    expiring = bool(item.get("expiring")) or (dte is not None and dte <= gamma_dte)
    qty = float(item.get("qty") or 1)
    size = float(item.get("contract_size") or 100)
    close_notional = close_px * size * qty if close_px > 0 else 0.0
    days_to_div = item.get("days_to_ex_div")
    div_window = days_to_div is not None and int(days_to_div) >= 0 and int(days_to_div) <= int(
        cfg.get("dividend_warn_days", 14)
    )

    # 组合资本上下文(可选;缺省不感知)
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
    deep_itm = bool(itm and (delta > 0.5 or moneyness_pct > deep_moneyness_pct))
    shallow_itm = bool(
        itm and not deep_itm
        and (moneyness_pct <= shallow_itm_pct and delta <= 0.55)
    )
    early_assign = bool(
        side == "CALL"
        and (
            (itm and (delta >= 0.8 or (div_window and delta >= 0.55)))
            # 浅 ITM + 除息窗口:同样有提前行权风险
            or (itm and shallow_itm and div_window and delta >= 0.45)
            or (not itm and div_window and delta >= 0.70)
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

    # 浮盈已极高且 DTE 仍明显长于临期窗口 → 落袋优先于继续拿 θ
    profit_cap_close = bool(
        profit_pct is not None
        and profit_pct >= max_hold_profit_pct
        and dte is not None
        and dte > gamma_dte
        and not fee_trap
    )
    if profit_cap_close:
        hold_for_theta = False

    # 薄 OTM:安全垫不足,高浮盈也不鼓励「死拿」到被扫到
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
    )
    would_open = would_meta["would_open_today"]
    would_open_reasons = list(would_meta["would_open_reasons"] or [])

    # 浮亏 OTM 且安全垫偏薄/临近 → 倾向 Roll 防守(不是「健康持有」)
    threatened_underwater = bool(
        underwater and not itm and dte is not None and dte <= hard_dte
        and (thin_otm or (buffer is not None and buffer < 5.0))
    )
    # 21DTE:浮盈仓 — ITM/低效/薄垫;浮亏仓 — 受威胁才 roll
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
        reasons.append(f"当前浮亏 {profit_pct}% (买回价高于开仓权利金)")
        if remaining_ann is not None:
            reasons.append(
                f"剩余权利金仍高(折年化 {remaining_ann}%)=风险未消,不是健康收租信号"
            )
    if profit_hit:
        reasons.append(f"浮盈 {profit_pct}% ≥ 止盈目标 {profit_target}%")
    elif soft_hit and low_yield:
        reasons.append(f"浮盈 {profit_pct}% 达软止盈且剩余年化低")
    if deep_itm:
        reasons.append(
            f"深度价内(Δ{delta:.2f}"
            + (f",价内 {moneyness_pct:.1f}%" if moneyness_pct > 0 else "")
            + ")"
        )
    elif shallow_itm:
        reasons.append(f"浅 ITM(Δ{delta:.2f},价内 {moneyness_pct:.1f}%)")
    elif itm:
        reasons.append(f"已 ITM(Δ{delta:.2f})" if delta else "已 ITM")
    if thin_otm and buffer is not None:
        reasons.append(f"OTM 安全垫仅 {buffer}%,接近行权价")
    if early_assign:
        if div_window:
            reasons.append(f"CC 临近除息({days_to_div}天)且 ITM/高Δ,提前行权风险升高")
        else:
            reasons.append("CC 深度价内,存在提前被行权风险(留意除息日)")
    if needs_roll_near:
        reasons.append(f"DTE {dte} ≤ {hard_dte} 且未达止盈,建议处理 gamma/效率")
    if low_yield:
        reasons.append(f"剩余年化 {remaining_ann}% < 目标 {min_annualized}%,担保金低效")
    if hold_for_theta and fee_trap:
        reasons.append(f"买回名义仅 ${close_notional:.0f} < ${hold_meta['min_close_notional']:.0f},手续费/点差不划算")
    elif hold_for_theta and residual_worth_keeping and remaining_ann is not None:
        reasons.append(
            f"OTM 浮盈≥{hold_theta_min_profit}% 且剩余年化 {remaining_ann}% 仍体面,优先吃 θ(DTE {dte})"
        )
    elif hold_for_theta:
        reasons.append(f"临期 OTM 且浮盈≥{hold_theta_min_profit}%,可持有吃 theta")
    if profit_cap_close:
        reasons.append(f"浮盈 {profit_pct}% 已很高(≥{max_hold_profit_pct}%),倾向落袋")
    if capital_tight and (low_yield or soft_hit):
        util_txt = f"{capital_util_pct:.0f}%" if capital_util_pct is not None else "偏高"
        reasons.append(f"组合资金占用偏紧(利用率 {util_txt}),宜优先释放低效担保金")

    # ── 决策优先级(数字越小越紧急) ──
    code: str = ACTION_NONE
    hint: Optional[str] = None
    priority = 9
    prefer_card: Optional[str] = None
    secondary_hint: Optional[str] = None

    if deep_itm:
        code, priority = ACTION_ROLL_ADJUST, 1
        prefer_card = "adjust_strike"
        if side == "PUT":
            hint = "尽快 Roll down/out(深 ITM Put)或评估接货"
        else:
            hint = "尽快 Roll up/out(深 ITM Call)或评估交货"
    elif itm and expiring:
        code, priority = ACTION_PREPARE_ASSIGN, 1
        prefer_card = "adjust_strike"
        if side == "PUT":
            hint = "临期 ITM Put:Roll down 或准备接货"
        else:
            hint = "临期 ITM Call:Roll up 或准备被叫走"
    elif early_assign:
        code, priority = ACTION_ROLL_ADJUST, 2
        prefer_card = "adjust_strike"
        hint = "留意提前行权/除息,考虑 Roll 或平仓"
    elif hold_for_theta:
        code, priority = ACTION_HOLD_THETA, 5
        prefer_card = "no_roll"
        if fee_trap:
            hint = "持有吃 theta(买回成本过低)"
        elif residual_worth_keeping:
            hint = "持有吃 theta(剩余年化仍高)"
        else:
            hint = "持有吃 theta(临期高浮盈)"
        if profit_hit:
            secondary_hint = (
                "资金紧时可主动止盈腾仓;否则可继续吃 θ"
                if capital_tight
                else "若需腾出仓位/换股,仍可止盈买回"
            )
    elif profit_hit or profit_cap_close:
        code, priority = ACTION_CLOSE, 2
        prefer_card = "no_roll"
        if side == "CALL":
            hint = "止盈平仓(结束 Call 义务,保留持股)"
        else:
            hint = "止盈平仓(释放担保金)"
        if thin_otm:
            secondary_hint = "OTM 安全垫薄,落袋后可再择机开仓"
    elif soft_hit and low_yield:
        code, priority = ACTION_REPLACE, 3
        prefer_card = "no_roll"
        if side == "CALL":
            hint = "软止盈+换仓(结束低效 CC,便于再卖)"
        else:
            hint = "软止盈+换仓(释放担保金)"
    elif needs_roll_near and itm:
        code, priority = ACTION_ROLL_ADJUST, 2
        prefer_card = "adjust_strike"
        if side == "PUT":
            hint = f"ITM Put 且≤{hard_dte}DTE:优先 Roll out/down"
        else:
            hint = f"ITM Call 且≤{hard_dte}DTE:优先 Roll out/up"
    elif needs_roll_near:
        code, priority = ACTION_ROLL, 3
        prefer_card = "roll_out"
        hint = f"考虑 Roll out(≤{hard_dte}DTE"
        if thin_otm:
            hint += ",安全垫薄"
        if low_yield:
            hint += ",剩余年化低"
        hint += ")"
    elif low_yield:
        code, priority = ACTION_REPLACE, 4
        prefer_card = "no_roll"
        hint = "平仓换仓(剩余年化低)"
    elif underwater and not itm and strike_above_floor:
        # 浮亏且 strike > 接货底线:死拿等于接受不愿接的货
        code, priority = ACTION_CLOSE, 3
        prefer_card = "no_roll"
        hint = f"浮亏且 strike>${floor_price:g}>底线:优先止损/Roll,不宜等接货"
        secondary_hint = "若仍想卖权:Roll down 到 floor 以下再开"
        reasons.append(
            f"接货底线 {floor_price:g},当前 strike {strike:g} 更高,被指派不符合预设风控"
        )
    elif underwater and not itm:
        # 浮亏但仍 OTM 且 strike 在底线内:持有=赌到期作废;绝非「健康收租」
        code, priority = ACTION_NONE, 6
        prefer_card = "no_roll"
        if side == "PUT":
            hint = "浮亏持有(仍 OTM):确认愿按 strike 接货再拿"
            secondary_hint = "若不愿接货或观点改变:止损买回或 Roll out/down"
        else:
            hint = "浮亏持有(仍 OTM):确认愿按 strike 交货再拿"
            secondary_hint = "若不愿被 call 走:买回或 Roll out/up"
        reasons.append(
            "theta 仍对卖方有利,但浮亏未恢复;仅当标的逻辑与行权价仍可接受时持有"
        )
        if would_open == "no":
            # 保持 NONE 不强制 CLOSE(strike>floor 已单独 CLOSE);升权+强调纪律否决
            priority = 4
            secondary_hint = (
                "纪律否决新开此腿:优先止损买回或 Roll,勿用沉没成本自我安慰"
            )
            reasons.append("以当前纪律不会新开此腿 — 继续持有=主动偏离策略")
        elif would_open == "caution":
            priority = min(priority, 5)
            reasons.append("纪律上对新开偏谨慎,持有需额外确认逻辑")
    elif shallow_itm and side == "PUT":
        code, priority = ACTION_NONE, 6
        prefer_card = None
        hint = "浅 ITM Put:观察,未深价内不必强 Roll"
        reasons.append("浅 ITM 可继续收 θ,设好接货预案即可")
    elif shallow_itm and side == "CALL":
        code, priority = ACTION_NONE, 6
        prefer_card = None
        hint = "浅 ITM Call:观察,留意是否继续上穿"
        reasons.append("浅 ITM CC 可继续收 θ,设好被 call 预案")
    elif dte is not None and dte <= hard_dte and not itm and residual_worth_keeping and not underwater:
        # 浮盈 + 健康 OTM:临近但不强推 roll
        code, priority = ACTION_NONE, 7
        prefer_card = "no_roll"
        hint = "OTM 健康持有,临期再评估"
        reasons.append(f"DTE {dte}≤{hard_dte} 但 OTM 且剩余年化尚可,无需强行 Roll")

    # 资金紧:低效/换仓腿升权;不改变高优先级风险动作
    if capital_tight and code == ACTION_REPLACE:
        priority = max(2, priority - 1)
        if side == "PUT":
            hint = (hint or "换仓") + "·资金紧"
        else:
            hint = (hint or "换仓") + "·资金紧"
    elif capital_tight and code == ACTION_CLOSE and not (profit_hit or profit_cap_close or strike_above_floor):
        priority = max(2, priority - 1)
    elif capital_tight and code == ACTION_HOLD_THETA and profit_hit:
        # 仍吃 θ,但排序略提前以便看见「可腾仓」
        priority = min(priority, 4)

    # 决策置信度 0–100:规则越硬、证据越足越高
    confidence = 50
    if code in (ACTION_ROLL_ADJUST, ACTION_PREPARE_ASSIGN) and (deep_itm or (itm and expiring)):
        confidence = 90
    elif code == ACTION_CLOSE and (profit_hit or profit_cap_close or strike_above_floor):
        confidence = 85 if strike_above_floor or profit_cap_close else 80
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
        confidence = 55  # 条件持有,依赖用户接货意愿
    elif code == ACTION_NONE:
        confidence = 60
    elif early_assign:
        confidence = 82
    if thin_otm and code in (ACTION_HOLD_THETA, ACTION_NONE):
        confidence = max(40, confidence - 15)
    # 纪律否决仅下调「仍建议持有」类置信;已 CLOSE/ROLL 与纪律一致则不降
    if would_open == "no" and code in (ACTION_NONE, ACTION_HOLD_THETA) and underwater:
        confidence = max(35, confidence - 15)
    elif would_open == "yes" and underwater and code == ACTION_NONE:
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

    tree = {
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
        "reasons": reasons,
        "decision_tree": tree,
        "moneyness_pct": round(moneyness_pct, 2) if moneyness_pct else 0.0,
    }


def format_alert_line(item: Dict[str, Any]) -> str:
    """单条 Telegram 告警文案。"""
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
