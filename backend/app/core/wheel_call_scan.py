"""Call 触线扫描辅助:1h+1d,启用标的一律扫(可不持股)。

严格 OTM(strike > spot) + strike≥max(cost_basis, sell_above);
供 WheelTimingMonitor.scan_all 薄调用;不自动下单。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.wheel_timing_klines import (
    CALL_SCAN_TIMEFRAMES,
    call_cost_basis_for_scan,
    call_holding_cycles,
    call_strike_min,
)


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
    for call_tf in CALL_SCAN_TIMEFRAMES:
        _prog(
            target_i=target_i, target_n=target_n, symbol=symbol, side="CALL",
            expiry=None, contract_i=0, contract_n=0,
            message=f"触线 · {symbol} CALL {call_tf} · 标的 {target_i}/{target_n}",
        )
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
            level_map={"EMA50": "WHEEL_CALL", "EMA200": "WHEEL_CALL"},
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
