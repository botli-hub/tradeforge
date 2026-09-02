"""美股 K 线优先 OpenD 历史回填:无 live OpenD。"""
from __future__ import annotations

import asyncio
import io
import sys
import types
import unittest
import urllib.error
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.adapter import FutuAdapter, YahooAdapter, YahooRateLimitError  # noqa: E402
from app.data.history_backfill import (  # noqa: E402
    ensure_local_kline_range,
    is_rate_limited,
    kline_source_chain,
)
from app.data.source_router import resolve_kline_source, resolve_quote_source  # noqa: E402


def _ensure_futu_stub():
    existing = sys.modules.get("futu")
    if existing is not None and hasattr(existing, "KLType") and hasattr(existing, "RET_OK"):
        return existing
    futu = types.ModuleType("futu")
    futu.RET_OK = 0

    class KLType:
        K_1M = "K_1M"
        K_5M = "K_5M"
        K_15M = "K_15M"
        K_30M = "K_30M"
        K_60M = "K_60M"
        K_240M = "K_240M"
        K_DAY = "K_DAY"
        K_WEEK = "K_WEEK"
        K_MON = "K_MON"

    class AuType:
        QFQ = "QFQ"

    futu.KLType = KLType
    futu.AuType = AuType
    sys.modules["futu"] = futu
    return futu


class FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


def _bar_row(ts: str, price: float = 10.0, volume: int = 100) -> dict:
    return {
        "time_key": ts,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price + 0.5,
        "volume": volume,
    }


def _cfg():
    return {
        "futu": {"host": "127.0.0.1", "port": 11111},
        "yahoo_base_url": "https://query1.finance.yahoo.com/v8/finance/chart",
    }


def _local_rows(n: int = 60):
    rows = []
    for i in range(n):
        price = 10.0 + i * 0.05
        rows.append({
            "symbol": "AAPL",
            "timeframe": "1d",
            "ts": f"2024-01-{(i % 28) + 1:02d}T00:00:00",
            "timestamp": f"2024-01-{(i % 28) + 1:02d}T00:00:00",
            "open": price,
            "high": price + 0.8,
            "low": price - 0.4,
            "close": price + 0.2,
            "volume": 1000 + i,
            "source": "futu",
        })
    return rows


class SourceRouterTest(unittest.TestCase):
    def test_us_kline_prefers_futu(self):
        for symbol in ("AAPL", "ARM", "TSLA", "US.AAPL", "SPCX"):
            self.assertEqual(resolve_kline_source(symbol), "futu")
            self.assertEqual(resolve_kline_source(symbol, "yahoo"), "futu")
            self.assertEqual(resolve_kline_source(symbol, "finnhub"), "futu")

    def test_us_quote_still_finnhub(self):
        self.assertEqual(resolve_quote_source("AAPL"), "finnhub")

    def test_cn_hk_kline_futu(self):
        self.assertEqual(resolve_kline_source("00700.HK"), "futu")
        self.assertEqual(resolve_kline_source("600519.SH"), "futu")

    def test_us_source_chain_futu_then_yahoo(self):
        self.assertEqual(kline_source_chain("AAPL"), ["futu", "yahoo"])
        self.assertEqual(kline_source_chain("TSLA"), ["futu", "yahoo"])


class FutuHistoryKlineTest(unittest.TestCase):
    def setUp(self):
        _ensure_futu_stub()

    def _adapter_with_ctx(self, ctx):
        with patch("app.data.adapter.get_effective_config", return_value=_cfg()):
            adapter = FutuAdapter(host="127.0.0.1", port=11111)
        adapter._quote_ctx = ctx
        adapter._connected = True
        adapter.last_error = None
        return adapter

    def test_paginated_request_history_kline(self):
        ctx = MagicMock()
        page1 = FakeFrame([_bar_row("2026-01-02 09:30:00", 10), _bar_row("2026-01-02 09:35:00", 11)])
        page2 = FakeFrame([_bar_row("2026-01-02 09:40:00", 12)])
        ctx.request_history_kline.side_effect = [
            (0, page1, b"next-page"),
            (0, page2, None),
        ]
        adapter = self._adapter_with_ctx(ctx)
        bars = adapter.get_klines("AAPL", "5m", "2026-01-02T00:00:00", "2026-01-02T16:00:00")
        self.assertEqual(len(bars), 3)
        self.assertEqual(ctx.request_history_kline.call_count, 2)
        first = ctx.request_history_kline.call_args_list[0].kwargs
        second = ctx.request_history_kline.call_args_list[1].kwargs
        self.assertEqual(first["max_count"], 1000)
        self.assertIsNone(first["page_req_key"])
        self.assertEqual(second["page_req_key"], b"next-page")
        self.assertEqual(first["start"], "2026-01-02")
        self.assertEqual(first["code"], "US.AAPL")
        self.assertEqual([bar.timestamp for bar in bars], [
            "2026-01-02 09:30:00",
            "2026-01-02 09:35:00",
            "2026-01-02 09:40:00",
        ])
        self.assertIsNone(adapter.last_error)

    def test_timeframes_map_to_kltype(self):
        futu = _ensure_futu_stub()
        ctx = MagicMock()
        ctx.request_history_kline.return_value = (0, FakeFrame([_bar_row("2026-01-02 00:00:00")]), None)
        adapter = self._adapter_with_ctx(ctx)
        expected = {
            "5m": futu.KLType.K_5M,
            "30m": futu.KLType.K_30M,
            "1h": futu.KLType.K_60M,
            "60m": futu.KLType.K_60M,
            "1d": futu.KLType.K_DAY,
            "1m": futu.KLType.K_1M,
            "15m": futu.KLType.K_15M,
            "1w": futu.KLType.K_WEEK,
        }
        for timeframe, ktype in expected.items():
            ctx.reset_mock()
            ctx.request_history_kline.return_value = (0, FakeFrame([_bar_row("2026-01-02 00:00:00")]), None)
            bars = adapter.get_klines("TSLA", timeframe, "2026-01-01", "2026-01-03")
            self.assertEqual(len(bars), 1, timeframe)
            self.assertEqual(ctx.request_history_kline.call_args.kwargs["ktype"], ktype, timeframe)

    def test_connects_opend_when_disconnected(self):
        ctx = MagicMock()
        ctx.request_history_kline.return_value = (0, FakeFrame([_bar_row("2026-01-02 00:00:00", 20)]), None)
        with patch("app.data.adapter.get_effective_config", return_value=_cfg()):
            adapter = FutuAdapter(host="127.0.0.1", port=11111)
        self.assertFalse(adapter.is_connected())

        def fake_connect():
            adapter._quote_ctx = ctx
            adapter._connected = True
            adapter.last_error = None
            return True

        adapter.connect = fake_connect
        bars = adapter.get_klines("ARM", "1d", "2026-01-01", "2026-02-01")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 20.5)
        self.assertTrue(ctx.request_history_kline.called)
        ctx.get_cur_kline.assert_not_called()
        ctx.subscribe.assert_not_called()


class Yahoo429Test(unittest.TestCase):
    def setUp(self):
        YahooAdapter.reset_backoff()

    def tearDown(self):
        YahooAdapter.reset_backoff()

    def _adapter(self):
        with patch("app.data.adapter.get_effective_config", return_value=_cfg()):
            adapter = YahooAdapter()
        adapter.connect()
        return adapter

    def test_http_429_raises_soft_and_backs_off(self):
        adapter = self._adapter()
        headers = EmailMessage()
        headers["Retry-After"] = "120"
        err = urllib.error.HTTPError(
            url="https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=io.BytesIO(b""),
        )
        with patch("urllib.request.urlopen", side_effect=err) as mocked:
            with self.assertRaises(YahooRateLimitError) as ctx:
                adapter.get_klines("AAPL", "1d", "2026-01-01", "2026-02-01")
            mocked.assert_called_once()
        self.assertTrue(YahooAdapter.in_backoff())
        self.assertIn("429", str(ctx.exception))
        self.assertTrue(is_rate_limited(ctx.exception))

        with patch("urllib.request.urlopen") as mocked_again:
            with self.assertRaises(YahooRateLimitError):
                adapter.get_klines("AAPL", "1d", "2026-01-01", "2026-02-01")
            mocked_again.assert_not_called()


class EnsureLocalKlineSoftFailTest(unittest.TestCase):
    def test_429_returns_local_bars(self):
        local = _local_rows(8)
        calls = []

        def fake_backfill(**kwargs):
            calls.append(kwargs["source"])
            if kwargs["source"] == "futu":
                raise RuntimeError("OpenD down")
            raise YahooRateLimitError("Yahoo 429 Too Many Requests; backoff 60s")

        with patch("app.data.history_backfill.is_kline_range_covered", return_value=False), \
             patch("app.data.history_backfill.backfill_kline_range", side_effect=fake_backfill), \
             patch("app.data.history_backfill.get_kline_bars", return_value=local):
            result = ensure_local_kline_range(
                "AAPL", "1d", "2024-01-01T00:00:00", "2024-03-01T00:00:00"
            )
        self.assertEqual(calls, ["futu", "yahoo"])
        self.assertEqual(result["bars"], local)
        self.assertTrue(result["degraded"])
        self.assertIn("429", result["error"])

    def test_missing_local_still_raises(self):
        with patch("app.data.history_backfill.is_kline_range_covered", return_value=False), \
             patch("app.data.history_backfill.backfill_kline_range", side_effect=YahooRateLimitError("429")), \
             patch("app.data.history_backfill.get_kline_bars", return_value=[]):
            with self.assertRaises(RuntimeError) as ctx:
                ensure_local_kline_range("AAPL", "5m", "2024-01-01T00:00:00", "2024-01-10T00:00:00")
            self.assertIn("429", str(ctx.exception))


class ChanAnalyzeLocalBarsTest(unittest.TestCase):
    def test_local_bars_keep_analyze_alive_on_429(self):
        from app.api.chan import chan_analyze

        rows = _local_rows(80)
        with patch("app.api.chan.ensure_local_kline_range", side_effect=YahooRateLimitError("Yahoo 429")), \
             patch("app.api.chan.get_kline_bars", return_value=rows):
            out = asyncio.run(chan_analyze(
                symbol="AAPL",
                timeframe="1d",
                limit=400,
                adapter="finnhub",
                host="127.0.0.1",
                port=11111,
            ))
        self.assertEqual(out["symbol"], "AAPL")
        self.assertEqual(out["source"], "futu")
        self.assertTrue(out["degraded"])
        self.assertGreaterEqual(out["bar_count"], 1)
        self.assertNotIn("auto-order", out)

    def test_empty_local_still_502(self):
        from fastapi import HTTPException
        from app.api.chan import chan_analyze

        with patch("app.api.chan.ensure_local_kline_range", side_effect=YahooRateLimitError("Yahoo 429")), \
             patch("app.api.chan.get_kline_bars", return_value=[]):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(chan_analyze(
                    symbol="AAPL",
                    timeframe="30m",
                    limit=400,
                    adapter="finnhub",
                    host="127.0.0.1",
                    port=11111,
                ))
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
