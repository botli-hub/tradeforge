"""Wheel 决策树:量化阈值、清单与工具函数. decide_position 见 wheel_decision_tree."""
from typing import Any, Dict, List, Optional, Tuple

# action_code 枚举(前端建卡/按钮用,少做字符串匹配)
ACTION_CLOSE = "CLOSE"
ACTION_ROLL = "ROLL"
ACTION_ROLL_ADJUST = "ROLL_ADJUST"
ACTION_HOLD_THETA = "HOLD_THETA"
ACTION_REPLACE = "REPLACE"
ACTION_PREPARE_ASSIGN = "PREPARE_ASSIGN"
ACTION_NONE = "NONE"

# ── 量化默认(设置页 wheel_position 可覆盖) ─────────────────────────────────
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


