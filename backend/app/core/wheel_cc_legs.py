"""一轮多笔 Covered Call:台账 SELL_CALL 减平仓为 source of truth.

不自动下单,不对富途对账,不改止盈百分比.
open_contract_* 仍是「主腿/最近到期」摘要;旧单腿周期无 JSON 也能工作.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple


class CcLegError(Exception):
    """多腿 CC 校验/匹配错误,状态机转 WheelError."""


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _norm_code(code: Any) -> str:
    c = str(code or "").strip()
    if c.upper().startswith("US."):
        c = c[3:]
    return c.upper()


def _expiry(v: Any) -> str:
    return str(v or "")[:10]


def leg_key(leg: Dict[str, Any]) -> str:
    """合约身份:优先代码,否则 strike+到期日."""
    code = _norm_code(leg.get("contract_code"))
    if code:
        return f"code:{code}"
    strike = _f(leg.get("strike"))
    exp = _expiry(leg.get("expiry"))
    return f"kx:{strike:.4f}:{exp}"


def same_contract(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ca, cb = _norm_code(a.get("contract_code")), _norm_code(b.get("contract_code"))
    if ca and cb:
        return ca == cb
    sa, sb = a.get("strike"), b.get("strike")
    ea, eb = _expiry(a.get("expiry")), _expiry(b.get("expiry"))
    if sa is None or sb is None or not ea or not eb:
        return False
    try:
        return abs(float(sa) - float(sb)) < 1e-6 and ea == eb
    except (TypeError, ValueError):
        return False


def leg_from_trade(t: Dict[str, Any]) -> Dict[str, Any]:
    qty = _f(t.get("qty"), 1.0) or 1.0
    size = int(_f(t.get("contract_size"), 100.0) or 100)
    return {
        "contract_code": (t.get("contract_code") or None),
        "strike": t.get("strike"),
        "expiry": _expiry(t.get("expiry")) or t.get("expiry"),
        "qty": qty,
        "price": _f(t.get("price")),
        "contract_size": size,
        "option_type": "CALL",
    }


def covered_shares(legs: Optional[Iterable[Dict[str, Any]]]) -> float:
    total = 0.0
    for leg in legs or []:
        qty = _f(leg.get("qty"), 1.0) or 1.0
        size = _f(leg.get("contract_size"), 100.0) or 100.0
        total += qty * size
    return total


def uncovered_shares_of(cycle: Dict[str, Any], legs: Optional[List[Dict[str, Any]]] = None) -> float:
    shares = _f(cycle.get("shares"))
    if legs is None:
        legs = cycle_open_cc_legs(cycle)
    return max(shares - covered_shares(legs), 0.0)


def primary_leg(legs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """最近到期,其次较低 strike — 写入 open_contract_* 摘要."""
    if not legs:
        return None

    def _sort_key(leg: Dict[str, Any]) -> Tuple[str, float]:
        return (_expiry(leg.get("expiry")) or "9999-12-31", _f(leg.get("strike"), 1e18))

    return sorted(legs, key=_sort_key)[0]


def synthesize_leg_from_open_fields(cycle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(cycle.get("open_option_type") or "").upper() != "CALL":
        return None
    if not cycle.get("open_strike") and not cycle.get("open_contract_code"):
        return None
    return {
        "contract_code": cycle.get("open_contract_code"),
        "strike": cycle.get("open_strike"),
        "expiry": cycle.get("open_expiry"),
        "qty": _f(cycle.get("open_qty"), 1.0) or 1.0,
        "price": _f(cycle.get("open_price")),
        "contract_size": int(_f(cycle.get("open_contract_size"), 100.0) or 100),
        "option_type": "CALL",
    }


def parse_open_cc_legs_json(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, str):
        import json
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if isinstance(data, list):
            return [dict(x) for x in data if isinstance(x, dict)]
    return []


def cycle_open_cc_legs(cycle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """读周期上的在场 CC 腿. JSON 优先,否则回退 open_contract_*."""
    legs = parse_open_cc_legs_json(cycle.get("open_cc_legs"))
    if legs:
        return legs
    syn = synthesize_leg_from_open_fields(cycle)
    return [syn] if syn else []


def overlay_leg_on_cycle(cycle: Dict[str, Any], leg: Dict[str, Any]) -> Dict[str, Any]:
    """把某一 CC 腿铺到 cycle.open_* 上,供体检/看板沿用单腿字段."""
    d = dict(cycle)
    d["open_contract_code"] = leg.get("contract_code") or d.get("open_contract_code")
    d["open_option_type"] = "CALL"
    d["open_strike"] = leg.get("strike")
    d["open_expiry"] = leg.get("expiry")
    d["open_qty"] = _f(leg.get("qty"), 1.0) or 1.0
    d["open_price"] = _f(leg.get("price"))
    d["open_contract_size"] = int(_f(leg.get("contract_size"), 100.0) or 100)
    d["cc_leg_key"] = leg_key(leg)
    expiry = d.get("open_expiry")
    if expiry:
        try:
            d["open_dte"] = (date.fromisoformat(str(expiry)[:10]) - date.today()).days
        except Exception:
            pass
    return d


def expand_open_option_rows(cycles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """CSP 一行;CC_OPEN 每条在场 Call 一行. 供 open-positions/check/今日必须处理."""
    rows: List[Dict[str, Any]] = []
    for c in cycles:
        status = str(c.get("status") or "")
        if status == "CSP_OPEN":
            if c.get("open_contract_code") or c.get("open_strike"):
                rows.append(dict(c))
            continue
        if status != "CC_OPEN":
            continue
        legs = cycle_open_cc_legs(c)
        if not legs:
            if c.get("open_contract_code") or c.get("open_strike"):
                rows.append(dict(c))
            continue
        for leg in legs:
            rows.append(overlay_leg_on_cycle(c, leg))
    return rows


def match_open_leg(
    legs: List[Dict[str, Any]],
    trade: Dict[str, Any],
) -> int:
    """关闭腿匹配:代码 > strike+到期 > 唯一在场腿. 找不到则 -1."""
    if not legs:
        return -1
    hits = [i for i, leg in enumerate(legs) if same_contract(leg, trade)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return hits[0]
    has_id = bool(_norm_code(trade.get("contract_code")) or (
        trade.get("strike") is not None and _expiry(trade.get("expiry"))
    ))
    if not has_id and len(legs) == 1:
        return 0
    return -1


def _require_match(legs: List[Dict[str, Any]], trade: Dict[str, Any], action: str) -> int:
    idx = match_open_leg(legs, trade)
    if idx >= 0:
        return idx
    if not legs:
        raise CcLegError(f"{action} 需要有在场 Call")
    if len(legs) > 1:
        raise CcLegError(
            f"本轮有 {len(legs)} 笔在场 Call,请指定 contract_code 或 strike+到期日后再{action}"
        )
    raise CcLegError(f"找不到要{action}的 Call 腿(请核对该合约代码或 strike/到期日)")


def sync_primary_open(s: Dict[str, Any]) -> None:
    """CC_OPEN 时把主腿写回 open_contract_*;无腿则清空摘要(不清 CSP)."""
    if s.get("status") == "CSP_OPEN":
        return
    legs = list(s.get("open_cc_legs") or [])
    if s.get("status") == "CC_OPEN" and legs:
        p = primary_leg(legs) or legs[0]
        s.update(
            open_contract_code=p.get("contract_code"),
            open_option_type="CALL",
            open_strike=p.get("strike"),
            open_expiry=p.get("expiry"),
            open_qty=_f(p.get("qty"), 1.0) or 1.0,
            open_price=_f(p.get("price")),
            open_contract_size=int(_f(p.get("contract_size"), 100.0) or 100),
        )
        return
    if s.get("status") != "CSP_OPEN":
        s.update(
            open_contract_code=None, open_option_type=None, open_strike=None,
            open_expiry=None, open_qty=0.0, open_price=0.0,
        )


def apply_sell_call(s: Dict[str, Any], t: Dict[str, Any]) -> None:
    """HOLDING 或 CC_OPEN 可再挂不同 Call;覆盖股数不得超过持股."""
    status = s.get("status")
    if status not in ("HOLDING", "CC_OPEN"):
        raise CcLegError(
            f"当前轮子处于「{'卖Put中' if status == 'CSP_OPEN' else status}」,不能登记「卖出Call」"
            "——该操作需要轮子处于「持股」或「卖Call中」且仍有未覆盖股份"
        )
    strike = t.get("strike")
    expiry = t.get("expiry")
    if not strike or not expiry:
        raise CcLegError("SELL_CALL 需要 strike 和 expiry")
    qty = _f(t.get("qty"), 1.0) or 1.0
    size = int(_f(t.get("contract_size"), 100.0) or 100)
    price = _f(t.get("price"))
    fee = _f(t.get("fee"))
    shares = _f(s.get("shares"))
    if shares <= 0:
        raise CcLegError("没有持股,不能登记卖出 Call")
    legs = list(s.get("open_cc_legs") or [])
    new_leg = leg_from_trade(t)
    for leg in legs:
        if leg_key(leg) == leg_key(new_leg) or same_contract(leg, new_leg):
            raise CcLegError(
                "该 Call 合约已在本轮在场(同代码或同 strike/到期日)。"
                "请登记不同合约,或先平仓后再开;不把同合约张数叠到已有腿上"
            )
    need = qty * size
    covered = covered_shares(legs)
    if covered + need > shares + 1e-6:
        raise CcLegError(
            f"在场 Call 已覆盖 {covered:g} 股,本单再覆盖 {need:g} 股,"
            f"超过持股 {shares:g}(通常 1 张=100 股)"
        )
    s["total_premium"] = _f(s.get("total_premium")) + qty * price * size - fee
    s["total_fees"] = _f(s.get("total_fees")) + fee
    legs.append(new_leg)
    s["open_cc_legs"] = legs
    s["status"] = "CC_OPEN"
    sync_primary_open(s)


def apply_cc_close(s: Dict[str, Any], t: Dict[str, Any], *, kind: str) -> None:
    """BUY_CALL_CLOSE / EXPIRE / CALLED_AWAY:只关匹配腿,其它腿保留.

    kind: close | expire | called_away
    """
    if s.get("status") != "CC_OPEN":
        raise CcLegError("需要轮子处于「卖Call中」")
    legs = list(s.get("open_cc_legs") or [])
    if not legs:
        syn = synthesize_leg_from_open_fields(s)
        if syn:
            legs = [syn]
    action = {"close": "买回平仓", "expire": "到期作废", "called_away": "被行权交货"}[kind]
    idx = _require_match(legs, t, action)
    leg = legs[idx]
    close_qty = _f(t.get("qty"), 0.0)
    if close_qty <= 0:
        close_qty = _f(leg.get("qty"), 1.0) or 1.0
    leg_qty = _f(leg.get("qty"), 1.0) or 1.0
    take = min(close_qty, leg_qty)
    size = int(_f(t.get("contract_size") or leg.get("contract_size"), 100.0) or 100)
    fee = _f(t.get("fee"))
    price = _f(t.get("price"))

    if kind == "close":
        s["total_premium"] = _f(s.get("total_premium")) - take * price * size
        s["total_fees"] = _f(s.get("total_fees")) + fee
    elif kind == "expire":
        s["total_fees"] = _f(s.get("total_fees")) + fee
    elif kind == "called_away":
        strike = t.get("strike") if t.get("strike") is not None else leg.get("strike")
        if not strike:
            raise CcLegError("CALLED_AWAY 需要 strike(交货价)")
        assigned_shares = take * size
        remain_shares = _f(s.get("shares")) - assigned_shares
        if remain_shares < -1e-6:
            raise CcLegError(
                f"交货 {assigned_shares:g} 股超过持股 {_f(s.get('shares')):g}"
            )
        remain_shares = max(remain_shares, 0.0)
        credit = take * _f(leg.get("price")) * size
        other = [x for i, x in enumerate(legs) if i != idx]
        if take < leg_qty - 1e-9:
            reduced = dict(leg)
            reduced["qty"] = leg_qty - take
            other.append(reduced)
        if remain_shares <= 1e-9:
            if other:
                raise CcLegError(
                    "交货后持股为 0,但仍有其他在场 Call;请先平掉其余 Call 或按实际张数交货"
                )
            s["realized_pnl"] = round(
                (float(strike) - _f(s.get("share_cost"))) * _f(s.get("shares"))
                + _f(s.get("total_premium")) - fee,
                4,
            )
            s["total_fees"] = _f(s.get("total_fees")) + fee
            s["shares"] = 0.0
            s["open_cc_legs"] = []
            s["status"] = "CLOSED"
            s["closed_at"] = t.get("traded_at")
            sync_primary_open(s)
            return
        # 部分交货:该腿权利金随交货股实现,不再摊薄剩余持股
        s["shares"] = remain_shares
        s["total_premium"] = _f(s.get("total_premium")) - credit
        s["total_fees"] = _f(s.get("total_fees")) + fee
        remain_cover = covered_shares(other)
        if remain_cover > remain_shares + 1e-6:
            raise CcLegError(
                f"交货后剩余 Call 覆盖 {remain_cover:g} 股超过剩余持股 {remain_shares:g},"
                "请先平掉其他 Call 或减少交货张数"
            )
        s["open_cc_legs"] = other
        s["status"] = "CC_OPEN" if other else "HOLDING"
        sync_primary_open(s)
        return

    if take >= leg_qty - 1e-9:
        legs.pop(idx)
    else:
        legs[idx] = dict(leg)
        legs[idx]["qty"] = leg_qty - take
    s["open_cc_legs"] = legs
    if legs:
        s["status"] = "CC_OPEN"
    else:
        s["status"] = "HOLDING"
    sync_primary_open(s)


def open_cc_legs_from_trades(trades: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """台账重放:SELL_CALL 减 BUY_CALL_CLOSE/EXPIRE/CALLED_AWAY. 纯函数,不碰库."""
    s: Dict[str, Any] = {
        "status": "HOLDING",
        "shares": 1e12,  # 只推导腿,覆盖校验交给状态机
        "share_cost": 0.0,
        "total_premium": 0.0,
        "total_fees": 0.0,
        "open_cc_legs": [],
    }
    for t in trades or []:
        tt = t.get("trade_type")
        if tt == "SELL_CALL":
            try:
                apply_sell_call(s, t)
            except CcLegError:
                # 推导路径:同合约重复则跳过叠加(状态机登记时已拒绝)
                continue
        elif tt == "BUY_CALL_CLOSE":
            if s.get("open_cc_legs"):
                try:
                    apply_cc_close(s, t, kind="close")
                except CcLegError:
                    continue
        elif tt == "EXPIRE":
            if s.get("open_cc_legs"):
                try:
                    apply_cc_close(s, t, kind="expire")
                except CcLegError:
                    continue
        elif tt == "CALLED_AWAY":
            if s.get("open_cc_legs"):
                try:
                    apply_cc_close(s, t, kind="called_away")
                except CcLegError:
                    continue
        elif tt in ("SELL_SHARES",):
            s["open_cc_legs"] = []
            s["status"] = "HOLDING"
    return list(s.get("open_cc_legs") or [])
