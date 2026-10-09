"""用持仓决策树重放已归档的日频期权报价。

入场口径接近历史回测;出场改走 decide_position。
结果始终 validated_edge=False,不能当成已验证优势。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.wheel_backtest import BTParams, _bars
from app.core.wheel_decision import decide_position


def _parse_quotes(quotes: List[Dict[str, Any]]) -> Dict[date, List[Dict[str, Any]]]:
    chain: Dict[date, List[Dict[str, Any]]] = {}
    for q in quotes or []:
        day = date.fromisoformat(str(q["date"])[:10])
        side = str(q["side"]).upper()
        bid, ask = float(q["bid"]), float(q["ask"])
        strike = float(q["strike"])
        expiry = date.fromisoformat(str(q["expiry"])[:10])
        delta = abs(float(q["delta"]))
        code = str(q.get("contract_code") or "").strip()
        if side not in ("PUT", "CALL") or not code:
            raise ValueError("历史期权报价无效")
        if not all(math.isfinite(v) for v in (bid, ask, strike, delta)):
            raise ValueError("历史期权报价无效")
        if bid < 0 or ask < bid or strike <= 0 or not 0 <= delta <= 1:
            raise ValueError("历史期权报价无效")
        chain.setdefault(day, []).append({
            "contract_code": code,
            "side": side,
            "bid": bid,
            "ask": ask,
            "strike": strike,
            "expiry": expiry,
            "delta": delta,
        })
    return chain


def _pick_entry(day_chain, day, spot, side, p, shares, cost, cash, qty, fee):
    valid = []
    for q in day_chain:
        if q["side"] != side or q["bid"] <= 0 or q["expiry"] <= day:
            continue
        dte = (q["expiry"] - day).days
        if abs(dte - p.dte) > 7 or abs(q["delta"] - p.delta) > 0.05:
            continue
        if side == "PUT" and q["strike"] > spot * p.floor_pct:
            continue
        if side == "CALL" and q["strike"] < cost:
            continue
        ann = (q["bid"] * qty - fee) / (q["strike"] * qty) * 365 / max(dte, 1) * 100
        funded = cash >= q["strike"] * qty + fee if side == "PUT" else shares >= qty and cash >= fee
        if ann < p.min_annualized or not funded:
            continue
        valid.append(q)
    if not valid:
        return None
    return min(valid, key=lambda q: (abs(q["delta"] - p.delta), q["contract_code"]))


def _roll_candidate(day_chain, leg, day, ask_close):
    """同方向、更远到期、卖价高于买回价的权利金候选。"""
    best = None
    for q in day_chain:
        if q["side"] != leg["side"] or q["contract_code"] == leg["contract_code"]:
            continue
        if q["expiry"] <= leg["expiry"] or q["bid"] <= ask_close:
            continue
        if best is None or q["expiry"] < best["expiry"]:
            best = q
    return best


def replay_with_decision_tree(
    rows: List[Dict[str, Any]],
    quotes: Optional[List[Dict[str, Any]]],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """历史报价 + 决策树。缺快照的交易日直接失败,不合成价格。"""
    if quotes is None:
        return {
            "ok": False,
            "error": "决策树重放需要历史合约 bid/ask",
            "validated_edge": False,
        }
    raw = params or {}
    p = BTParams(**{k: v for k, v in raw.items() if k in BTParams.__dataclass_fields__})
    try:
        p.validate()
        bars = _bars(rows)
        chain = _parse_quotes(quotes)
    except Exception as e:
        return {"ok": False, "error": str(e), "validated_edge": False}
    if len(bars) <= p.warmup_bars:
        return {"ok": False, "error": "日线不足预热长度", "validated_edge": False}

    cash, shares, cost = p.initial_capital, 0, 0.0
    leg = None
    trades: List[Dict[str, Any]] = []
    qty = p.contracts * p.contract_size
    fee = p.contracts * p.fee_per_contract
    min_ann = float(raw.get("min_annualized", p.min_annualized) or 0)
    profit_target = float(raw.get("profit_target_pct", p.profit_take * 100) or 50)

    def book(day, kind, flow, **details):
        trades.append({
            "date": day.isoformat(),
            "type": kind,
            "cashflow": round(flow, 2),
            **details,
        })

    for i in range(p.warmup_bars, len(bars)):
        day, spot = bars[i]["date"], bars[i]["close"]
        day_chain = chain.get(day)
        if not day_chain:
            return {
                "ok": False,
                "error": f"{day} 缺少历史合约快照",
                "missing_date": day.isoformat(),
                "validated_edge": False,
            }
        if leg and day >= leg["expiry"]:
            if day != leg["expiry"]:
                return {"ok": False, "error": "缺少到期日标的收盘价", "validated_edge": False}
            intrinsic = max(leg["strike"] - spot, 0) if leg["side"] == "PUT" else max(spot - leg["strike"], 0)
            if intrinsic > 0 and leg["side"] == "PUT":
                flow = -leg["strike"] * qty
                cash += flow
                shares = qty
                cost = leg["strike"]
                book(day, "ASSIGNED", flow)
            elif intrinsic > 0:
                flow = leg["strike"] * qty
                cash += flow
                shares = 0
                book(day, "CALLED_AWAY", flow)
            else:
                book(day, "EXPIRE", 0)
            leg = None
        elif leg:
            quote = next((q for q in day_chain if q["contract_code"] == leg["contract_code"]), None)
            if quote is None:
                return {
                    "ok": False,
                    "error": f"{day} 缺少在场合约 {leg['contract_code']} 报价",
                    "validated_edge": False,
                }
            dte = (leg["expiry"] - day).days
            itm = spot < leg["strike"] if leg["side"] == "PUT" else spot > leg["strike"]
            profit = None
            if leg["premium"] > 0:
                profit = round((leg["premium"] - quote["ask"]) / leg["premium"] * 100, 1)
            decision = decide_position({
                "side": leg["side"],
                "strike": leg["strike"],
                "spot": spot,
                "dte": dte,
                "delta": quote["delta"],
                "itm": itm,
                "open_price": leg["premium"],
                "buyback_ask": quote["ask"],
                "current_price": quote["ask"],
                "profit_pct": profit,
                "floor_price": spot * p.floor_pct if leg["side"] == "PUT" else None,
                "stance": "acquire",
                "qty": p.contracts,
                "contract_size": p.contract_size,
            }, min_ann, profit_target)
            code = decision.get("action_code")
            if code in ("CLOSE", "REPLACE"):
                flow = -quote["ask"] * qty - fee
                cash += flow
                book(day, "BUY_" + leg["side"] + "_CLOSE", flow, action=code)
                leg = None
            elif code == "ROLL":
                nxt = _roll_candidate(day_chain, leg, day, quote["ask"])
                if nxt is not None:
                    close_flow = -quote["ask"] * qty - fee
                    open_flow = nxt["bid"] * qty - fee
                    cash += close_flow + open_flow
                    book(day, "ROLL_CLOSE", close_flow)
                    book(day, "ROLL_OPEN", open_flow, contract_code=nxt["contract_code"])
                    leg = {
                        "contract_code": nxt["contract_code"],
                        "side": nxt["side"],
                        "strike": nxt["strike"],
                        "expiry": nxt["expiry"],
                        "premium": nxt["bid"],
                    }
        if leg is None and i < len(bars) - 1:
            side = "CALL" if shares else "PUT"
            chosen = _pick_entry(day_chain, day, spot, side, p, shares, cost, cash, qty, fee)
            if chosen:
                flow = chosen["bid"] * qty - fee
                cash += flow
                leg = {
                    "contract_code": chosen["contract_code"],
                    "side": side,
                    "strike": chosen["strike"],
                    "expiry": chosen["expiry"],
                    "premium": chosen["bid"],
                }
                book(day, "SELL_" + side, flow, contract_code=chosen["contract_code"], strike=chosen["strike"])

    final = cash + shares * bars[-1]["close"]
    return {
        "ok": True,
        "validated_edge": False,
        "evidence_level": "decision_tree_replay",
        "trade_count": len(trades),
        "trades": trades,
        "final_equity": round(final, 2),
        "cash": round(cash, 2),
        "shares": shares,
        "note": "决策树重放使用归档报价;未验证策略优势,不含提前行权与财报路径。",
    }
