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


def post_assign_hint(
    cycle: Dict[str, Any],
    *,
    uncovered_shares: Optional[float] = None,
) -> Dict[str, Any]:
    """接货后 / 部分覆盖后下一步。时机层按立场分流,不再一律优先挂 CC。"""
    from app.core.wheel_call_timing import (
        attach_cc_timing, evaluate_cc_timing,
        ensure_sell_above_column, get_target_sell_above,
    )

    symbol = cycle.get("symbol")
    shares = float(cycle.get("shares") or 0)
    free = float(uncovered_shares) if uncovered_shares is not None else shares
    cb = cost_basis_of(cycle)
    contracts = int(free // 100) if free >= 100 else 0
    notes: List[str] = []
    if cycle.get("status") == "CC_OPEN" and uncovered_shares is not None:
        notes.append(f"{symbol} 卖Call中,尚有未覆盖 {free:g}/{shares:g} 股")
    else:
        notes.append(f"{symbol} 已接货 {shares:g} 股")
    if cb is not None:
        notes.append(f"有效成本基础 ≈ ${cb:.2f}/股(含权利金摊薄)")
    if contracts >= 1:
        notes.append(f"可再挂约 {contracts} 张 Covered Call(参考,非下单)")
    else:
        notes.append("未覆盖股份不足 100 股,暂不能再挂标准 CC")

    ensure_sell_above_column()
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

    sell_above = get_target_sell_above(symbol) if symbol else None
    if sell_above is None:
        sell_above = target.get("sell_above") or target.get("call_floor")

    touch = None
    try:
        from app.data.leaps_repository import get_latest_call_touch
        age_h = 72.0
        try:
            age_h = float(((cfg.get("wheel_timing") or {}).get("call_touch_max_age_hours") or 72))
        except (TypeError, ValueError):
            age_h = 72.0
        touch = get_latest_call_touch(str(symbol or ""), max_age_hours=age_h)
    except Exception:
        touch = None

    timing = evaluate_cc_timing(
        stance=target.get("stance") or cycle.get("stance"),
        spot=spot,
        cost_basis=cb,
        sell_above=sell_above,
        shares=free,
        uncovered_days=cycle.get("uncovered_days"),
        iv_rank=iv_rank if iv_rank is not None else (touch or {}).get("iv_rank"),
        min_annualized=target.get("min_annualized"),
        dte_min=target.get("dte_min"),
        dte_max=target.get("dte_max"),
        candidate_ann=(touch or {}).get("annualized"),
        candidate_dte=(touch or {}).get("dte"),
        ema_touch=bool(touch),
        ema_type=(touch or {}).get("ema_type"),
        cfg=cfg,
    )
    if touch:
        timing["touch"] = {
            "contract_code": touch.get("contract_code"),
            "strike": touch.get("strike"),
            "last_seen": touch.get("last_seen") or touch.get("created_at"),
            "ema_type": touch.get("ema_type"),
            "dte": touch.get("dte"),
        }

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
    """HOLDING,或 CC_OPEN 仍有≥100 未覆盖股 → 待办(按 Call 时机分档)。"""
    from app.data import wheel_repository as repo
    from app.core.wheel_cc_legs import uncovered_shares_of

    out: List[Dict[str, Any]] = []
    for c in repo.get_cycles(include_closed=False):
        status = c.get("status")
        shares = float(c.get("shares") or 0)
        if shares <= 0:
            continue
        if status == "HOLDING":
            hint = post_assign_hint(c)
        elif status == "CC_OPEN":
            free = uncovered_shares_of(c)
            if free < 100:
                continue
            hint = post_assign_hint(c, uncovered_shares=free)
        else:
            continue
        uncovered = c.get("uncovered_days")
        if uncovered is not None:
            hint["uncovered_days"] = uncovered
        out.append(hint)
    out.sort(key=lambda x: (x.get("priority") or 9, -(x.get("uncovered_days") or 0)))
    return out
