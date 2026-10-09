"""Wheel Roll 决策引擎

把「期权链子集」升级为交易员决策台:
- 顶部场景结论(决策树)
- 三卡片: Roll out / 调 strike / 不平不 roll
- 报价情景: optimistic / default / conservative
- 效率指标、流动性、事件、限价建议、成本硬底线
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple


# ── 报价情景 ──────────────────────────────────────────────────────────────────

def _mid(bid: float, ask: float) -> float:
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    return bid if bid > 0 else (ask if ask > 0 else 0.0)


def _tick(price: float) -> float:
    """美股期权跳动。$3 以下 $0.05,$3 及以上非便士 $0.10。"""
    from app.core.wheel_sizing import option_tick
    return option_tick(price)


def _open_ladder(bid: float, ask: float):
    from app.core.wheel_sizing import limit_ladder
    return limit_ladder(bid, ask)


def validate_roll_economics(
    *,
    side: str,
    buyback: float,
    sell: float,
    original_credit: float = 0,
    new_strike: float = 0,
    floor_price: Optional[float] = None,
    call_floor: Optional[float] = None,
    prior_roll_count: int = 0,
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """返回错误文案;None 表示允许。没有愿接价时跳过 strike 底线。"""
    c = cfg or {}
    require = bool(c.get("roll_require_credit", True))
    try:
        max_debit_pct = float(c.get("roll_max_debit_pct", 0.25) or 0)
    except (TypeError, ValueError):
        max_debit_pct = 0.25
    try:
        max_count = int(c.get("roll_max_count", 4) or 4)
    except (TypeError, ValueError):
        max_count = 4
    net = float(sell) - float(buyback)
    if require and net < -1e-9:
        cap = max(0.0, float(original_credit or 0) * max_debit_pct)
        if -net > cap + 1e-9:
            return f"Roll 净借记 {-net:.2f} 超过原权利金的 {max_debit_pct:.0%}"
    if prior_roll_count >= max_count:
        return f"已 Roll {prior_roll_count} 次,达到上限 {max_count}"
    if str(side or "").upper() == "PUT" and floor_price is not None and float(floor_price) > 0:
        if float(new_strike) > float(floor_price) + 1e-9:
            return f"新 Put strike {float(new_strike):g} 高于愿接 {float(floor_price):g}"
    if str(side or "").upper() == "CALL" and call_floor is not None and float(call_floor) > 0:
        if float(new_strike) + 1e-9 < float(call_floor):
            return f"新 Call strike {float(new_strike):g} 低于成本/愿卖 {float(call_floor):g}"
    return None


def pricing_scenarios(
    close_bid: float,
    close_ask: float,
    open_bid: float,
    open_ask: float,
    size: int = 100,
) -> Dict[str, Dict[str, float]]:
    """三档净现金流(每张, 已×size 的金额在调用方乘;这里返回单价)。

    close = 买回 short; open = 新卖 short。
    net_credit_per_share = open_sell_price - close_buy_price
    """
    c_mid = _mid(close_bid, close_ask)
    o_mid = _mid(open_bid, open_ask)
    c_ask = close_ask if close_ask > 0 else (close_bid if close_bid > 0 else c_mid)
    o_bid = open_bid if open_bid > 0 else o_mid
    # 悲观: 平仓更贵、开仓更便宜
    c_worse = c_ask + _tick(c_ask)
    o_worse = max(0.0, o_bid - _tick(o_bid)) if o_bid > 0 else 0.0

    def pack(buy: float, sell: float) -> Dict[str, float]:
        net = sell - buy
        return {
            "close_price": round(buy, 4),
            "open_price": round(sell, 4),
            "net_credit_per_share": round(net, 4),
            "net_credit_per_contract": round(net * size, 2),
        }

    return {
        "optimistic": pack(c_mid, o_mid),
        "default": pack(c_ask, o_bid),
        "conservative": pack(c_worse, o_worse),
    }


def spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100, 2)


# ── 决策树 ────────────────────────────────────────────────────────────────────

def resolve_roll_leg(cycle: Dict[str, Any], close_contract_code: Optional[str] = None) -> Dict[str, Any]:
    """Roll 台用的在场腿。

    缺省用周期 open_* 主腿。传入 close_contract_code 时按登记接口同一规则
    (代码去掉一个 US. 前缀)选中非主腿 CC。找不到则仍回主腿。
    """
    if not cycle:
        return {}
    code = str(close_contract_code or "").strip()
    if not code:
        return dict(cycle)
    from app.core.wheel_cc_legs import expand_open_option_rows

    norm = code.removeprefix("US.")
    matched = [
        r for r in expand_open_option_rows([cycle])
        if (r.get("open_contract_code") or "").removeprefix("US.") == norm
    ]
    if len(matched) == 1:
        return matched[0]
    return dict(cycle)


def _as_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def decide_roll_scenario(
    *,
    side: str,
    dte: Optional[int],
    profit_pct: Optional[float],
    itm: bool,
    deep_itm: bool,
    delta: float,
    remaining_ann: Optional[float],
    min_annualized: float,
    profit_target: float,
    hard_roll_dte: int = 21,
    close_notional: float = 0.0,
    pos_cfg: Optional[Dict[str, Any]] = None,
    strike: Optional[float] = None,
    spot: Optional[float] = None,
    floor_price: Optional[float] = None,
    stance: Optional[str] = None,
    cost_basis: Optional[float] = None,
    share_cost: Optional[float] = None,
    sell_above: Optional[float] = None,
    open_price: Optional[float] = None,
    buyback_ask: Optional[float] = None,
    current_price: Optional[float] = None,
    qty: Optional[float] = None,
    contract_size: Optional[float] = None,
    cycle_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Roll 场景跟持仓树的 action_code,不再单独判深 ITM。

    PREPARE_ASSIGN 不推荐调 strike。只收租或 strike 高于愿接价时,
    树给出的 roll/close 才推荐调 strike。剩余年化用树的 capital_employed。
    deep_itm / remaining_ann 参数保留给旧调用方,动作以树为准。
    """
    del deep_itm, remaining_ann, close_notional  # 树自己算,避免第二套门槛
    from app.core.wheel_decision import decide_position

    cfg = dict(pos_cfg or {})
    if "hard_roll_dte" not in cfg and hard_roll_dte is not None:
        cfg["hard_roll_dte"] = hard_roll_dte
    dte_i = _as_int(dte)
    item: Dict[str, Any] = {
        "side": side,
        "strike": strike if strike is not None else 0,
        "spot": spot,
        "dte": dte_i,
        "buyback_ask": buyback_ask,
        "current_price": current_price if current_price is not None else buyback_ask,
        "open_price": open_price,
        "profit_pct": profit_pct,
        "itm": bool(itm),
        "delta": delta,
        "qty": qty if qty is not None else 1,
        "contract_size": contract_size if contract_size is not None else 100,
        "floor_price": floor_price,
        "stance": stance,
        "cost_basis": cost_basis,
        "share_cost": share_cost,
        "sell_above": sell_above,
    }
    if cycle_id:
        item["cycle_id"] = cycle_id
    pos = decide_position(
        item,
        float(min_annualized or 0),
        float(profit_target if profit_target is not None else 50),
        cfg or None,
    )
    return _scenario_from_position(
        pos,
        side=side,
        profit_pct=profit_pct,
        profit_target=float(profit_target if profit_target is not None else 50),
        dte=dte_i,
        hard_roll_dte=int(cfg.get("hard_roll_dte") or hard_roll_dte or 21),
    )


def _income_stance(stance: Any) -> bool:
    return str(stance or "").strip().lower() in ("income", "只收租", "rent", "premium")


def _offer_strike_adjust(pos: Dict[str, Any], code: str, income: bool, above: bool) -> bool:
    """树说 roll/close,且原因是只收租或超愿接 → 调 strike。PREPARE_ASSIGN 永不。"""
    if code == "PREPARE_ASSIGN":
        return False
    if code == "ROLL_ADJUST":
        return True
    if code not in ("ROLL", "CLOSE", "REPLACE"):
        return False
    if above:
        return True
    if not income:
        return False
    branch = str(pos.get("decision_branch") or "")
    return bool(
        pos.get("deep_itm")
        or branch in ("income_assign_warn", "deep_itm", "deep_itm_above_floor")
    )


def _scenario_from_position(
    pos: Dict[str, Any],
    *,
    side: str,
    profit_pct: Optional[float],
    profit_target: float,
    dte: Optional[int],
    hard_roll_dte: int,
) -> Dict[str, Any]:
    code = str(pos.get("action_code") or "NONE").upper()
    branch = str(pos.get("decision_branch") or "")
    tree = pos.get("decision_tree") or {}
    stance = pos.get("stance") or tree.get("stance")
    income = _income_stance(stance)
    above = bool(pos.get("strike_above_floor"))
    hint = pos.get("action_hint") or ""
    rem = pos.get("remaining_annualized")
    side_u = str(side or "").upper()
    common = {
        "position_action": code,
        "decision_branch": branch,
        "remaining_annualized": rem,
        "capital_employed": tree.get("capital_employed"),
        "deep_itm": bool(pos.get("deep_itm")),
    }
    if code == "PREPARE_ASSIGN":
        label = "接货" if side_u == "PUT" else "交货"
        return {
            **common,
            "scenario": "prepare_assign",
            "recommended_action": "let_expire",
            "headline": hint or f"准备{label}(轮子成功路径),不优先调 strike",
            "detail": (
                f"持仓决策是准备{label}。不推荐调 strike 的 Roll;"
                "不愿按此 strike 成交再考虑买回。"
            ),
            "prefer_card": "no_roll",
        }
    if _offer_strike_adjust(pos, code, income, above):
        adj = "out_and_up" if side_u == "CALL" else "out_and_down"
        why = []
        if income:
            why.append("只收租")
        if above:
            why.append("strike 高于愿接价")
        why_s = "、".join(why) if why else "持仓决策"
        return {
            **common,
            "scenario": "deep_itm" if pos.get("deep_itm") else "roll_adjust",
            "recommended_action": "roll_adjust",
            "headline": hint or f"{why_s}:优先评估调 strike 的 Roll",
            "detail": (
                f"{why_s},接货不是这条腿的成功路径。"
                "Call 看能否 credit roll up;Put 看能否 credit roll down。"
                "大额 debit 防守通常不如买回"
            ),
            "prefer_card": "adjust_strike",
            "prefer_branch": adj,
        }
    if code == "HOLD_THETA":
        return {
            **common,
            "scenario": "hold_theta",
            "recommended_action": "let_expire",
            "headline": hint or (
                f"OTM 高浮盈,优先吃 θ(浮盈 {profit_pct}%"
                + (f",剩余年化 {rem}%" if rem is not None else "")
                + ")"
            ),
            "detail": (
                "与持仓决策一致:剩余权利金仍体面或临期摩擦大,不必为 credit 硬 roll;"
                "若需腾仓/换股再考虑买回。"
            ),
            "prefer_card": "no_roll",
        }
    if code == "CLOSE":
        detail = (
            "结束 Call 义务、保留持股,不必为 credit 硬 roll"
            if side_u == "CALL"
            else "释放担保金再开新轮,不必为 credit 硬 roll"
        )
        shown = profit_pct
        return {
            **common,
            "scenario": "take_profit",
            "recommended_action": "close_now",
            "headline": hint or f"建议止盈平仓(浮盈 {shown}% ≥ 目标 {profit_target:g}%)",
            "detail": detail,
            "prefer_card": "no_roll",
        }
    if code == "ROLL":
        return {
            **common,
            "scenario": "roll_21dte",
            "recommended_action": "roll_out",
            "headline": hint or f"DTE {dte} ≤ {hard_roll_dte} 且未达止盈 → 优先 Roll out",
            "detail": "同 strike(或合规 strike)换到 30–45 DTE、目标 δ,尽量 for credit",
            "prefer_card": "roll_out",
        }
    if code == "REPLACE":
        return {
            **common,
            "scenario": "low_yield",
            "recommended_action": "close_or_roll",
            "headline": hint or f"剩余年化偏低({rem}%),仓位低效",
            "detail": (
                "优先平仓后再卖 CC" if side_u == "CALL" else "优先平仓换仓"
            ) + ";若 roll for credit 效率尚可,选 $/天 更高者",
            "prefer_card": "no_roll",
        }
    return {
        **common,
        "scenario": "hold_or_monitor",
        "recommended_action": "hold",
        "headline": hint or "仓位尚健康,可继续持有观察",
        "detail": "无需强行 roll;有更好 credit/效率机会时再换",
        "prefer_card": "no_roll",
    }


# ── 候选增强 ──────────────────────────────────────────────────────────────────

def enrich_candidate(
    *,
    side: str,
    contract: Dict[str, Any],
    expiry: str,
    dte: int,
    cur_dte: Optional[int],
    cur_strike: float,
    cur_expiry: str,
    buyback_bid: float,
    buyback_ask: float,
    size: int,
    spot: Optional[float],
    cost_basis: Optional[float],
    call_cost_floor: Optional[float],
    shares: float,
    band: str,
    branch: str,
    delta_unknown: bool,
    d_for_sort: float,
    target_mid: float,
    delta_lo: float,
    delta_hi: float,
    covers_earnings: bool,
    covers_dividend: bool,
    allow_down_strike: bool,
) -> Optional[Dict[str, Any]]:
    strike = float(contract.get("strike") or 0)
    bid = float(contract.get("bid") or 0)
    ask = float(contract.get("ask") or 0)
    oi = int(contract.get("open_interest") or 0)
    d = abs(float(contract.get("delta") or 0))
    if bid <= 0 and ask <= 0:
        return None

    sp = spread_pct(bid, ask)
    scenarios = pricing_scenarios(buyback_bid, buyback_ask, bid, ask, size)
    default_net = scenarios["default"]["net_credit_per_contract"]
    cons_net = scenarios["conservative"]["net_credit_per_contract"]

    extra_dte = max((dte - (cur_dte or 0)), 1)
    credit_per_day = round(default_net / extra_dte, 3)
    # 新仓现金担保年化(用 default 开仓权利金)
    open_px = scenarios["default"]["open_price"]
    collateral = strike if side == "PUT" else (cost_basis or strike)
    ann = round(open_px / collateral * 365 / dte * 100, 2) if collateral and dte else None

    # 若被 call/assign 路径
    if_called_total = None
    if_assigned_cost = None
    if side == "CALL" and cost_basis is not None and shares > 0:
        # 被 call: (strike - cost)*shares + 本笔 roll 净权利金(default)
        if_called_total = round(
            (strike - cost_basis) * shares + default_net, 2
        )
    if side == "PUT":
        if_assigned_cost = round(strike - open_px, 4)

    # 新 cost basis 粗估(Call: 原 basis - 本次净权利金/股)
    new_cost_basis = None
    if side == "CALL" and cost_basis is not None and shares > 0:
        new_cost_basis = round(cost_basis - default_net / shares, 4)

    # 排序分: δ 契合 + 效率 + credit(保守) - 价差惩罚
    delta_score = 1.0 - min(abs(d_for_sort - target_mid) / max(delta_hi - delta_lo, 0.05), 1.0)
    if band != "preferred":
        delta_score *= 0.5
    eff_score = max(min(credit_per_day / 5.0, 2.0), -2.0)  # ~$5/天满分附近
    liq_pen = 0.0
    if sp is not None:
        if sp > 10:
            liq_pen = 3.0
        elif sp > 6:
            liq_pen = 1.0
    strike_bonus = 0.0
    if side == "CALL" and call_cost_floor and call_cost_floor > 0:
        strike_bonus = min((strike - call_cost_floor) / call_cost_floor, 0.5) * 1.5
    # 同 strike roll out 加分
    same_strike_bonus = 1.5 if abs(strike - cur_strike) < 1e-6 else 0.0
    # 默认禁止 call down / put up(更差方向)除非 allow
    worse_dir = False
    if side == "CALL" and strike + 1e-9 < cur_strike and not allow_down_strike:
        worse_dir = True
    if side == "PUT" and strike > cur_strike + 1e-9 and not allow_down_strike:
        worse_dir = True

    rank = (
        delta_score * 10
        + eff_score * 3
        + cons_net / max(size * max(strike * 0.01, 0.3), 1) * 2
        + strike_bonus
        + same_strike_bonus
        - liq_pen
        - (2.0 if covers_earnings and side == "PUT" else 0)
        - (1.5 if covers_dividend and side == "CALL" else 0)
        - (5.0 if worse_dir else 0)
    )

    # 限价建议
    limit_close = round(scenarios["default"]["close_price"], 2)
    limit_open = round(scenarios["default"]["open_price"], 2)
    net_target = round(limit_open - limit_close, 2)

    return {
        "contract_code": contract.get("option_symbol"),
        "expiry": expiry,
        "dte": dte,
        "strike": strike,
        "delta": None if delta_unknown else round(d, 3),
        "delta_unknown": delta_unknown,
        "bid": bid,
        "ask": ask,
        "spread_pct": sp,
        "open_interest": oi,
        "volume": contract.get("volume") or 0,
        "branch": branch,
        "band": band,
        "same_strike": abs(strike - cur_strike) < 1e-6,
        "worse_direction": worse_dir,
        "pricing": scenarios,
        # 兼容旧字段: default 情景
        "net_credit_per_contract": default_net,
        "net_credit_conservative": cons_net,
        "credit_per_day": credit_per_day,
        "extra_dte": extra_dte,
        "annualized": ann,
        "if_called_total": if_called_total,
        "if_assigned_cost": if_assigned_cost,
        "new_cost_basis_est": new_cost_basis,
        "covers_earnings": covers_earnings,
        "covers_dividend": covers_dividend,
        "rank_score": round(rank, 4),
        "limit_hints": {
            "close_limit": limit_close,
            "open_limit": limit_open,
            "net_credit_target": net_target,
            "tick": _tick(max(limit_open, limit_close, 0)),
            "combo_net_per_share": round(limit_open - limit_close, 4),
            "open_ladder": _open_ladder(bid, ask),
            "note": "平仓挂 close_limit(买),开仓挂 open_limit(卖);组合净价=新卖−买回,限价从中间价按 tick 下调",
        },
        "preview": {
            "new_strike": strike,
            "new_expiry": expiry[:10],
            "new_delta": None if delta_unknown else round(d, 3),
            "new_dte": dte,
            "net_credit_default": default_net,
            "net_credit_conservative": cons_net,
            "new_cost_basis_est": new_cost_basis,
            "if_called_total": if_called_total,
            "if_assigned_cost": if_assigned_cost,
        },
        "draft_legs": [
            {
                "trade_type": "BUY_PUT_CLOSE" if side == "PUT" else "BUY_CALL_CLOSE",
                "contract_code": None,  # 调用方填
                "strike": cur_strike,
                "expiry": cur_expiry,
                "price": limit_close,
                "is_roll": True,
            },
            {
                "trade_type": "SELL_PUT" if side == "PUT" else "SELL_CALL",
                "contract_code": contract.get("option_symbol"),
                "strike": strike,
                "expiry": expiry,
                "price": limit_open,
                "is_roll": True,
            },
        ],
    }


def classify_branch(side: str, strike: float, cur_strike: float, expiry: str, cur_expiry: str) -> str:
    same_k = abs(strike - cur_strike) < 1e-6
    further = str(expiry)[:10] > str(cur_expiry)[:10]
    if same_k and further:
        return "out"
    if side == "CALL":
        if strike > cur_strike + 1e-9 and further:
            return "out_and_up"
        if strike > cur_strike + 1e-9:
            return "up"
        if strike + 1e-9 < cur_strike and further:
            return "out_and_down"
        if strike + 1e-9 < cur_strike:
            return "down"
    else:
        if strike + 1e-9 < cur_strike and further:
            return "out_and_down"
        if strike + 1e-9 < cur_strike:
            return "down"
        if strike > cur_strike + 1e-9 and further:
            return "out_and_up"
        if strike > cur_strike + 1e-9:
            return "up"
    return "adjust"


def pick_best(cands: List[Dict[str, Any]], predicate) -> Optional[Dict[str, Any]]:
    pool = [c for c in cands if predicate(c)]
    if not pool:
        return None
    pool.sort(key=lambda x: (-(x.get("rank_score") or 0), -(x.get("net_credit_conservative") or 0)))
    return pool[0]


def build_decision_cards(
    candidates: List[Dict[str, Any]],
    *,
    side: str,
    cur_strike: float,
    buyback_ask: float,
    size: int,
    open_price: float,
    scenario: Dict[str, Any],
    allow_down_strike: bool,
) -> Dict[str, Any]:
    """三卡片 + 推荐高亮。"""
    # 主推列表排除 worse_direction(除非允许)
    clean = [
        c for c in candidates
        if allow_down_strike or not c.get("worse_direction")
    ]
    best_out = pick_best(
        clean,
        lambda c: c.get("branch") == "out" or c.get("same_strike"),
    )
    if side == "CALL":
        best_adj = pick_best(
            clean,
            lambda c: c.get("branch") in ("out_and_up", "up"),
        ) or pick_best(
            clean,
            lambda c: (not c.get("same_strike")) and (c.get("strike") or 0) > cur_strike,
        )
    else:
        best_adj = pick_best(
            clean,
            lambda c: c.get("branch") in ("out_and_down", "down"),
        ) or pick_best(
            clean,
            lambda c: not c.get("same_strike") and (c.get("strike") or 0) < cur_strike,
        )

    close_cost = round(buyback_ask * size, 2)
    locked = None
    if open_price and buyback_ask is not None:
        locked = round((open_price - buyback_ask) * size, 2)

    rec_sub = "close_now" if scenario.get("scenario") == "take_profit" else "let_expire"
    if scenario.get("recommended_action") == "close_now":
        rec_sub = "close_now"
    elif scenario.get("recommended_action") == "let_expire":
        rec_sub = "let_expire"

    is_call = side == "CALL"
    close_pros = (
        ["结束 Call 义务、保留持股", "锁定已实现权利金", "便于再卖下一轮 CC"]
        if is_call else
        ["释放 CSP 现金担保", "锁定已实现权利金", "便于再开新 Put"]
    )
    expire_cons = (
        ["若到期 ITM 可能被 call 走(交货)", "临期 gamma / 提前行权(除息)风险"]
        if is_call else
        ["若到期 ITM 可能被指派接货", "临期 gamma 风险"]
    )
    expire_when = (
        "OTM + 临期 + 愿意继续持股吃 θ"
        if is_call else
        "OTM + 临期 + 不需要现金担保周转"
    )
    no_roll = {
        "key": "no_roll",
        "title": "不 Roll",
        "available": True,
        "options": {
            "close_now": {
                "action": "close_now",
                "buyback_cost_per_contract": close_cost,
                "locked_premium_est": locked,
                "pros": close_pros,
                "cons": ["放弃剩余 theta", "可能错过 roll credit"],
                "when": "浮盈达标、剩余年化低、或需要调仓时",
            },
            "let_expire": {
                "action": "let_expire",
                "buyback_cost_per_contract": 0,
                "pros": ["零手续费吃完剩余权利金(若保持 OTM)"],
                "cons": expire_cons,
                "when": expire_when,
            },
        },
        "recommended_sub": rec_sub,
    }

    cards = {
        "roll_out": _make_card(
            "roll_out",
            "Roll Out(保 strike / 换时间)",
            best_out,
            "经典防守:不改或少改 strike,只换更远到期,尽量 for credit",
            side,
        ),
        "adjust_strike": _make_card(
            "adjust_strike",
            "Roll 调 strike(Call↑ / Put↓)" if side == "CALL" else "Roll 调 strike(Put↓)",
            best_adj,
            "Call 上移锁利润或降被 call 概率;Put 下移降接货概率",
            side,
        ),
        "no_roll": no_roll,
    }

    prefer = scenario.get("prefer_card") or "roll_out"
    # 若推荐卡无候选,回退
    if prefer == "roll_out" and not cards["roll_out"].get("available"):
        prefer = "adjust_strike" if cards["adjust_strike"].get("available") else "no_roll"
    if prefer == "adjust_strike" and not cards["adjust_strike"].get("available"):
        prefer = "roll_out" if cards["roll_out"].get("available") else "no_roll"

    return {
        "cards": cards,
        "highlighted": prefer,
        "scenario": scenario,
    }


def _pros(pick: Dict, side: str) -> List[str]:
    out = []
    if (pick.get("net_credit_per_contract") or 0) > 0:
        out.append("default 情景净收权利金")
    if pick.get("same_strike"):
        out.append("同 strike,逻辑清晰")
    if pick.get("band") == "preferred":
        out.append("δ 落在目标带")
    if pick.get("if_called_total") is not None and pick["if_called_total"] > 0:
        out.append(f"若被 call 估盈 ${pick['if_called_total']:.0f}")
    if (pick.get("spread_pct") or 99) <= 6:
        out.append("价差尚可")
    return out or ["见预览指标"]


def _cons(pick: Dict, side: str) -> List[str]:
    out = []
    if (pick.get("net_credit_conservative") or 0) < 0:
        out.append("保守报价下为 net debit")
    if pick.get("covers_earnings"):
        out.append("新到期覆盖财报")
    if pick.get("covers_dividend"):
        out.append("新到期前有除息(Call 留意提前行权)")
    if (pick.get("spread_pct") or 0) > 8:
        out.append(f"点差偏宽 {pick.get('spread_pct')}%")
    if pick.get("worse_direction"):
        out.append("strike 向不利方向移动")
    return out


def _make_card(key, title, pick, blurb, side) -> Dict[str, Any]:
    if not pick:
        return {
            "key": key, "title": title, "available": False,
            "blurb": blurb, "candidate": None,
            "summary": "当前筛选下无合适合约",
            "pros": [], "cons": [],
        }
    net = pick.get("net_credit_per_contract") or 0
    cons = pick.get("net_credit_conservative") or 0
    dlt = pick.get("delta")
    dlt_s = f"{dlt:.2f}" if dlt is not None else "—"
    return {
        "key": key,
        "title": title,
        "available": True,
        "blurb": blurb,
        "candidate": pick,
        "summary": (
            f"K${pick['strike']:g} · {str(pick['expiry'])[:10]} · δ{dlt_s} · "
            f"default {'+' if net >= 0 else ''}{net:.0f}/张 · "
            f"保守 {'+' if cons >= 0 else ''}{cons:.0f}/张 · "
            f"{pick.get('credit_per_day')}$/天"
        ),
        "pros": _pros(pick, side),
        "cons": _cons(pick, side),
    }


def roll_history_for_symbol(symbol: str, limit: int = 8) -> List[Dict[str, Any]]:
    """从台账识别同日买回+再卖出的 roll 片段。"""
    from app.data import wheel_repository as repo

    trades = repo.get_trades(symbol=symbol, limit=300)
    # 按 cycle + 日聚合
    by_day: Dict[str, List[Dict]] = {}
    for t in trades:
        day = str(t.get("traded_at") or "")[:10]
        key = f"{t.get('cycle_id')}|{day}"
        by_day.setdefault(key, []).append(t)

    rolls = []
    for key, legs in by_day.items():
        types = {x.get("trade_type") for x in legs}
        close_types = {"BUY_PUT_CLOSE", "BUY_CALL_CLOSE"}
        open_types = {"SELL_PUT", "SELL_CALL"}
        if not (types & close_types) or not (types & open_types):
            # 也认 is_roll 标记
            if not any(x.get("is_roll") for x in legs):
                continue
        close_leg = next((x for x in legs if x.get("trade_type") in close_types), None)
        open_leg = next((x for x in legs if x.get("trade_type") in open_types), None)
        if not close_leg or not open_leg:
            continue
        size = open_leg.get("contract_size") or 100
        net = (
            (open_leg.get("price") or 0) * (open_leg.get("qty") or 1) * size
            - (close_leg.get("price") or 0) * (close_leg.get("qty") or 1) * size
            - (open_leg.get("fee") or 0) - (close_leg.get("fee") or 0)
        )
        rolls.append({
            "date": str(close_leg.get("traded_at") or "")[:10],
            "cycle_id": close_leg.get("cycle_id"),
            "close_strike": close_leg.get("strike"),
            "open_strike": open_leg.get("strike"),
            "close_expiry": close_leg.get("expiry"),
            "open_expiry": open_leg.get("expiry"),
            "net_credit": round(net, 2),
            "note": open_leg.get("note") or close_leg.get("note"),
        })
    rolls.sort(key=lambda x: x["date"], reverse=True)
    return rolls[:limit]
