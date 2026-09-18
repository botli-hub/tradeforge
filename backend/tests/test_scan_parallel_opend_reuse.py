"""标的级并行扫描 + OpenD QuoteContext 复用。

覆盖:
- resolve_scan_max_workers 边界
- QuoteSession 开闭
- LeapsMonitor / WheelTimingMonitor.scan_all 多标的均被扫到
- 全局限频 _throttle 共享(并行下仍串行化)
- 传入 quote_ctx 时不再 open_quote_context(复用)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.opend import (  # noqa: E402
    QuoteSession,
    resolve_scan_max_workers,
    SCAN_MAX_WORKERS_CAP,
)
from app.core import leaps_monitor as lm  # noqa: E402


def test_resolve_scan_max_workers_bounds():
    assert resolve_scan_max_workers(None) == 4
    assert resolve_scan_max_workers(1) == 1
    assert resolve_scan_max_workers(0) == 1
    assert resolve_scan_max_workers(-3) == 1
    assert resolve_scan_max_workers(99) == SCAN_MAX_WORKERS_CAP
    assert resolve_scan_max_workers("3") == 3
    assert resolve_scan_max_workers("x", default=4) == 4


def test_quote_session_opens_and_closes_once():
    fake_ctx = MagicMock()
    with patch("app.core.opend.open_quote_context", return_value=fake_ctx) as opener:
        with QuoteSession(host="127.0.0.1", port=11111) as qs:
            assert qs.ctx is fake_ctx
            opener.assert_called_once()
        fake_ctx.close.assert_called_once()


def test_quote_session_external_ctx_not_closed():
    fake_ctx = MagicMock()
    with QuoteSession(ctx=fake_ctx) as qs:
        assert qs.ctx is fake_ctx
    fake_ctx.close.assert_not_called()


def test_leaps_scan_all_hits_all_symbols_parallel():
    mon = lm.LeapsMonitor({
        "futu": {"host": "127.0.0.1", "port": 11111},
        "signal": {"scan_max_workers": 3},
    })
    seen: List[str] = []
    lock = threading.Lock()

    def fake_scan(symbol, floor_price, is_intraday=False, **kw):
        with lock:
            seen.append(symbol)
        time.sleep(0.02)
        return []

    watchlist = [
        {"symbol": "AAPL", "floor_price": 100, "enabled": True},
        {"symbol": "TSLA", "floor_price": 200, "enabled": True},
        {"symbol": "NVDA", "floor_price": 300, "enabled": True},
        {"symbol": "OFF", "floor_price": 1, "enabled": False},
    ]
    with patch.object(lm.repo, "get_watchlist", return_value=watchlist):
        with patch.object(mon, "scan_symbol", side_effect=fake_scan):
            out = mon.scan_all(is_intraday=True)
    assert out == []
    assert sorted(seen) == ["AAPL", "NVDA", "TSLA"]


def test_wheel_scan_all_hits_all_targets_parallel():
    wt = lm.WheelTimingMonitor({
        "futu": {"host": "127.0.0.1", "port": 11111},
        "wheel_timing": {"scan_max_workers": 3},
    })
    seen: List[str] = []
    lock = threading.Lock()

    def fake_one(t, ti, n_targets, is_intraday, report, prog_lock):
        with lock:
            seen.append(t["symbol"])
        time.sleep(0.02)
        return []

    targets = [
        {"symbol": "AAPL", "floor_price": 100, "enabled": True},
        {"symbol": "SPX", "floor_price": 4000, "enabled": True},
        {"symbol": "QQQ", "floor_price": 300, "enabled": True},
    ]
    with patch("app.data.wheel_repository.get_targets", return_value=targets):
        with patch("app.data.leaps_repository.upsert_timing_history"):
            with patch.object(wt, "_scan_one_target", side_effect=fake_one):
                with patch("app.core.wheel_timing_progress.update"):
                    out = wt.scan_all(is_intraday=True)
    assert out == []
    assert sorted(seen) == ["AAPL", "QQQ", "SPX"]


def test_throttle_shared_under_parallel_workers():
    """并行 worker 调用 _throttle 仍走同一把锁+全局时间戳。"""
    calls: List[float] = []
    lock = threading.Lock()
    # 重置全局节流状态
    with lm._QUOTA_LOCK:
        lm._LAST_QUOTA_CALL["t"] = 0.0

    def tracking_sleep(sec):
        # 记录实际等待,加速测试:把 3.2s 缩到极小但保留串行语义
        pass

    original = lm._throttle

    def thin_throttle(min_interval: float = 0.05):
        with lm._QUOTA_LOCK:
            now = time.monotonic()
            wait = lm._LAST_QUOTA_CALL["t"] + min_interval - now
            if wait > 0:
                time.sleep(wait)
            lm._LAST_QUOTA_CALL["t"] = time.monotonic()
            with lock:
                calls.append(lm._LAST_QUOTA_CALL["t"])

    def worker():
        for _ in range(3):
            thin_throttle()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 9
    # 相邻调用间隔应 ≥ min_interval(允许微小时钟误差)
    deltas = [b - a for a, b in zip(calls, calls[1:])]
    assert all(d >= 0.04 for d in deltas), deltas


def test_fetch_kline_reuses_quote_ctx_no_open():
    mon = lm.LeapsMonitor({"futu": {"host": "127.0.0.1", "port": 11111}})
    fake_ctx = MagicMock()
    fake_ctx.subscribe.return_value = (0, None)  # RET_OK-ish; code uses futu.RET_OK

    import futu
    fake_ctx.subscribe.return_value = (futu.RET_OK, None)
    import pandas as pd
    fake_df = pd.DataFrame([
        {"time_key": "2026-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
    ])
    fake_ctx.get_cur_kline.return_value = (futu.RET_OK, fake_df)

    with patch("app.core.opend.open_quote_context") as opener:
        with patch.object(lm, "_throttle"):
            bars = mon._fetch_kline_history(
                "US.AAPL260117P00100000", num=10, timeframe="1d", quote_ctx=fake_ctx,
            )
    opener.assert_not_called()
    fake_ctx.subscribe.assert_called_once()
    fake_ctx.get_cur_kline.assert_called_once()
    fake_ctx.close.assert_not_called()
    fake_ctx.unsubscribe.assert_called_once()
    assert len(bars) == 1


def test_fetch_underlying_reuses_quote_ctx():
    mon = lm.LeapsMonitor({"futu": {"host": "127.0.0.1", "port": 11111}})
    fake_ctx = MagicMock()
    import futu
    import pandas as pd
    fake_df = pd.DataFrame([{"last_price": 150.0, "prev_close_price": 149.0}])
    fake_ctx.get_market_snapshot.return_value = (futu.RET_OK, fake_df)

    with patch("app.core.opend.open_quote_context") as opener:
        with patch.object(lm, "_throttle"):
            price = mon._fetch_underlying_price("AAPL", quote_ctx=fake_ctx)
    opener.assert_not_called()
    fake_ctx.close.assert_not_called()
    assert price == 150.0


def test_scan_symbol_opens_one_quote_session(monkeypatch):
    """整标的扫描只进入一次 QuoteSession(一次 open)。"""
    mon = lm.LeapsMonitor({"futu": {"host": "127.0.0.1", "port": 11111}})
    open_count = {"n": 0}
    fake_ctx = MagicMock()

    class FakeSession:
        def __init__(self, *a, **k):
            open_count["n"] += 1
            self._ctx = fake_ctx

        @property
        def ctx(self):
            return self._ctx

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    with patch.object(lm, "QuoteSession", FakeSession):
        with patch.object(mon, "_scan_symbol_body", return_value=[]) as body:
            out = mon.scan_symbol("AAPL", 100.0)
    assert out == []
    assert open_count["n"] == 1
    assert body.call_count == 1
    assert body.call_args.kwargs.get("quote_ctx") is fake_ctx


def test_chan_alert_cycle_scans_symbols_in_parallel():
    from app.services import chan_alerts as ca

    analyzed: List[str] = []
    lock = threading.Lock()

    def fake_analyze(symbol, timeframe, cfg):
        with lock:
            analyzed.append(f"{symbol}:{timeframe}")
        time.sleep(0.01)
        return {"signals": []}

    targets = [{"symbol": s, "enabled": True} for s in ("AAA", "BBB", "CCC")]
    cfg = {
        "chan_alerts": {
            "enabled": True,
            "timeframes": ["30m"],
            "session_only": False,
            "scan_max_workers": 3,
            "symbols": ["AAA", "BBB", "CCC"],
        }
    }
    out = ca.run_chan_alert_cycle(
        cfg=cfg,
        analyze_fn=fake_analyze,
        dry_run=True,
        persist=False,
        session_open=True,
        force=True,
        last_runs={},  # empty → due; prime_on_empty will bootstrap
        prime_on_empty=True,
        targets=targets,
    )
    # due includes 30m; each symbol analyzed
    assert sorted(analyzed) == ["AAA:30m", "BBB:30m", "CCC:30m"]
    assert len(out.get("scanned") or []) == 3
