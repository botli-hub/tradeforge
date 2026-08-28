"""账户权益:现金 + 持股市值 + 期权盯市。

设置里的 total_equity 只作「起始现金」,不再当死净值。
登记成交改现金;股价/权利金变动改市值。
不写回配置、不下单。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

OPT_IN = ("SELL_PUT", "SELL_CALL")
OPT_OUT = ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def trade_cashflow(t: Dict[str, Any]) -> float:
    """一笔登记对现金的影响。卖权收权利金为正;接货/买股为负。"""
    tt = str(t.get("trade_type") or "")
    qty = _f(t.get("qty"), 1.0) or 1.0
    price = _f(t.get("price"))
    fee = _f(t.get("fee"))
    size = _f(t.get("contract_size"), 100.0) or 100.0
    strike = _f(t.get("strike"))
    if tt in OPT_IN:
        return qty * price * size - fee
    if tt in OPT_OUT:
        return -(qty * price * size + fee)
    if tt == "BUY_SHARES":
        return -(qty * price + fee)
    if tt == "SELL_SHARES":
        return qty * price - fee
    if tt == "ASSIGNED":
        return -(strike * qty * size) - fee
    if tt == "CALLED_AWAY":
        return strike * qty * size - fee
    if tt == "EXPIRE":
        return -fee
    return 0.0


def _option_mark(cycle: Dict[str, Any], option_marks: Dict[str, float]) -> Optional[float]:
    code = str(cycle.get("open_contract_code") or "")
    if code and code in option_marks:
        m = _f(option_marks.get(code))
        if m > 0:
            return m
    last = _f(cycle.get("current_price") or cycle.get("open_price"))
    return last if last > 0 else None


def nav_from_books(
    starting_cash: float,
    trades: Iterable[Dict[str, Any]],
    cycles: Iterable[Dict[str, Any]],
    *,
    spots: Optional[Dict[str, float]] = None,
    option_marks: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """纯函数:起始现金 + 流水 + 持仓市值。

    空头期权盯市为负(负债)。无行情时持股退成本、期权退开仓价,
    这样刚登记时权利金与盯市对冲,权益不虚增。
    """
    spots = spots or {}
    option_marks = option_marks or {}
    flows: List[Dict[str, Any]] = []
    cash_delta = 0.0
    for t in trades:
        cf = trade_cashflow(t)
        cash_delta += cf
        flows.append({
            "trade_type": t.get("trade_type"),
            "symbol": t.get("symbol"),
            "cashflow": round(cf, 2),
        })
    cash = float(starting_cash or 0) + cash_delta

    stock_mv = 0.0
    stock_cost = 0.0
    option_mtm = 0.0
    csp_collateral = 0.0
    stock_rows: List[Dict[str, Any]] = []
    option_rows: List[Dict[str, Any]] = []
    per_symbol: Dict[str, Dict[str, float]] = {}

    def _row(sym: str) -> Dict[str, float]:
        return per_symbol.setdefault(sym, {
            "csp_collateral": 0.0, "holding_cost": 0.0, "holding_mv": 0.0, "option_mtm": 0.0,
        })

    for c in cycles:
        status = str(c.get("status") or "")
        if status == "CLOSED":
            continue
        sym = str(c.get("symbol") or "")
        u = _row(sym)
        shares = _f(c.get("shares"))
        cost = _f(c.get("share_cost"))
        if shares > 0:
            px = _f(spots.get(sym))
            mark = px if px > 0 else cost
            mv = shares * mark
            stock_mv += mv
            stock_cost += shares * cost
            u["holding_mv"] += mv
            u["holding_cost"] += shares * cost
            stock_rows.append({
                "symbol": sym,
                "shares": shares,
                "spot": round(mark, 4) if mark else None,
                "mv": round(mv, 2),
                "cost": round(shares * cost, 2),
                "spot_fallback": not (px > 0),
            })
        if status == "CSP_OPEN" and _f(c.get("open_strike")) > 0:
            qty = _f(c.get("open_qty"), 1.0) or 1.0
            size = _f(c.get("open_contract_size") or c.get("contract_size"), 100.0) or 100.0
            coll = _f(c.get("open_strike")) * qty * size
            csp_collateral += coll
            u["csp_collateral"] += coll
        if status in ("CSP_OPEN", "CC_OPEN"):
            qty = _f(c.get("open_qty"), 1.0) or 1.0
            size = _f(c.get("open_contract_size") or c.get("contract_size"), 100.0) or 100.0
            mark = _option_mark(c, option_marks)
            if mark is None:
                mark = _f(c.get("open_price"))
            mtm = -mark * qty * size
            option_mtm += mtm
            u["option_mtm"] += mtm
            option_rows.append({
                "symbol": sym,
                "side": c.get("open_option_type"),
                "contract_code": c.get("open_contract_code"),
                "qty": qty,
                "mark": round(mark, 4) if mark else None,
                "mtm": round(mtm, 2),
            })

    for u in per_symbol.values():
        for k in list(u):
            u[k] = round(u[k], 2)

    equity = cash + stock_mv + option_mtm
    total_committed = csp_collateral + stock_mv
    return {
        "starting_cash": round(float(starting_cash or 0), 2),
        "cash": round(cash, 2),
        "cash_delta": round(cash_delta, 2),
        "stock_mv": round(stock_mv, 2),
        "stock_cost": round(stock_cost, 2),
        "holding_mv": round(stock_mv, 2),
        "holding_cost": round(stock_cost, 2),
        "option_mtm": round(option_mtm, 2),
        "csp_collateral": round(csp_collateral, 2),
        "total_committed": round(total_committed, 2),
        "idle_cash": round(cash - csp_collateral, 2),
        "equity": round(equity, 2),
        "equity_source": "nav",
        "per_symbol": per_symbol,
        "stock_rows": stock_rows,
        "option_rows": option_rows,
    }


def load_nav_marks(symbols: Optional[Iterable[str]] = None) -> Tuple[Dict[str, float], Dict[str, float]]:
    """现价:体检缓存优先,其次本地日K。期权:体检缓存 last/ask。"""
    spots: Dict[str, float] = {}
    option_marks: Dict[str, float] = {}
    try:
        from app.core.wheel_today import load_positions_cache
        cached = load_positions_cache(max_age_min=24 * 60)
        items = ((cached or {}).get("data") or {}).get("items") or []
        for it in items:
            sym = str(it.get("symbol") or "")
            sp = _f(it.get("spot"))
            if sym and sp > 0:
                spots[sym] = sp
            code = str(it.get("contract_code") or "")
            mark = _f(it.get("current_price") or it.get("buyback_ask"))
            if code and mark > 0:
                option_marks[code] = mark
    except Exception as e:
        logger.debug("nav marks cache: %s", e)

    need = {str(s) for s in (symbols or []) if s} - set(spots)
    if need:
        try:
            from app.core.volatility import get_daily_closes
            for sym in need:
                try:
                    cl = get_daily_closes(sym, limit=5)
                    if cl and float(cl[-1]) > 0:
                        spots[sym] = float(cl[-1])
                except Exception:
                    continue
        except Exception as e:
            logger.debug("nav marks closes: %s", e)
    return spots, option_marks


def compute_account_nav(
    starting_cash: float = 0.0,
    *,
    spots: Optional[Dict[str, float]] = None,
    option_marks: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """读台账 + 行情,算当前权益。"""
    from app.data import wheel_repository as repo
    from app.data.database import get_db

    conn = get_db()
    try:
        trades = [dict(r) for r in conn.execute(
            "SELECT * FROM wheel_trades ORDER BY traded_at, created_at"
        ).fetchall()]
    finally:
        conn.close()
    cycles = repo.get_cycles(include_closed=True)
    live = [c for c in cycles if str(c.get("status") or "") != "CLOSED"]
    symbols = {c.get("symbol") for c in live if c.get("symbol")}
    auto_spots, auto_opts = load_nav_marks(symbols)
    if spots:
        auto_spots.update({k: float(v) for k, v in spots.items() if v is not None})
    if option_marks:
        auto_opts.update({k: float(v) for k, v in option_marks.items() if v is not None})
    nav = nav_from_books(
        starting_cash, trades, live,
        spots=auto_spots, option_marks=auto_opts,
    )
    nav["spots_used"] = {k: round(v, 4) for k, v in auto_spots.items()}
    nav["option_marks_used"] = {k: round(v, 4) for k, v in auto_opts.items()}
    return nav


def compute_nav(
    starting_cash: float,
    trades: Iterable[Dict[str, Any]],
    cycles: Iterable[Dict[str, Any]],
    spots: Optional[Dict[str, float]] = None,
    option_marks: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """portfolio_overview 用的别名。"""
    return nav_from_books(
        starting_cash, trades, cycles,
        spots=spots, option_marks=option_marks,
    )


def resolve_spots(
    symbols: Iterable[str],
    given: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """合并传入现价 + 缓存/日K。返回 (spots, source_by_symbol)。"""
    auto, _opts = load_nav_marks(symbols)
    src: Dict[str, str] = {k: "close_or_cache" for k in auto}
    if given:
        for k, v in given.items():
            if v is None:
                continue
            auto[str(k)] = float(v)
            src[str(k)] = "quote"
    return auto, src
