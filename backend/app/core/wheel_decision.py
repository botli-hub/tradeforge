"""Wheel 持仓动态决策树

在固定 50% 止盈 / 21DTE 基础上,按浮盈、DTE、剩余年化、ITM 深度、
平仓真实成本(买回 ask)、手续费门槛、CSP/CC 分叉给出:

  action_code + action_hint + action_priority + prefer_card + reasons

供体检 API、今日管理卡、Telegram 告警共用。
"""
from typing import Any, Dict, List, Optional

# action_code 枚举(前端建卡/按钮用,少做字符串匹配)
ACTION_CLOSE = "CLOSE"
ACTION_ROLL = "ROLL"
ACTION_ROLL_ADJUST = "ROLL_ADJUST"
ACTION_HOLD_THETA = "HOLD_THETA"
ACTION_REPLACE = "REPLACE"
ACTION_PREPARE_ASSIGN = "PREPARE_ASSIGN"
ACTION_NONE = "NONE"


def decide_position(
    item: Dict[str, Any],
    min_annualized: float,
    profit_target: float,
    pos_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """输入 item 建议含:
      side/strike/spot/dte/current_price/buyback_ask/profit_pct/itm/delta/expiring
      qty(张,默认1)/contract_size(默认100)/days_to_ex_div(除息剩几天,可选)

    返回增强字段见末尾 dict。
    """
    cfg = pos_cfg or {}
    soft_profit = float(cfg.get("soft_profit_pct", max(profit_target * 0.6, 30)))
    hard_dte = int(cfg.get("hard_roll_dte", 21))
    gamma_dte = int(cfg.get("gamma_warn_dte", 7))
    hold_theta_min_profit = float(cfg.get("hold_theta_min_profit_pct", 40))
    # 平仓名义金额低于此(美元)且高浮盈 OTM → 倾向吃 θ,避免手续费/点差吞收益
    min_close_notional = float(cfg.get("min_close_notional", 20.0))
    shallow_itm_pct = float(cfg.get("shallow_itm_pct", 1.5))  # 价内%低于此且 Δ≤0.55 视为浅 ITM
    deep_moneyness_pct = float(cfg.get("deep_itm_moneyness_pct", 3.0))

    strike = item.get("strike") or 0
    spot = item.get("spot") or 0
    dte = item.get("dte")
    # 卖方平仓成本:优先买回 ask
    buyback = item.get("buyback_ask")
    last = item.get("current_price") or 0
    close_px = float(buyback) if buyback is not None and float(buyback) > 0 else float(last or 0)
    delta = abs(item.get("delta") or 0)
    itm = bool(item.get("itm"))
    profit_pct = item.get("profit_pct")
    profit_hit = profit_pct is not None and profit_pct >= profit_target
    soft_hit = profit_pct is not None and profit_pct >= soft_profit
    side = item.get("side")
    expiring = bool(item.get("expiring")) or (dte is not None and dte <= gamma_dte)
    qty = float(item.get("qty") or 1)
    size = float(item.get("contract_size") or 100)
    close_notional = close_px * size * qty if close_px > 0 else 0.0
    days_to_div = item.get("days_to_ex_div")
    div_window = days_to_div is not None and int(days_to_div) >= 0 and int(days_to_div) <= int(
        cfg.get("dividend_warn_days", 14)
    )

    # 剩余年化:用真实买回成本(卖方视角)
    remaining_ann = None
    if strike > 0 and dte and dte > 0 and close_px > 0:
        remaining_ann = round(close_px / strike * 365 / dte * 100, 2)

    low_yield = bool(
        not itm and remaining_ann is not None
        and min_annualized > 0 and remaining_ann < min_annualized
    )
    roll_21dte = bool(dte is not None and dte <= hard_dte and not profit_hit)

    moneyness = 0.0
    if spot > 0 and strike > 0:
        moneyness = (strike - spot) / spot if side == "PUT" else (spot - strike) / spot
    moneyness_pct = moneyness * 100
    deep_itm = bool(itm and (delta > 0.5 or moneyness_pct > deep_moneyness_pct))
    shallow_itm = bool(
        itm and not deep_itm
        and (moneyness_pct <= shallow_itm_pct and delta <= 0.55)
    )
    early_assign = bool(side == "CALL" and itm and (delta >= 0.8 or (div_window and delta >= 0.55)))

    # 手续费/点差保护:名义太小且高浮盈 OTM → 吃 θ 优于止盈买回
    fee_trap = bool(
        close_notional > 0
        and close_notional < min_close_notional
        and not itm
        and profit_pct is not None
        and profit_pct >= hold_theta_min_profit
    )

    # 临期高浮盈:吃完 theta 比付手续费划算
    hold_for_theta = bool(
        (
            profit_pct is not None
            and profit_pct >= hold_theta_min_profit
            and dte is not None
            and dte <= gamma_dte
            and not itm
            and not deep_itm
        )
        or fee_trap
    )

    reasons: List[str] = []
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
    if early_assign:
        if div_window:
            reasons.append(f"CC 临近除息({days_to_div}天)且 ITM/高Δ,提前行权风险升高")
        else:
            reasons.append("CC 深度价内,存在提前被行权风险(留意除息日)")
    if roll_21dte:
        reasons.append(f"DTE {dte} ≤ {hard_dte} 且未达止盈,gamma 风险上升")
    if low_yield:
        reasons.append(f"剩余年化 {remaining_ann}% < 目标 {min_annualized}%,担保金低效")
    if hold_for_theta and fee_trap:
        reasons.append(f"买回名义仅 ${close_notional:.0f} < ${min_close_notional:.0f},手续费/点差不划算")
    elif hold_for_theta:
        reasons.append(f"临期且浮盈≥{hold_theta_min_profit}%,可持有吃 theta")

    # ── 决策优先级(数字越小越紧急) + action_code / prefer_card ──
    code: str = ACTION_NONE
    hint: Optional[str] = None
    priority = 9
    prefer_card: Optional[str] = None

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
        hint = "持有吃 theta(临期高浮盈)" if not fee_trap else "持有吃 theta(买回成本过低)"
    elif profit_hit:
        code, priority = ACTION_CLOSE, 2
        prefer_card = "no_roll"
        hint = "止盈平仓"
    elif soft_hit and low_yield:
        code, priority = ACTION_REPLACE, 3
        prefer_card = "no_roll"
        hint = "软止盈+换仓(释放担保金)"
    elif roll_21dte and itm:
        code, priority = ACTION_ROLL_ADJUST, 2
        prefer_card = "adjust_strike"
        if side == "PUT":
            hint = f"ITM Put 且≤{hard_dte}DTE:优先 Roll out/down"
        else:
            hint = f"ITM Call 且≤{hard_dte}DTE:优先 Roll out/up"
    elif roll_21dte:
        code, priority = ACTION_ROLL, 3
        prefer_card = "roll_out"
        hint = f"考虑 Roll out(≤{hard_dte}DTE)"
    elif low_yield:
        code, priority = ACTION_REPLACE, 4
        prefer_card = "no_roll"
        hint = "平仓换仓(剩余年化低)"
    elif shallow_itm and side == "PUT":
        # 浅 ITM Put:观察,不必立刻恐慌
        code, priority = ACTION_NONE, 6
        prefer_card = None
        hint = "浅 ITM Put:观察,未深价内不必强 Roll"
        reasons.append("浅 ITM 可继续收 θ,设好接货预案即可")

    tree = {
        "profit_hit": profit_hit,
        "soft_profit_hit": soft_hit,
        "hold_for_theta": hold_for_theta,
        "fee_trap": fee_trap,
        "shallow_itm": shallow_itm,
        "soft_profit_pct": soft_profit,
        "hard_roll_dte": hard_dte,
        "min_close_notional": min_close_notional,
        "close_notional": round(close_notional, 2),
        "close_px": close_px or None,
    }

    return {
        "remaining_annualized": remaining_ann,
        "low_yield": low_yield,
        "roll_21dte": roll_21dte,
        "deep_itm": deep_itm,
        "shallow_itm": shallow_itm,
        "early_assign_risk": early_assign,
        "action_code": code,
        "action_hint": hint,
        "action_priority": priority,
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
