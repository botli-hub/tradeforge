"""指派后流程:HOLDING 成本基础 + 首笔 CC 提示 + 卖 Call 时机。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def cost_basis_of(cycle: Dict[str, Any]) -> Optional[float]:
    """接货后有效成本 = share_cost − 累计净权利金/股数。"""
    shares = float(cycle.get("shares") or 0)
    share_cost = cycle.get("share_cost")
    if shares <= 0 or share_cost is None:
        return None
    prem = float(cycle.get("total_premium") or 0)
    return round(float(share_cost) - prem / shares, 4)


def _target_for(symbol: Optional[str]) -> Dict[str, Any]:
    if not symbol:
        return {}
    try:
        from app.data import wheel_repository as repo
        return repo.get_target(symbol) or {}
    except Exception:
        return {}


def _load_cfg() -> Dict[str, Any]:
    try:
        from app.api.leaps import _load_config
        return _load_config() or {}
    except Exception:
        return {}


def post_assign_hint(cycle: Dict[str, Any]) -> Dict[str, Any]:
    """接货后下一步。时机层按立场分流,不再一律优先挂 CC。"""
    from app.core.wheel_call_timing import attach_cc_timing, evaluate_cc_timing

    symbol = cycle.get("symbol")
    shares = float(cycle.get("shares") or 0)
    cb = cost_basis_of(cycle)
    contracts = int(shares // 100) if shares >= 100 else 0
    notes: List[str] = [
        f"{symbol} 已接货 {shares:g} 股",
    ]
    if cb is not None:
        notes.append(f"有效成本基础 ≈ ${cb:.2f}/股(含权利金摊薄)")
    if contracts >= 1:
        notes.append(f"可挂约 {contracts} 张 Covered Call(参考,非下单)")
    else:
        notes.append("持股不足 100 股,暂不能标准 CC")

    target = _target_for(symbol)
    cfg = _load_cfg()
    anchors: Dict[str, Any] = {}
    spot = None
    try:
        from app.core.volatility import get_daily_closes
        closes = get_daily_closes(symbol, limit=5)
        spot = closes[-1] if closes else None
        anchors["spot"] = spot
        if spot and cb:
            floor_cc = max(cb, float(spot) * 0.98)
            anchors["suggest_strike_floor"] = round(floor_cc, 2)
            anchors["note"] = "CC strike 锚在成本基础/愿卖价之上"
    except Exception:
        pass

    iv_rank = None
    try:
        from app.core.wheel_iv_regime import symbol_iv_rank
        iv_rank = symbol_iv_rank(symbol)
    except Exception:
        iv_rank = target.get("iv_rank")

    sell_above = target.get("sell_above") or target.get("call_floor") or cb
    timing = evaluate_cc_timing(
        stance=target.get("stance") or cycle.get("stance"),
        spot=spot,
        cost_basis=cb,
        sell_above=sell_above,
        shares=shares,
        uncovered_days=cycle.get("uncovered_days"),
        iv_rank=iv_rank,
        min_annualized=target.get("min_annualized"),
        dte_min=target.get("dte_min"),
        dte_max=target.get("dte_max"),
        cfg=cfg,
    )

    hint = {
        "cycle_id": cycle.get("id"),
        "symbol": symbol,
        "status": cycle.get("status"),
        "shares": shares,
        "share_cost": cycle.get("share_cost"),
        "cost_basis": cb,
        "cc_contracts": contracts,
        "call_anchors": anchors,
        "notes": notes,
        "min_call_strike": timing.get("strike_floor") or anchors.get("suggest_strike_floor") or cb,
    }
    return attach_cc_timing(hint, timing)


def post_assign_queue() -> List[Dict[str, Any]]:
    """所有 HOLDING 且未挂 CC 的周期 → 待办(按 Call 时机分档)。"""
    from app.data import wheel_repository as repo

    out: List[Dict[str, Any]] = []
    for c in repo.get_cycles(include_closed=False):
        if c.get("status") != "HOLDING":
            continue
        if (c.get("shares") or 0) <= 0:
            continue
        hint = post_assign_hint(c)
        uncovered = c.get("uncovered_days")
        if uncovered is not None:
            hint["uncovered_days"] = uncovered
        out.append(hint)
    out.sort(key=lambda x: (x.get("priority") or 9, -(x.get("uncovered_days") or 0)))
    return out
