"""Call 触线扫描辅助:1h+1d,启用标的一律扫(可不持股)。

严格 OTM(strike > spot) + strike≥max(cost_basis, sell_above);
供 WheelTimingMonitor.scan_all 薄调用;不自动下单。

1h 默认仅推 EMA200 强信号(可配 wheel_timing.call_1h_ema_types);
日线仍扫 EMA50+EMA200。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.wheel_timing_klines import (
    CALL_SCAN_TIMEFRAMES,
    TIMEFRAME_HOUR,
    call_cost_basis_for_scan,
    call_holding_cycles,
    call_strike_min,
    normalize_timeframe,
)

# Call 日线:EMA50 + EMA200;1h 默认仅 EMA200(强)
_CALL_DAILY_EMA_TYPES = ("EMA50", "EMA200")
_CALL_1H_EMA_TYPES_DEFAULT = ("EMA200",)


def _call_ema_types_for_tf(timeframe: str, wheel_timing: Optional[Dict[str, Any]] = None) -> Sequence[str]:
    """1h → call_1h_ema_types(默认仅 EMA200);其它(含 1d)→ EMA50+EMA200。"""
    wt = wheel_timing or {}
    if normalize_timeframe(timeframe) == TIMEFRAME_HOUR:
        raw = wt.get("call_1h_ema_types")
        if raw is None:
            return list(_CALL_1H_EMA_TYPES_DEFAULT)
        if isinstance(raw, str):
            items = [x.strip().upper() for x in raw.split(",") if x.strip()]
        else:
            items = [str(x).strip().upper() for x in raw if str(x).strip()]
        # 只认 EMA50 / EMA200;空则回落默认
        allowed = {"EMA50", "EMA200"}
        out = [x for x in items if x in allowed]
        return out or list(_CALL_1H_EMA_TYPES_DEFAULT)
    return list(_CALL_DAILY_EMA_TYPES)


def call_level_map(
    timeframe: str,
    *,
    signal_level: str = "WHEEL_CALL",
    wheel_timing: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """按周期构造 Call 触线 level_map。"""
    return {ema: signal_level for ema in _call_ema_types_for_tf(timeframe, wheel_timing)}


def scan_call_touches(
    *,
    monitor,
    symbol: str,
    target: Dict[str, Any],
    cycles: Sequence[Dict[str, Any]],
    is_intraday: bool,
    dte_lo: int,
    dte_hi: int,
    core_lo: int,
    core_hi: int,
    iv_threshold: float,
    strike_range_down: float,
    strike_range_up: float,
    max_expiries: int,
    prefer_core_dte: bool,
    progress_cb: Optional[Callable[..., None]] = None,
    report: Optional[List[Dict[str, Any]]] = None,
    target_i: int = 0,
    target_n: int = 0,
) -> list:
    """对启用标的扫 Call 1h+1d;HOLDING 时用成本锚 strike。返回信号列表。"""
    holding = call_holding_cycles(cycles)
    cost_basis = call_cost_basis_for_scan(cycles)
    sell_above = target.get("sell_above")
    try:
        from app.core.wheel_call_timing import get_target_sell_above
        sa = get_target_sell_above(symbol)
        if sa is not None:
            sell_above = sa
    except Exception:
        pass
    strike_floor = call_strike_min(cost_basis, sell_above)
    signals = []
    _prog = progress_cb or (lambda **kw: None)
    wt = {}
    try:
        wt = (getattr(monitor, "cfg", None) or {}).get("wheel_timing") or {}
    except Exception:
        wt = {}
    for call_tf in CALL_SCAN_TIMEFRAMES:
        _prog(
            target_i=target_i, target_n=target_n, symbol=symbol, side="CALL",
            expiry=None, contract_i=0, contract_n=0,
            message=f"触线 · {symbol} CALL {call_tf} · 标的 {target_i}/{target_n}",
        )
        level_map = call_level_map(call_tf, wheel_timing=wt)
        rep = {
            "symbol": symbol, "side": "CALL",
            "timeframe": call_tf,
            "dte": f"{dte_lo}-{dte_hi}",
            "core_dte": f"{core_lo}-{core_hi}",
            "holding": bool(holding),
        }
        signals.extend(monitor.scan_symbol(
            symbol, 0, is_intraday=is_intraday,
            option_type="CALL",
            dte_min=dte_lo, dte_max=dte_hi,
            strike_min=strike_floor,
            level_map=level_map,
            iv_threshold=iv_threshold,
            respect_30d_cap=False, with_suggestions=False,
            report=rep,
            strike_range_down=strike_range_down,
            strike_range_up=strike_range_up,
            progress_cb=_prog,
            max_expiries=max_expiries,
            core_dte_min=core_lo, core_dte_max=core_hi,
            prefer_core_dte=prefer_core_dte,
            timeframe=call_tf,
            otm_only=True,
        ))
        if report is not None:
            report.append(rep)
    return signals
