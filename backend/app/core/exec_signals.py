"""可执行卖期权信号 — 触线/机会合流 → 席位 pending 队列。

Emit 不做 floor/DTE 闸;点差/重复/util/Call 正股由席位门控。
suggested_limit 对齐 draft_from_opportunity:优先 bid。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.data import exec_signal_repository as repo

logger = logging.getLogger(__name__)


def _get(sig: Any, key: str, default: Any = None) -> Any:
    if isinstance(sig, dict):
        return sig.get(key, default)
    return getattr(sig, key, default)


def side_from_signal(sig: Any) -> Optional[str]:
    level = _get(sig, "signal_level") or _get(sig, "side") or _get(sig, "kind")
    return repo.normalize_side(level)


def maybe_emit_from_touch(
    sig: Any,
    *,
    leaps_signal_id: Optional[str] = None,
    ask: Optional[float] = None,
    quote_asof: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """触线 LeapsSignal → 队列。非 TSLL/SPCH 返回 None。"""
    symbol = _get(sig, "symbol")
    if not repo.is_allowed_symbol(symbol):
        return None
    side = side_from_signal(sig)
    strike = _get(sig, "strike")
    expiry = _get(sig, "expiry")
    if side is None or strike is None or not expiry:
        logger.warning(
            "exec-signal skip incomplete touch: symbol=%s side=%s strike=%s expiry=%s",
            symbol, side, strike, expiry,
        )
        return None
    bid = _get(sig, "bid")
    try:
        return repo.emit_signal(
            symbol=symbol,
            side=side,
            strike=float(strike),
            expiry=str(expiry)[:10],
            source="touch",
            bid=float(bid) if bid is not None else None,
            ask=ask if ask is not None else _get(sig, "ask"),
            quote_asof=quote_asof or _get(sig, "quote_asof"),
            contract_code=_get(sig, "contract_code"),
            qty=1,
            leaps_signal_id=leaps_signal_id,
            timeframe=str(_get(sig, "timeframe") or ""),
            ema_type=str(_get(sig, "ema_type") or ""),
            ema_value=_get(sig, "ema_value"),
        )
    except Exception as e:
        logger.warning("exec-signal emit from touch failed: %s", e)
        return None


def maybe_emit_from_opportunity(
    opp: Dict[str, Any],
    *,
    source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """机会流 item → 队列(source=dual/score/suggest/timing→touch)。无闸门。"""
    if not isinstance(opp, dict):
        return None
    symbol = opp.get("symbol")
    if not repo.is_allowed_symbol(symbol):
        return None
    side = repo.normalize_side(opp.get("side"))
    strike = opp.get("strike")
    expiry = opp.get("expiry")
    if side is None or strike is None or not expiry:
        return None
    src = (source or opp.get("source") or "suggest").strip().lower()
    if src == "timing":
        src = "touch"
    if src not in repo.VALID_SOURCES:
        src = "suggest"
    bid = opp.get("bid")
    # 对齐 draft_from_opportunity: limit = bid
    return repo.emit_signal(
        symbol=symbol,
        side=side,
        strike=float(strike),
        expiry=str(expiry)[:10],
        source=src,
        bid=float(bid) if bid is not None else None,
        ask=float(opp["ask"]) if opp.get("ask") is not None else None,
        quote_asof=opp.get("quote_asof"),
        contract_code=opp.get("contract_code"),
        qty=1,
        timeframe=str((opp.get("timing") or {}).get("timeframe") or ""),
        ema_type=str((opp.get("timing") or {}).get("ema_type") or ""),
        ema_value=(opp.get("timing") or {}).get("ema_value") if src == "touch" else None,
    )
