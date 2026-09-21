"""Validated, quantity-preserving Wheel ledger replay (no broker operations).

Stock inventory, the open put and each call are independent facts. Status is
only a projection. Realized P&L recognizes each disposed lot and every fee once.
"""
from copy import deepcopy
from datetime import date
import math

from app.core.wheel_cc_legs import same_contract, sync_primary_open


class LedgerError(ValueError):
    pass


def validate_trade(t):
    t = dict(t)
    tt = t.get("trade_type")
    for key, default in (("qty", 1), ("price", 0), ("fee", 0), ("contract_size", 100)):
        v = t.get(key)
        v = default if v is None else v
        try:
            v = float(v)
        except (ValueError, TypeError):
            raise LedgerError(f"{key} 必须是有限数字")
        if not math.isfinite(v) or v < 0 or (key in ("qty", "contract_size") and v <= 0):
            raise LedgerError(f"{key} 数值无效")
        if key == "contract_size" or (key == "qty" and tt not in ("BUY_SHARES", "SELL_SHARES")):
            if not v.is_integer():
                raise LedgerError(f"{key} 必须为正整数")
        t[key] = v
    if t.get("strike") is not None:
        try:
            k = float(t["strike"])
        except (ValueError, TypeError):
            raise LedgerError("strike 无效")
        if not math.isfinite(k) or k <= 0:
            raise LedgerError("strike 必须为正数")
        t["strike"] = k
    if t.get("expiry"):
        try:
            t["expiry"] = date.fromisoformat(str(t["expiry"])[:10]).isoformat()
        except ValueError:
            raise LedgerError("expiry 必须是有效日期")
    if tt in ("SELL_PUT", "SELL_CALL") and (not t.get("strike") or not t.get("expiry")):
        raise LedgerError(f"{tt} 需要 strike 和 expiry")
    return t


def _put(s):
    if s.get("open_option_type") == "PUT" and s.get("open_qty", 0) > 0:
        return {k: s.get("open_" + k) for k in ("contract_code", "strike", "expiry", "qty", "price", "contract_size")}
    return None


def _match(t, leg):
    identified = t.get("contract_code") or (t.get("strike") is not None and t.get("expiry"))
    if identified and not same_contract(t, leg):
        raise LedgerError("关闭合约与在场合约不匹配")
    if t.get("strike") is not None and abs(t["strike"] - float(leg["strike"])) > 1e-8:
        raise LedgerError("关闭腿 strike 与开仓腿不一致")
    if t.get("expiry") and t["expiry"] != leg["expiry"]:
        raise LedgerError("关闭腿到期日与开仓腿不一致")
    if t["contract_size"] != leg["contract_size"]:
        raise LedgerError("合约乘数与开仓腿不一致")
    if t["qty"] > leg["qty"] + 1e-9:
        raise LedgerError("平仓/指派数量超过在场合约数量")
    t.update(strike=leg["strike"], expiry=leg["expiry"], contract_code=leg.get("contract_code"))


def apply_trade(state, raw):
    """Return canonical trade; failed validation never mutates state."""
    s = deepcopy(state)
    t = validate_trade(raw)
    tt, qty, price, fee, size = (t[k] for k in ("trade_type", "qty", "price", "fee", "contract_size"))
    if s["status"] == "CLOSED":
        raise LedgerError("这个轮子已经结束结算,请新开轮子")
    for key in ("stock_realized", "option_realized", "cash_balance"):
        s.setdefault(key, 0.0)
    legs = s.setdefault("open_cc_legs", [])
    put = _put(s)
    cash = 0.0
    if tt == "BUY_SHARES":
        if s["status"] != "IDLE" or price <= 0:
            raise LedgerError("BUY_SHARES 需要空仓状态和正数价格")
        s.update(shares=qty, share_cost=price)
        cash = -qty * price
    elif tt == "SELL_SHARES":
        covered = sum(l["qty"] * l["contract_size"] for l in legs)
        if price <= 0 or qty > s["shares"] - covered + 1e-9:
            raise LedgerError("卖股数量超过未覆盖持股或价格无效")
        s["stock_realized"] += qty * (price - s["share_cost"])
        s["shares"] -= qty
        cash = qty * price
    elif tt in ("SELL_PUT", "SELL_CALL"):
        leg = {k: t.get(k) for k in ("contract_code", "strike", "expiry", "qty", "price", "contract_size")}
        if tt == "SELL_PUT":
            if put or s["shares"] or legs:
                raise LedgerError("SELL_PUT 需要空仓轮子;加仓请新开轮子")
            s.update({"open_" + k: v for k, v in leg.items()})
            s["open_option_type"] = "PUT"
        else:
            if any(same_contract(t, l) for l in legs):
                raise LedgerError("该 Call 已在本轮在场,请合并实际成交或指定其他合约")
            covered = sum(l["qty"] * l["contract_size"] for l in legs)
            if covered + qty * size > s["shares"] + 1e-9:
                raise LedgerError("Call 覆盖数量超过持股")
            leg["option_type"] = "CALL"
            legs.append(leg)
        cash = qty * price * size
        s["total_premium"] += cash - fee
    elif tt in ("BUY_PUT_CLOSE", "ASSIGNED", "BUY_CALL_CLOSE", "CALLED_AWAY", "EXPIRE"):
        is_put = tt in ("BUY_PUT_CLOSE", "ASSIGNED")
        if tt == "EXPIRE":
            if put and legs and not t.get("contract_code") and not (t.get("strike") and t.get("expiry")):
                raise LedgerError("同时有 Put 和 Call,到期登记必须指定合约")
            is_put = bool(put and (not legs or same_contract(t, put)))
        if is_put:
            if not put:
                raise LedgerError("需要在场 Put")
            leg = put
        else:
            matches = [l for l in legs if same_contract(t, l)]
            if not t.get("contract_code") and not (t.get("strike") and t.get("expiry")) and len(legs) == 1:
                matches = legs
            if len(matches) != 1:
                raise LedgerError("找不到唯一在场 Call,请指定合约")
            leg = matches[0]
        _match(t, leg)
        s["option_realized"] += qty * size * (leg["price"] - (price if tt in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE") else 0))
        if tt in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE"):
            cash = -qty * size * price
            s["total_premium"] += cash - fee
        else:
            s["total_premium"] -= fee
        if tt == "ASSIGNED":
            received = qty * size
            cost = s["shares"] * s["share_cost"] + received * leg["strike"]
            s["shares"] += received
            s["share_cost"] = cost / s["shares"]
            cash = -received * leg["strike"]
        elif tt == "CALLED_AWAY":
            delivered = qty * size
            if delivered > s["shares"] + 1e-9:
                raise LedgerError("交货数量超过持股")
            s["stock_realized"] += delivered * (leg["strike"] - s["share_cost"])
            s["shares"] -= delivered
            cash = delivered * leg["strike"]
        leg["qty"] -= qty
        if is_put:
            s["open_qty"] = leg["qty"]
            if leg["qty"] <= 1e-9:
                s.update(open_contract_code=None, open_option_type=None, open_strike=None,
                         open_expiry=None, open_qty=0.0, open_price=0.0)
        elif leg["qty"] <= 1e-9:
            legs.remove(leg)
    else:
        raise LedgerError(f"未知交易类型: {tt}")
    s["total_fees"] += fee
    s["cash_balance"] += cash - fee
    s["realized_pnl"] = round(s["stock_realized"] + s["option_realized"] - s["total_fees"], 4)
    if _put(s):
        s["status"] = "CSP_OPEN"
    elif legs:
        s["status"] = "CC_OPEN"
    elif s["shares"] > 1e-9:
        s["status"] = "HOLDING"
    elif tt in ("SELL_SHARES", "CALLED_AWAY"):
        s["status"] = "CLOSED"
        s["closed_at"] = t.get("traded_at")
    else:
        s["status"] = "IDLE"
    sync_primary_open(s)
    state.clear()
    state.update(s)
    return t
