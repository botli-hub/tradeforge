"""组合层资金配置、压力测试、简易相关性"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def portfolio_overview(
    total_equity: Optional[float] = None,
    max_portfolio_pct: float = 0.80,
    max_symbol_pct: float = 0.25,
) -> Dict[str, Any]:
    """汇总占用、余量、超限标的。

    净值参考优先级:
      1) 入参 / 设置 total_equity > 0
      2) 启用标的 max_capital 之和 > 0
      3) 都没有 → equity 未知,不计算利用率%(避免除以 1 炸出千万级百分比)
    """
    from app.data import wheel_repository as repo

    usage = repo.get_capital_usage()
    targets = repo.get_targets()
    enabled = [t for t in targets if t.get("enabled")]

    total_committed = float(usage.get("total_committed") or 0)
    caps_sum = sum(float(t.get("max_capital") or 0) for t in enabled)

    equity: Optional[float] = None
    equity_source = "unknown"
    notes: List[str] = []

    if total_equity is not None and float(total_equity) > 0:
        equity = float(total_equity)
        equity_source = "config"  # 设置页 wheel_portfolio.total_equity
    elif caps_sum > 0:
        equity = caps_sum
        equity_source = "max_capital_sum"
        notes.append("净值参考=各启用标的 max_capital 之和(未单独配置组合净值)")
    else:
        equity = None
        equity_source = "unknown"
        notes.append(
            "未配置组合净值且标的 max_capital 均为 0:无法计算利用率/净值%。"
            "请在设置→组合风控填写「组合净值」,或在标的设置为各标的填写 max_capital"
        )

    has_equity = equity is not None and equity > 0
    util = (total_committed / equity) if has_equity else None
    per = usage.get("per_symbol") or {}
    # 无占用的启用标的也要列出
    all_syms = {t["symbol"] for t in enabled} | set(per.keys())
    target_by = {t["symbol"]: t for t in targets}

    over_symbol = []
    symbol_rows = []
    for sym in all_syms:
        t = target_by.get(sym) or {"symbol": sym, "max_capital": 0, "enabled": 1}
        if not t.get("enabled") and sym not in per:
            continue
        u = per.get(sym, {})
        committed = float(u.get("csp_collateral") or 0) + float(u.get("holding_cost") or 0)
        cap = float(t.get("max_capital") or 0)
        # 上限 0 = 未设限额,不计算余量、不因「超上限」告警
        headroom = (cap - committed) if cap > 0 else None
        pct_eq = (committed / equity * 100) if has_equity and equity else None
        over_cap = bool(cap > 0 and committed > cap + 1e-6)
        # 仅在有真实净值时做「占净值%」超限
        over_pct = bool(has_equity and pct_eq is not None and pct_eq > max_symbol_pct * 100)
        row = {
            "symbol": sym,
            "committed": round(committed, 2),
            "csp_collateral": round(float(u.get("csp_collateral") or 0), 2),
            "holding_cost": round(float(u.get("holding_cost") or 0), 2),
            "max_capital": cap,
            "headroom": round(headroom, 2) if headroom is not None else None,
            "headroom_ratio": round(headroom / cap, 3) if cap > 0 and headroom is not None else None,
            "pct_of_equity": round(pct_eq, 2) if pct_eq is not None else None,
            "over_symbol_cap": over_cap,
            "over_symbol_pct": over_pct,
            "cap_unset": cap <= 0,
        }
        symbol_rows.append(row)
        if over_cap or over_pct:
            over_symbol.append(row)

    symbol_rows.sort(key=lambda x: x["committed"], reverse=True)
    idle_cash = round(equity - total_committed, 2) if has_equity else None
    idle_pct = round(idle_cash / equity * 100, 2) if has_equity and equity and idle_cash is not None else None
    over_portfolio = bool(has_equity and util is not None and util > max_portfolio_pct)

    if any(r.get("cap_unset") and r["committed"] > 0 for r in symbol_rows):
        notes.append("部分有占用的标的未设 max_capital(上限显示 0):余量无法计算,不计入「超标的上限」")

    return {
        "equity": round(equity, 2) if has_equity else None,
        "equity_source": equity_source,
        "equity_configured": equity_source == "config",
        "total_committed": round(total_committed, 2),
        "csp_collateral": usage.get("csp_collateral"),
        "holding_cost": usage.get("holding_cost"),
        "utilization_pct": round(util * 100, 2) if util is not None else None,
        "max_portfolio_pct": max_portfolio_pct * 100,
        "max_symbol_pct": max_symbol_pct * 100,
        "over_portfolio": over_portfolio,
        "idle_cash": idle_cash,
        "idle_pct": idle_pct,
        "per_symbol": symbol_rows,
        "violations": over_symbol,
        "assignment_stress": usage.get("assignment_stress"),
        "notes": notes,
        "ok": has_equity,  # 前端可用:净值是否可计算
    }


def _daily_returns(symbol: str, limit: int = 60) -> List[float]:
    from app.core.volatility import get_daily_closes
    closes = get_daily_closes(symbol, limit=limit + 1)
    if len(closes) < 10:
        return []
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    return rets


def _corr(a: List[float], b: List[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / n
    va = sum((x - ma) ** 2 for x in a) / n
    vb = sum((x - mb) ** 2 for x in b) / n
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def correlation_matrix(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    from app.data import wheel_repository as repo

    if not symbols:
        symbols = [t["symbol"] for t in repo.get_targets() if t.get("enabled")]
    rets = {s: _daily_returns(s) for s in symbols}
    pairs: List[Dict[str, Any]] = []
    high: List[Dict[str, Any]] = []
    for i, sa in enumerate(symbols):
        for sb in symbols[i + 1:]:
            c = _corr(rets.get(sa, []), rets.get(sb, []))
            if c is None:
                continue
            row = {"a": sa, "b": sb, "corr": round(c, 3)}
            pairs.append(row)
            if c >= 0.7:
                high.append(row)
    pairs.sort(key=lambda x: abs(x["corr"]), reverse=True)
    return {"symbols": symbols, "pairs": pairs[:50], "high_corr": high}


def stress_test(
    shocks: Optional[List[float]] = None,
    total_equity: Optional[float] = None,
) -> Dict[str, Any]:
    """对在场 CSP:标的下跌 shock 后 ITM 数量与接货资金。"""
    from app.data import wheel_repository as repo
    from app.core.volatility import get_daily_closes

    shocks = shocks or [-0.10, -0.20]
    cycles = [
        c for c in repo.get_cycles(include_closed=False)
        if c["status"] == "CSP_OPEN" and c.get("open_strike")
    ]
    holdings = [
        c for c in repo.get_cycles(include_closed=False)
        if (c.get("shares") or 0) > 0
    ]

    # spot 用本地日K最后收盘
    spots: Dict[str, float] = {}
    for c in cycles + holdings:
        sym = c["symbol"]
        if sym not in spots:
            cl = get_daily_closes(sym, limit=5)
            spots[sym] = cl[-1] if cl else 0.0

    usage = repo.get_capital_usage()
    base_holding = usage.get("holding_cost") or 0
    scenarios = []
    for sh in shocks:
        assign_cost = 0.0
        itm_list = []
        for c in cycles:
            spot = spots.get(c["symbol"]) or 0
            shocked = spot * (1 + sh)
            strike = c["open_strike"] or 0
            qty = c.get("open_qty") or 1
            size = c.get("open_contract_size") or 100
            if shocked < strike:
                cost = strike * qty * size
                assign_cost += cost
                itm_list.append({
                    "symbol": c["symbol"],
                    "cycle_id": c["id"],
                    "strike": strike,
                    "spot_shocked": round(shocked, 2),
                    "assign_cost": round(cost, 2),
                })
        # 持股市值下跌(仅展示浮亏粗估)
        holding_mtm = 0.0
        for c in holdings:
            spot = spots.get(c["symbol"]) or 0
            holding_mtm += (c.get("shares") or 0) * spot * (1 + sh)

        total_need = assign_cost + base_holding
        scenarios.append({
            "shock_pct": round(sh * 100, 1),
            "csp_itm_count": len(itm_list),
            "assign_capital_needed": round(assign_cost, 2),
            "holding_mtm": round(holding_mtm, 2),
            "total_capital_if_assigned": round(total_need, 2),
            "itm_positions": itm_list,
        })

    equity = total_equity
    if not equity:
        targets = [t for t in repo.get_targets() if t.get("enabled")]
        equity = sum((t.get("max_capital") or 0) for t in targets) or 0

    return {
        "spots_used": {k: round(v, 2) for k, v in spots.items()},
        "open_csp_count": len(cycles),
        "equity_ref": round(equity, 2) if equity else None,
        "scenarios": scenarios,
        "note": "spot 取本地日K收盘;实际请结合实时报价。同时 assign 为极端情景。",
    }


def headroom_ratio_for_symbol(symbol: str) -> Optional[float]:
    from app.data import wheel_repository as repo
    t = repo.get_target(symbol)
    if not t or not (t.get("max_capital") or 0):
        return None
    usage = repo.get_capital_usage()["per_symbol"].get(symbol, {})
    committed = (usage.get("csp_collateral") or 0) + (usage.get("holding_cost") or 0)
    cap = t["max_capital"]
    return max(0.0, (cap - committed) / cap)
