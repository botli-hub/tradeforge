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
    hold_theta_max_dte = int(cfg.get("hold_theta_max_dte", 14))
    shallow_itm_pct = float(cfg.get("shallow_itm_pct", 1.5))
    deep_moneyness_pct = float(cfg.get("deep_itm_moneyness_pct", 3.0))
    # OTM 安全垫过薄(默认 1.5%)时,不鼓励硬吃 θ/硬 roll 到无视风险
    thin_otm_pct = float(cfg.get("thin_otm_buffer_pct", 1.5))
    # 浮盈已很高(默认 80%)且 DTE 仍不短 → 倾向落袋,避免「永远拿着」
    max_hold_profit_pct = float(cfg.get("max_hold_profit_pct", 80.0))

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
            secondary_hint = "若需腾出仓位/换股,仍可止盈买回"
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
        confidence = 72
    elif code == ACTION_NONE and underwater:
        confidence = 55  # 条件持有,依赖用户接货意愿
    elif code == ACTION_NONE:
        confidence = 60
    elif early_assign:
        confidence = 82
    if thin_otm and code in (ACTION_HOLD_THETA, ACTION_NONE):
        confidence = max(40, confidence - 15)

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
