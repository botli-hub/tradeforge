"""Thin patch: WheelTimingMonitor.scan_all → Call 1h+1d + non-HOLDING.

Import this module (or call install()) once. Prefer over rewriting leaps_monitor.py.
不自动下单。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from app.core.leaps_monitor import WheelTimingMonitor, LeapsSignal
    from app.core.wheel_call_scan import scan_call_touches
    from app.core.wheel_timing_klines import TIMEFRAME_DAY
    import logging
    logger = logging.getLogger("app.core.leaps_monitor")

    def scan_all(self, symbol: Optional[str] = None, is_intraday: bool = True,
                 report: Optional[List[Dict[str, Any]]] = None) -> List[LeapsSignal]:
        """is_intraday=True(默认): 用合约最新价与 EMA 比较(现价 ≥ EMA 触发);
        False 则用当日最高价(盘中摸过均线也算)。"""
        from app.data import wheel_repository as wrepo
        from app.data import leaps_repository as lrepo

        def _prog(**kw):
            # 独立进度模块,禁止静默吞错导致前端一直「启动中」
            from app.core.wheel_timing_progress import update as _upd
            _upd(**kw)

        signals: List[LeapsSignal] = []
        targets = [t for t in wrepo.get_targets() if t.get("enabled")]
        if symbol:
            targets = [t for t in targets if t["symbol"] == symbol.upper()]
        n_targets = len(targets)
        _prog(
            phase="timing", target_n=n_targets, target_i=0,
            symbol=None, side=None, expiry=None, contract_i=0, contract_n=0,
            message=f"触线 · 共 {n_targets} 个标的",
        )
        for ti, t in enumerate(targets, start=1):
            sym = t["symbol"]
            try:
                cycles = wrepo.get_active_cycles(sym)
                dte_lo, dte_hi = self._dte_window(t)
                core_lo, core_hi = self._core_dte_window(t)

                # 卖 Put:启用标的一律扫描(状态机支持多轮并行,是否开仓由用户决定);
                # 接货底线降级为软警告(信号带 below_floor 标记,不再硬性跳过)
                _prog(
                    target_i=ti, target_n=n_targets, symbol=sym, side="PUT",
                    expiry=None, contract_i=0, contract_n=0,
                    message=f"触线 · {sym} PUT · 标的 {ti}/{n_targets}",
                )
                rep: Dict[str, Any] = {
                    "symbol": sym, "side": "PUT",
                    "dte": f"{dte_lo}-{dte_hi}",
                    "core_dte": f"{core_lo}-{core_hi}",
                }
                signals.extend(self.monitor.scan_symbol(
                    sym, t["floor_price"], is_intraday=is_intraday,
                    option_type="PUT",
                    dte_min=dte_lo, dte_max=dte_hi,
                    level_map={"EMA50": "WHEEL_PUT", "EMA200": "WHEEL_PUT"},
                    iv_threshold=self.iv_threshold,
                    respect_30d_cap=False, with_suggestions=False,
                    report=rep,
                    strike_range_down=self.strike_range_down,
                    strike_range_up=self.strike_range_up,
                    floor_hard=False,
                    progress_cb=_prog,
                    max_expiries=self.max_expiries,
                    core_dte_min=core_lo, core_dte_max=core_hi,
                    prefer_core_dte=self.prefer_core_dte,
                    timeframe=TIMEFRAME_DAY,
                ))
                if report is not None:
                    report.append(rep)

                # Call: 启用标的一律扫 1h+1d(可不持股);持仓时 strike 锚成本;CC 挂机仍认 1h
                signals.extend(scan_call_touches(
                    monitor=self.monitor,
                    symbol=sym,
                    target=t,
                    cycles=cycles,
                    is_intraday=is_intraday,
                    dte_lo=dte_lo,
                    dte_hi=dte_hi,
                    core_lo=core_lo,
                    core_hi=core_hi,
                    iv_threshold=self.iv_threshold,
                    strike_range_down=self.strike_range_down,
                    strike_range_up=self.strike_range_up,
                    max_expiries=self.max_expiries,
                    prefer_core_dte=self.prefer_core_dte,
                    progress_cb=_prog,
                    report=report,
                    target_i=ti,
                    target_n=n_targets,
                ))
            except Exception as e:
                logger.error("wheel timing scan(%s) failed: %s", sym, e)
                if report is not None:
                    report.append({"symbol": sym, "side": "-", "note": f"扫描异常: {e}"})

        # 写入时机历史(按合约代码合并去重)
        for sig in signals:
            try:
                lrepo.upsert_timing_history(sig)
            except Exception as e:
                logger.warning("时机历史写入失败(%s): %s", sig.contract_code, e)
        return signals


    WheelTimingMonitor.scan_all = scan_all  # type: ignore[method-assign]
    _INSTALLED = True


try:
    install()
except Exception:
    pass
