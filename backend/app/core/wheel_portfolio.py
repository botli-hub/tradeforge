"""组合层资金配置、压力测试、简易相关性"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def portfolio_overview(
    total_equity: Optional[float] = None,
    max_portfolio_pct: float = 0.80,
    max_symbol_pct: float = 0.25,
    spots: Optional[Dict[str, float]] = None,
    option_marks: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """权益 = 现金 + 持股市值 + 期权盯市。

    起始现金只接受显式入参或设置中的 total_equity；标的上限不是现金余额。
    """
    from app.data import wheel_repository as repo
    from app.core.wheel_nav import compute_account_nav

    targets = repo.get_targets()
    enabled = [t for t in targets if t.get("enabled")]

    notes: List[str] = []
    starting = 0.0
    starting_source = "zero"
    if total_equity is not None and float(total_equity) > 0:
        starting = float(total_equity)
        starting_source = "config"
    else:
        notes.append(
            "未配置起始现金且标的 max_capital 均为 0:权益只随已登记成交和市值走。"
            "请在设置→组合风控填写「起始现金」"
        )

    nav = compute_account_nav(starting, spots=spots, option_marks=option_marks)
    equity = nav["equity"]
    has_equity = starting > 0 and equity > 0
    live_n = sum(1 for _ in (nav.get("per_symbol") or {}))
    if starting <= 0 and not nav.get("cash_delta") and live_n == 0 and equity == 0:
        has_equity = False
        notes.append("无起始现金、无持仓:无法计算利用率")

    total_committed = float(nav["total_committed"] or 0)
    util = (total_committed / equity) if has_equity and equity else None
    per_nav = nav.get("per_symbol") or {}
    resolved_spots = nav.get("spots_used") or {}
    all_syms = {t["symbol"] for t in enabled} | set(per_nav.keys())
    target_by = {t["symbol"]: t for t in targets}

    over_symbol = []
    symbol_rows = []
    for sym in all_syms:
        t = target_by.get(sym) or {"symbol": sym, "max_capital": 0, "enabled": 1}
        if not t.get("enabled") and sym not in per_nav:
            continue
        u = per_nav.get(sym, {})
        committed = float(u.get("csp_collateral") or 0) + float(u.get("holding_mv") or 0)
        cap = float(t.get("max_capital") or 0)
        headroom = (cap - committed) if cap > 0 else None
        pct_eq = (committed / equity * 100) if has_equity and equity else None
        over_cap = bool(cap > 0 and committed > cap + 1e-6)
        over_pct = bool(has_equity and pct_eq is not None and pct_eq > max_symbol_pct * 100)
        row = {
            "symbol": sym,
            "committed": round(committed, 2),
            "csp_collateral": round(float(u.get("csp_collateral") or 0), 2),
            "holding_cost": round(float(u.get("holding_cost") or 0), 2),
            "holding_mv": round(float(u.get("holding_mv") or 0), 2),
            "option_mtm": round(float(u.get("option_mtm") or 0), 2),
            "spot": resolved_spots.get(sym),
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
    idle_cash = nav["idle_cash"] if has_equity else None
    idle_pct = round(idle_cash / equity * 100, 2) if has_equity and equity and idle_cash is not None else None
    over_portfolio = bool(has_equity and util is not None and util > max_portfolio_pct)

    if any(r.get("cap_unset") and r["committed"] > 0 for r in symbol_rows):
        notes.append("部分有占用的标的未设 max_capital(上限显示 0):余量无法计算,不计入「超标的上限」")

    return {
        "equity": round(equity, 2) if has_equity else None,
        "equity_source": "nav" if has_equity else "unknown",
        "equity_configured": starting_source == "config",
        "starting_cash": nav["starting_cash"],
        "starting_cash_source": starting_source,
        "cash": nav["cash"] if has_equity else None,
        "stock_mv": nav["stock_mv"],
        "option_mtm": nav["option_mtm"],
        "total_committed": round(total_committed, 2),
        "csp_collateral": nav["csp_collateral"],
        "holding_cost": nav["holding_cost"],
        "holding_mv": nav["holding_mv"],
        "utilization_pct": round(util * 100, 2) if util is not None else None,
        "max_portfolio_pct": max_portfolio_pct * 100,
        "max_symbol_pct": max_symbol_pct * 100,
        "over_portfolio": over_portfolio,
        "idle_cash": idle_cash,
        "idle_pct": idle_pct,
        "per_symbol": symbol_rows,
        "violations": over_symbol,
        "assignment_stress": nav["csp_collateral"],
        "assignment_cash_shortfall": round(max(0, nav["csp_collateral"] - nav["cash"]), 2),
        "valuation_incomplete": nav.get("valuation_incomplete", False),
        "reconciliation_required": nav.get("reconciliation_required", False),
        "notes": notes,
        "ok": has_equity,
        "nav_formula": "cash + stock_mv + option_mtm",
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


def stress_test(shocks=None, total_equity=None):
    """Terminal common-shock payoff; cash coverage separate from equity losses."""
    from app.data import wheel_repository as repo
    from app.core.wheel_nav import compute_account_nav
    from app.core.wheel_cc_legs import expand_open_option_rows
    cycles = repo.get_cycles(include_closed=False)
    nav = compute_account_nav(float(total_equity or 0))
    spots = nav.get("spots_used") or {}
    missing = sorted({c["symbol"] for c in cycles if c["status"] != "IDLE" and not spots.get(c["symbol"])})
    scenarios = []
    for shock in shocks or [-.2, -.4]:
        if not -1 <= shock <= 0:
            raise ValueError("shock 必须在 [-1,0]")
        holding_mv = sum((c.get("shares") or 0) * spots.get(c["symbol"], 0) * (1 + shock) for c in cycles)
        need, intrinsic, assigned_mv = 0., 0., 0.
        positions = []
        for leg in expand_open_option_rows(cycles):
            sym = leg["symbol"]
            px, k = spots.get(sym, 0) * (1 + shock), float(leg.get("open_strike") or 0)
            units = float(leg.get("open_qty") or 0) * float(leg.get("open_contract_size") or 100)
            if k <= 0 or units <= 0:
                continue
            put = leg["open_option_type"] == "PUT"
            intrinsic += max(k-px if put else px-k,0)*units
            if put and px < k:
                need += k*units; assigned_mv += px*units
                positions.append({"symbol":sym,"cycle_id":leg["id"],"strike":k,"spot_shocked":px,"assign_cost":k*units})
        stressed = nav["cash"]+holding_mv-intrinsic
        scenarios.append({"shock_pct":shock*100,"csp_itm_count":len(positions),
            "assign_capital_needed":round(need,2),"assignment_cash_shortfall":round(max(0,need-nav["cash"]),2),
            "post_assignment_cash":round(nav["cash"]-need,2),"holding_mtm":round(holding_mv,2),
            "total_capital_if_assigned":round(holding_mv+assigned_mv,2),
            "stressed_equity":round(stressed,2) if not missing else None,
            "equity_loss":round(nav["equity"]-stressed,2) if not missing else None,
            "itm_positions":positions})
    return {"ok":not missing,"spots_used":spots,"missing_spots":missing,
        "open_csp_count":sum(c["status"]=="CSP_OPEN" for c in cycles),"equity_ref":nav["equity"],
        "scenarios":scenarios,"note":"同一价格冲击下的终端支付情景（期权按内在价值）；非到期前 IV/时间价值压力预测。现金缺口与权益损失分列。"}


def headroom_ratio_for_symbol(symbol: str) -> Optional[float]:
    ov = portfolio_overview()
    for row in ov.get("per_symbol") or []:
        if row.get("symbol") == symbol:
            return row.get("headroom_ratio")
    return None
