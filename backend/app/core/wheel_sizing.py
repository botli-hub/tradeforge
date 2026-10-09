"""张数、费用、预期波动和限价阶梯。纯函数,不访问券商。"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def fee_per_share(fee_per_contract: float = 0.65, contract_size: float = 100) -> float:
    fee = _f(fee_per_contract)
    size = _f(contract_size)
    if fee is None or size is None or size <= 0:
        return 0.0
    return max(0.0, fee) / size


def net_premium(
    premium: float,
    fee_per_contract: float = 0.65,
    contract_size: float = 100,
) -> float:
    prem = _f(premium) or 0.0
    return max(0.0, prem - fee_per_share(fee_per_contract, contract_size))


def annualized_yield(premium: float, collateral: float, dte: int) -> float:
    prem = _f(premium) or 0.0
    col = _f(collateral) or 0.0
    try:
        days = int(dte)
    except (TypeError, ValueError):
        return 0.0
    if col <= 0 or days <= 0 or prem <= 0:
        return 0.0
    return round(prem / col * (365 / days) * 100, 2)


def net_annualized(
    premium: float,
    collateral: float,
    dte: int,
    fee_per_contract: float = 0.65,
    contract_size: float = 100,
) -> Dict[str, float]:
    gross = annualized_yield(premium, collateral, dte)
    net_prem = net_premium(premium, fee_per_contract, contract_size)
    return {
        "annualized": annualized_yield(net_prem, collateral, dte),
        "annualized_gross": gross,
        "premium_net": round(net_prem, 4),
        "fee_per_share": round(fee_per_share(fee_per_contract, contract_size), 4),
    }


def _iv_decimal(iv: Any) -> Optional[float]:
    v = _f(iv)
    if v is None or v <= 0:
        return None
    if v > 3:
        return v / 100.0
    return v


def expected_move(spot: Any, iv: Any, dte: Any) -> Optional[float]:
    """spot · IV · √(DTE/365)。IV 大于 3 视为百分数。"""
    s = _f(spot)
    v = _iv_decimal(iv)
    days = _f(dte)
    if s is None or v is None or days is None or s <= 0 or days <= 0:
        return None
    return round(s * v * math.sqrt(days / 365.0), 4)


def expected_move_pct(spot: Any, iv: Any, dte: Any) -> Optional[float]:
    em = expected_move(spot, iv, dte)
    s = _f(spot)
    if em is None or s is None or s <= 0:
        return None
    return round(em / s * 100, 3)


def buffer_sigma(side: str, spot: Any, strike: Any, iv: Any, dte: Any) -> Optional[float]:
    em = expected_move(spot, iv, dte)
    s = _f(spot)
    k = _f(strike)
    if em is None or em <= 0 or s is None or k is None:
        return None
    buf = (s - k) if str(side or "").upper() == "PUT" else (k - s)
    return round(buf / em, 3)


def estimate_pot(delta: Any) -> float:
    """触及概率粗估 ≈ 2|Δ|,上限 1。不是校准后的盈利概率。"""
    d = abs(_f(delta) or 0.0)
    return round(min(1.0, 2.0 * d), 4)


def option_tick(price: Any) -> float:
    """美股期权跳动。低于 $3 按 $0.05;$3 及以上非便士 $0.10。"""
    p = abs(_f(price) or 0.0)
    if p >= 3:
        return 0.10
    return 0.05


def limit_ladder(bid: Any, ask: Any, *, steps: int = 3) -> List[Dict[str, Any]]:
    """从中间价起步,按 tick 下调,地板是 bid。bid 仍是可成交下限。"""
    b = _f(bid)
    a = _f(ask)
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return []
    mid = (b + a) / 2.0
    tick = option_tick(mid)
    out: List[Dict[str, Any]] = []
    px = mid
    for i in range(max(1, int(steps))):
        shown = max(b, round(px + 1e-9, 2))
        out.append({
            "step": i,
            "limit": round(shown, 2),
            "tick": tick,
            "role": "start" if i == 0 else ("floor" if shown <= b + 1e-9 else "step"),
        })
        if shown <= b + 1e-9:
            break
        px = shown - tick
    return out


def suggest_contract_qty(
    *,
    strike: Any,
    contract_size: Any = 100,
    equity: Any = None,
    max_symbol_pct: Any = 0.25,
    symbol_committed: Any = 0,
    symbol_headroom: Any = None,
    idle_cash: Any = None,
    cash_reserve: Any = 0,
    size_mult: Any = 1.0,
    risk_max_qty: Any = None,
    budget_enabled: bool = False,
) -> Optional[int]:
    """权益或行权价未知时返回 None(草稿保持 1 张)。

    预算已知且用尽时返回 0。size_mult 来自 IV 环境,只在张数算出之后再缩放。
    风险预算关闭时不用 suggested_max_qty。
    """
    k = _f(strike)
    size = _f(contract_size)
    if k is None or size is None or k <= 0 or size <= 0:
        return None
    per = k * size
    caps: List[float] = []
    head = _f(symbol_headroom)
    if head is not None:
        caps.append(max(0.0, head))
    eq = _f(equity)
    if eq is not None and eq > 0:
        pct = _f(max_symbol_pct)
        if pct is None:
            pct = 0.25
        committed = _f(symbol_committed) or 0.0
        caps.append(max(0.0, eq * pct - committed))
    idle = _f(idle_cash)
    if idle is not None:
        reserve = _f(cash_reserve) or 0.0
        caps.append(max(0.0, idle - reserve))
    if not caps:
        return None
    budget = min(caps)
    mult = _f(size_mult)
    if mult is None or mult < 0:
        mult = 1.0
    qty = math.floor(math.floor(budget / per) * mult)
    if budget_enabled and risk_max_qty is not None:
        cap = _f(risk_max_qty)
        if cap is not None:
            qty = min(qty, math.floor(cap))
    return max(0, int(qty))


def effective_position_floor(
    entry: Any,
    live: Any,
    reprice_pct: Any = 0,
) -> Optional[float]:
    """在场 Put 用入场愿接价。不自动放宽。

    reprice_pct ≤ 0 时永不因现价下跌而收紧。
    只有现价推荐低于入场、且跌幅超过 reprice_pct 时才改用更低的推荐价。
    """
    entry_f = _f(entry)
    live_f = _f(live)
    if entry_f is not None and entry_f <= 0:
        entry_f = None
    if live_f is not None and live_f <= 0:
        live_f = None
    if entry_f is None:
        return round(live_f, 2) if live_f is not None else None
    if live_f is None or live_f >= entry_f:
        return round(entry_f, 2)
    pct = _f(reprice_pct) or 0.0
    if pct <= 0:
        return round(entry_f, 2)
    drop = (entry_f - live_f) / entry_f * 100.0
    if drop > pct:
        return round(live_f, 2)
    return round(entry_f, 2)


def holding_disposal(
    spot: Any,
    cost_basis: Any,
    shares: Any = None,
    drop_pct: Any = 8,
) -> Optional[Dict[str, Any]]:
    """持股回撤达到阈值时给出三种处理,不自动下单。"""
    sp = _f(spot)
    cb = _f(cost_basis)
    thresh = _f(drop_pct)
    if sp is None or cb is None or thresh is None or sp <= 0 or cb <= 0:
        return None
    dd = (cb - sp) / cb * 100.0
    if dd < thresh:
        return None
    return {
        "drop_pct": round(dd, 2),
        "threshold_pct": thresh,
        "shares": _f(shares),
        "options": [
            {"code": "wait_cc", "label": "继续等卖 Call"},
            {"code": "sell_call_below_cost", "label": "可考虑低于成本卖 Call"},
            {"code": "stop", "label": "止损卖出正股"},
        ],
    }


def dte_preference(dte: Any) -> float:
    """25–45 天为 1。短于 14 天从 1 线性降到 0.7。"""
    try:
        d = int(dte)
    except (TypeError, ValueError):
        return 1.0
    if d < 14:
        return round(0.7 + 0.3 * max(d, 0) / 14.0, 4)
    return 1.0
