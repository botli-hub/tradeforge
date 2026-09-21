"""One post-trade risk calculation for previews and every registration path."""
import json
from app.core.wheel_nav import nav_from_books


def evaluate_books(nav, targets, config):
    pc = config.get("wheel_portfolio") or {}
    violations = []
    equity, cash = nav["equity"], nav["cash"]
    reserve = float(pc.get("cash_reserve", 0) or 0)
    collateral = nav["csp_collateral"]
    cash_gap = max(0.0, collateral + reserve - cash)
    if nav["starting_cash"] <= 0:
        violations.append("未配置起始现金")
    if equity <= 0:
        violations.append("权益非正")
    if cash_gap > 0.005:
        violations.append(f"全部 Put 接货后现金缺口 ${cash_gap:.2f}")
    cap_pct = float(pc.get("max_portfolio_pct", 0.8))
    if equity > 0 and nav["total_committed"] > equity * cap_pct + 0.005:
        violations.append("交易后组合占用超限")
    target_map = {x["symbol"]: x for x in targets}
    sectors = {}
    for sym, row in nav["per_symbol"].items():
        target = target_map.get(sym, {})
        used = row["csp_collateral"] + row["holding_mv"]
        cap = float(target.get("max_capital") or 0)
        if cap > 0 and used > cap + 0.005:
            violations.append(f"{sym} 交易后资金上限超限")
        if equity > 0 and used > equity * float(pc.get("max_symbol_pct", .25)) + .005:
            violations.append(f"{sym} 交易后权益占比超限")
        sector = target.get("sector") or (pc.get("sector_by_symbol") or {}).get(sym)
        if sector:
            sectors[sector] = sectors.get(sector, 0) + used
    for sector, used in sectors.items():
        if equity > 0 and used > equity * float(pc.get("max_sector_pct", .5)) + .005:
            violations.append(f"{sector} 接货后板块集中度超限")
    if nav.get("valuation_incomplete"):
        violations.append("持仓估值缺失/过期,须刷新后计划新交易")
    if nav.get("reconciliation_required"):
        violations.append("历史台账需核对")
    return {"ok": not violations, "violations": violations,
        "assignment_cash_required": round(collateral, 2),
        "assignment_cash_shortfall": round(cash_gap, 2),
        "post_assignment_cash": round(cash - collateral, 2),
        "equity": equity, "cash": cash, "total_committed": nav["total_committed"],
        "sectors": sectors}


def check_books(conn):
    from app.data.wheel_repository import _enrich_cycle
    row = conn.execute("SELECT value FROM app_kv WHERE key='backend_config'").fetchone()
    config = json.loads(row["value"]) if row and row["value"] else {}
    starting = float((config.get("wheel_portfolio") or {}).get("total_equity", 0) or 0)
    trades = [dict(r) for r in conn.execute("SELECT * FROM wheel_trades")]
    cycles = [_enrich_cycle(dict(r)) for r in conn.execute("SELECT * FROM wheel_cycles")]
    targets = [dict(r) for r in conn.execute("SELECT * FROM wheel_targets")]
    # Use the same connection/snapshot; no network I/O while holding write lock.
    cache = conn.execute("SELECT value FROM app_kv WHERE key='open_positions_cache_v1'").fetchone()
    spots, marks = {}, {}
    if cache:
        from app.core.wheel_quotes import quote_is_fresh
        try:
            payload = json.loads(cache["value"])
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if quote_is_fresh(payload.get("saved_at") or payload.get("cached_at")):
            for it in (payload.get("data") or {}).get("items", []):
                try:
                    spot = float(it.get("spot") or 0)
                    mark = float(it.get("buyback_ask") or 0)
                except (TypeError, ValueError):
                    continue
                if spot > 0 and it.get("symbol"):
                    spots[it["symbol"]] = spot
                if mark > 0 and it.get("contract_code"):
                    marks[it["contract_code"]] = mark
    nav = nav_from_books(starting, trades, cycles, spots=spots, option_marks=marks)
    result = evaluate_books(nav, targets, config)
    # Per-trade willingness is checked in addition to portfolio capacity.
    for c in cycles:
        target = next((t for t in targets if t["symbol"] == c["symbol"]), {})
        if c.get("open_option_type") == "PUT":
            floor = target.get("floor_price")
            try:
                from app.core.wheel_floor import resolve_willing_price
                floor = resolve_willing_price(
                    c["symbol"], None, None, floor,
                )
            except Exception:
                pass
            if floor is None or c["open_strike"] > floor:
                result["violations"].append(f"{c['symbol']} 未设置愿接价或 Put 超愿接价")
            if target.get("stance") == "income":
                result["violations"].append(f"{c['symbol']} 不愿接货")
    result["ok"] = not result["violations"]
    return result


def candidate_risk(nav, opportunity, targets, config):
    """Projected cash/obligations for one suggested order, independent of ranking."""
    from copy import deepcopy
    projected = deepcopy(nav)
    sym = opportunity.get('symbol')
    target = next((t for t in targets if t['symbol'] == sym), {})
    side = opportunity.get('side', 'PUT')
    qty = float(opportunity.get('suggest_qty') or opportunity.get('qty') or 1)
    size = float(opportunity.get('contract_size') or 100)
    strike = float(opportunity.get('strike') or 0)
    bid = float(opportunity.get('bid') or 0)
    ask = float(opportunity.get('ask') or 0)
    fee = float((config.get('wheel_portfolio') or {}).get('fee_per_contract', .65)) * qty
    errors = []
    if strike <= 0 or qty <= 0 or qty != int(qty) or size <= 0:
        errors.append('合约字段或数量无效')
    from app.core.wheel_quotes import executable_quote
    max_spread = float((config.get("wheel_scan") or {}).get("max_spread_pct", 8) or 8)
    if not executable_quote(opportunity, max_spread_pct=max_spread):
        errors.append('报价不可执行')
    if side == 'PUT':
        floor = target.get('floor_price')
        try:
            from app.core.wheel_floor import resolve_willing_price
            floor = resolve_willing_price(sym, None, None, floor)
        except Exception:
            pass
        if floor is None or strike > float(floor):
            errors.append('Put 超愿接价或愿接价未设置')
        if target.get('stance') == 'income' or not target.get('enabled', True):
            errors.append('标的禁止新 Put')
        collateral = strike*qty*size
        projected['csp_collateral'] += collateral
        projected['total_committed'] += collateral
        row = projected['per_symbol'].setdefault(sym, {'csp_collateral':0,'holding_mv':0,'holding_cost':0,'option_mtm':0})
        row['csp_collateral'] += collateral
    elif side == 'CALL':
        from app.data import wheel_repository as repo
        from app.core.wheel_cc_legs import uncovered_shares_of
        cycle = repo.get_cycle(opportunity.get('cycle_id')) if opportunity.get('cycle_id') else None
        if not cycle or uncovered_shares_of(cycle) < qty*size:
            errors.append('未指定足额备兑持股轮子')
        if target.get('sell_above') and strike < target['sell_above']:
            errors.append('Call 低于愿卖价')
    else:
        errors.append('未知方向')
    projected['cash'] += bid*qty*size-fee
    projected['equity'] += (bid-ask)*qty*size-fee  # conservative immediately-close mark
    result = evaluate_books(projected,targets,config)
    result['violations'] += errors
    result['ok'] = not result['violations']
    return result
