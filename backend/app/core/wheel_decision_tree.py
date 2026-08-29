"""decide_position 核心树. 包装入口见 wheel_decision.

产品默认允许接货:深 ITM Put 是接货窗口,不是默认 Roll。
不愿接货的标的不应进轮子;income 仅作偏离守卫。
"""
from typing import Any, Dict, List, Optional

from app.core.wheel_decision_lib import *  # noqa: F401,F403
from app.core.wheel_decision_lib import (  # noqa: F401
    ACTION_CLOSE, ACTION_HOLD_THETA, ACTION_NONE, ACTION_PREPARE_ASSIGN,
    ACTION_REPLACE, ACTION_ROLL, ACTION_ROLL_ADJUST, POSITION_QUANT,
    build_assign_checklist, eval_hold_for_theta, eval_would_open_today,
    capital_employed, captured_annualized, parse_days_held,
    merge_pos_quant, otm_buffer_pct, remaining_annualized, residual_floor,
)


def _call_ok_to_deliver(item: Dict[str, Any], strike: float) -> bool:
    """Call strike 已在成本/愿卖之上 → 交货符合预设。"""
    from app.core.wheel_call_timing import strike_floor
    fl = strike_floor(item.get("cost_basis") or item.get("share_cost"), item.get("sell_above"))
    try:
        return bool(fl is not None and float(fl) > 0 and float(strike) >= float(fl))
    except (TypeError, ValueError):
        return False


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
    deep_itm_delta_put = float(q["deep_itm_delta"])
    deep_itm_delta_call = float(q.get("deep_itm_delta_call", deep_itm_delta_put))
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
    deep_itm_delta = deep_itm_delta_call if str(side or "").upper() == "CALL" else deep_itm_delta_put
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

    cap = capital_employed(
        side=side,
        strike=float(strike or 0),
        spot=float(spot or 0) or None,
        cost_basis=item.get("cost_basis") or item.get("share_cost"),
    )
    remaining_ann = remaining_annualized(
        close_px, float(cap or 0), dte if isinstance(dte, int) else None,
    )
    days_held = parse_days_held(item)
    captured_ann = captured_annualized(profit_pct, days_held)
    iv_rank = item.get("iv_rank")
    try:
        iv_rank = float(iv_rank) if iv_rank is not None else None
    except (TypeError, ValueError):
        iv_rank = None
    fast_profit_days = int(q["fast_profit_days"])

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
        iv_rank=iv_rank,
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

    velocity_close = False
    if (
        profit_hit
        and days_held is not None
        and days_held <= fast_profit_days
        and dte is not None
        and dte > hold_theta_max_dte
        and not fee_trap
    ):
        hold_for_theta = False
        velocity_close = True

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
        stance=item.get("stance"),
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
    if velocity_close:
        reasons.append(
            f"已持{days_held}天兑现年化{captured_ann:g}%,剩余DTE{dte}> {hold_theta_max_dte} → 落袋周转"
        )
    if hold_meta.get("iv_crush"):
        reasons.append(f"IV分位 {iv_rank:.0f}<{q['iv_low_rank']:.0f} 塌缩,止盈优先于吃θ")
    elif hold_meta.get("iv_rich"):
        reasons.append(f"IV分位 {iv_rank:.0f}≥{q['iv_high_rank']:.0f} 高位,剩余权利金偏贵")
    if cap:
        reasons.append(
            f"占用权益 ${cap:g}" + ("(持股)" if str(side or "").upper() == "CALL" else "(CSP担保)")
        )
    if capital_tight and (low_yield or soft_hit):
        util_txt = f"{capital_util_pct:.0f}%" if capital_util_pct is not None else f"≥{capital_tight_util:g}%"
        reasons.append(f"资金紧(利用率 {util_txt}),优先释放低效担保")

    # ── 决策树(量化序,命中即停) ──
    from app.core.wheel_stance import STANCE_INCOME, normalize_stance
    st = normalize_stance(item.get("stance"))

    code: str = ACTION_NONE
    hint: Optional[str] = None
    priority = 9
    prefer_card: Optional[str] = None
    secondary_hint: Optional[str] = None
    branch = "none"

    # 1 深 ITM:允许接货的 Put = 接货窗口;超愿接或只收租才 Roll
    if deep_itm:
        if side == "PUT" and st != STANCE_INCOME and not strike_above_floor:
            branch = "deep_itm_acquire"
            code, priority = ACTION_PREPARE_ASSIGN, 1
            prefer_card = "adjust_strike"
            hint = "深ITM·准备接货(轮子成功路径)"
            secondary_hint = "不愿按此 strike 接:才 Roll 调低"
            reasons.append("允许接货:深ITM Put 视为接货窗口,不默认 Roll")
        elif side == "PUT" and st != STANCE_INCOME and strike_above_floor:
            branch = "deep_itm_above_floor"
            code, priority = ACTION_ROLL_ADJUST, 1
            prefer_card = "adjust_strike"
            hint = f"深ITM且 strike>{floor_price:g}:超愿接,Roll 调低或平"
        elif side == "CALL" and _call_ok_to_deliver(item, float(strike or 0)):
            branch = "deep_itm_call_deliver"
            code, priority = ACTION_PREPARE_ASSIGN, 1
            prefer_card = "adjust_strike"
            hint = "深ITM Call·已在愿卖/成本之上,准备交货"
            secondary_hint = "想继续持股:Roll 调高 strike"
        elif side == "CALL":
            branch = "deep_itm"
            code, priority = ACTION_ROLL_ADJUST, 2
            prefer_card = "adjust_strike"
            hint = f"深ITM(Δ>{deep_itm_delta:g}/价内>{deep_moneyness_pct:g}%):低于成本/愿卖,Roll 调高"
        else:
            branch = "deep_itm"
            code, priority = ACTION_ROLL_ADJUST, 1
            prefer_card = "adjust_strike"
            hint = f"深ITM(Δ>{deep_itm_delta:g}/价内>{deep_moneyness_pct:g}%):不愿接货,Roll 调 strike"
    # 2 风险:临期 ITM
    elif itm and expiring:
        branch = "prepare_assign"
        code, priority = ACTION_PREPARE_ASSIGN, 1
        prefer_card = "adjust_strike"
        hint = (
            f"临期ITM(DTE≤{gamma_dte}):准备接货"
            if side == "PUT" and st != STANCE_INCOME
            else f"临期ITM(DTE≤{gamma_dte}):Roll 或准备接货"
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
    # 5 效率:硬止盈 / 过高持有 / 快速兑现周转
    elif profit_hit or profit_cap_close:
        branch = "close_velocity" if velocity_close else "close_profit"
        code, priority = ACTION_CLOSE, 2
        prefer_card = "no_roll"
        if profit_cap_close and not profit_hit:
            hint = f"过高持有(≥{max_hold_profit_pct:g}%)落袋"
        elif velocity_close:
            hint = f"止盈周转(持{days_held}天兑现年化{captured_ann:g}%)"
        elif side == "CALL":
            hint = f"止盈(≥{profit_target:g}%)结束Call义务"
        else:
            hint = f"止盈(≥{profit_target:g}%)释放担保"
        if thin_otm:
            secondary_hint = f"OTM垫<{thin_otm_pct:g}%,落袋后择机再开"
    # 6 效率:软止盈+低效
    elif soft_hit and low_yield:
        if str(side or "").upper() == "CALL":
            branch = "lift_cover_soft"
            code, priority = ACTION_CLOSE, 3
            prefer_card = "no_roll"
            hint = f"软止盈≥{soft_profit:g}%+覆盖年化<{min_annualized}% → 揭盖"
        else:
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
        if str(side or "").upper() == "CALL":
            branch = "lift_cover"
            code, priority = ACTION_CLOSE, 4
            prefer_card = "no_roll"
            hint = f"覆盖年化{remaining_ann}%<{min_annualized}% → 揭盖等更好时机"
        else:
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
        "deep_itm_delta_put": deep_itm_delta_put,
        "deep_itm_delta_call": deep_itm_delta_call,
        "fast_profit_days": fast_profit_days,
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
        "velocity_close": velocity_close,
        "days_held": days_held,
        "captured_annualized": captured_ann,
        "capital_employed": cap,
        "iv_rank": iv_rank,
        "iv_crush": bool(hold_meta.get("iv_crush")),
        "iv_rich": bool(hold_meta.get("iv_rich")),
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
