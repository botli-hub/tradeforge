"""Wheel 持仓动态决策树

在固定 50% 止盈 / 21DTE 基础上,按浮盈、DTE、剩余年化、ITM 深度给出
主建议 + 优先级,供体检 API 与 Telegram 告警共用。
"""
from typing import Any, Dict, List, Optional


def decide_position(
    item: Dict[str, Any],
    min_annualized: float,
    profit_target: float,
    pos_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """输入 item: side/strike/spot/dte/current_price/profit_pct/itm/delta/expiring/theta。

    返回增强字段: remaining_annualized, low_yield, roll_21dte, deep_itm,
    early_assign_risk, action_hint, action_priority, reasons, decision_tree。
    """
    cfg = pos_cfg or {}
    soft_profit = float(cfg.get("soft_profit_pct", max(profit_target * 0.6, 30)))
    hard_dte = int(cfg.get("hard_roll_dte", 21))
    gamma_dte = int(cfg.get("gamma_warn_dte", 7))
    hold_theta_min_profit = float(cfg.get("hold_theta_min_profit_pct", 40))

    strike = item.get("strike") or 0
    spot = item.get("spot") or 0
    dte = item.get("dte")
    cur = item.get("current_price") or 0
    delta = abs(item.get("delta") or 0)
    itm = bool(item.get("itm"))
    profit_pct = item.get("profit_pct")
    profit_hit = profit_pct is not None and profit_pct >= profit_target
    soft_hit = profit_pct is not None and profit_pct >= soft_profit
    side = item.get("side")
    expiring = bool(item.get("expiring")) or (dte is not None and dte <= gamma_dte)

    remaining_ann = None
    if strike > 0 and dte and dte > 0 and cur > 0:
        remaining_ann = round(cur / strike * 365 / dte * 100, 2)

    low_yield = bool(
        not itm and remaining_ann is not None
        and min_annualized > 0 and remaining_ann < min_annualized
    )
    roll_21dte = bool(dte is not None and dte <= hard_dte and not profit_hit)

    moneyness = 0.0
    if spot > 0 and strike > 0:
        moneyness = (strike - spot) / spot if side == "PUT" else (spot - strike) / spot
    deep_itm = bool(itm and (delta > 0.5 or moneyness > 0.03))
    early_assign = bool(side == "CALL" and itm and delta >= 0.8)

    # 临期高浮盈:吃完 theta 比付手续费划算
    hold_for_theta = bool(
        profit_pct is not None
        and profit_pct >= hold_theta_min_profit
        and dte is not None
        and dte <= gamma_dte
        and not itm
        and not deep_itm
    )

    reasons: List[str] = []
    if profit_hit:
        reasons.append(f"浮盈 {profit_pct}% ≥ 止盈目标 {profit_target}%")
    elif soft_hit and low_yield:
        reasons.append(f"浮盈 {profit_pct}% 达软止盈且剩余年化低")
    if deep_itm:
        reasons.append(
            f"深度价内(Δ{delta:.2f}"
            + (f",价内 {moneyness*100:.1f}%" if moneyness > 0 else "")
            + ")"
        )
    elif itm:
        reasons.append(f"已 ITM(Δ{delta:.2f})" if delta else "已 ITM")
    if early_assign:
        reasons.append("CC 深度价内,存在提前被行权风险(留意除息日)")
    if roll_21dte:
        reasons.append(f"DTE {dte} ≤ {hard_dte} 且未达止盈,gamma 风险上升")
    if low_yield:
        reasons.append(f"剩余年化 {remaining_ann}% < 目标 {min_annualized}%,担保金低效")
    if hold_for_theta:
        reasons.append(f"临期且浮盈≥{hold_theta_min_profit}%,可持有吃 theta")

    # 决策优先级(数字越小越紧急)
    if deep_itm:
        hint, priority = "尽快 Roll(深度价内)", 1
    elif itm and expiring:
        hint, priority = "临期 ITM:Roll 或准备接货/交货", 1
    elif early_assign:
        hint, priority = "留意提前行权/除息,考虑 Roll 或平仓", 2
    elif hold_for_theta:
        hint, priority = "持有吃 theta(临期高浮盈)", 5
    elif profit_hit:
        hint, priority = "止盈平仓", 2
    elif soft_hit and low_yield:
        hint, priority = "软止盈+换仓(释放担保金)", 3
    elif roll_21dte and itm:
        hint, priority = "ITM 且≤21DTE:优先 Roll out/down", 2
    elif roll_21dte:
        hint, priority = f"考虑 Roll(≤{hard_dte}DTE)", 3
    elif low_yield:
        hint, priority = "平仓换仓(剩余年化低)", 4
    else:
        hint, priority = None, 9

    tree = {
        "profit_hit": profit_hit,
        "soft_profit_hit": soft_hit,
        "hold_for_theta": hold_for_theta,
        "soft_profit_pct": soft_profit,
        "hard_roll_dte": hard_dte,
    }

    return {
        "remaining_annualized": remaining_ann,
        "low_yield": low_yield,
        "roll_21dte": roll_21dte,
        "deep_itm": deep_itm,
        "early_assign_risk": early_assign,
        "action_hint": hint,
        "action_priority": priority,
        "reasons": reasons,
        "decision_tree": tree,
        "moneyness_pct": round(moneyness * 100, 2) if moneyness else 0.0,
    }


def format_alert_line(item: Dict[str, Any]) -> str:
    """单条 Telegram 告警文案。"""
    hint = item.get("action_hint") or "关注"
    side = item.get("side") or ""
    sym = item.get("symbol") or ""
    dte = item.get("dte")
    profit = item.get("profit_pct")
    parts = [f"⚠ {sym} {side}", hint]
    if dte is not None:
        parts.append(f"DTE{dte}")
    if profit is not None:
        parts.append(f"浮盈{profit}%")
    if item.get("itm"):
        parts.append("ITM")
    return " · ".join(parts)
