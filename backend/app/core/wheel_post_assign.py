"""指派后流程:HOLDING 成本基础 + 首笔 CC 提示。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def cost_basis_of(cycle: Dict[str, Any]) -> Optional[float]:
    """接货后有效成本 = share_cost − 累计净权利金/股数。"""
    shares = float(cycle.get("shares") or 0)
    share_cost = cycle.get("share_cost")
    if shares <= 0 or share_cost is None:
        return None
    prem = float(cycle.get("total_premium") or 0)
    # total_premium 已是净收权利金(美元)
    return round(float(share_cost) - prem / shares, 4)


def post_assign_hint(cycle: Dict[str, Any]) -> Dict[str, Any]:
    """接货后下一步。"""
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
        notes.append(f"可卖约 {contracts} 张 Covered Call")
    else:
        notes.append("持股不足 100 股,暂不能标准 CC")

    # 轻量 CC 锚点(不拉链)
    anchors: Dict[str, Any] = {}
    try:
        from app.core.volatility import get_daily_closes
        closes = get_daily_closes(symbol, limit=5)
        spot = closes[-1] if closes else None
        anchors["spot"] = spot
        if spot and cb:
            # 建议 strike 不低于 max(成本, spot) 的保守上方
            floor_cc = max(cb, spot * 0.98)
            anchors["suggest_strike_floor"] = round(floor_cc, 2)
            anchors["note"] = "CC strike 建议 ≥ 成本基础,避免锁死亏损卖出"
    except Exception:
        pass

    return {
        "cycle_id": cycle.get("id"),
        "symbol": symbol,
        "status": cycle.get("status"),
        "shares": shares,
        "share_cost": cycle.get("share_cost"),
        "cost_basis": cb,
        "cc_contracts": contracts,
        "next_step": "SELL_CALL" if contracts >= 1 else "HOLD_OR_BUY_MORE",
        "next_step_hint": "去机会页找 Call / 或标的页「建议 Call」" if contracts >= 1 else "持股不足一张",
        "call_anchors": anchors,
        "notes": notes,
        "priority": 2 if contracts >= 1 else 5,
    }


def post_assign_queue() -> List[Dict[str, Any]]:
    """所有 HOLDING 且未挂 CC 的周期 → 待办。"""
    from app.data import wheel_repository as repo

    out: List[Dict[str, Any]] = []
    for c in repo.get_cycles(include_closed=False):
        if c.get("status") != "HOLDING":
            continue
        if (c.get("shares") or 0) <= 0:
            continue
        hint = post_assign_hint(c)
        # 裸奔天数
        uncovered = c.get("uncovered_days")
        if uncovered is not None:
            hint["uncovered_days"] = uncovered
            if uncovered >= 3:
                hint["priority"] = 1
                hint["notes"] = list(hint.get("notes") or []) + [
                    f"已裸奔 {uncovered} 天,theta 空转"
                ]
        out.append(hint)
    out.sort(key=lambda x: (x.get("priority") or 9, -(x.get("uncovered_days") or 0)))
    return out
