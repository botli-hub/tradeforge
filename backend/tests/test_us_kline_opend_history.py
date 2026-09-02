"""美股 K 线：OpenD 历史回填优先，Yahoo 429 软失败。无 live OpenD / Yahoo。"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.futu_history_kline import (  # noqa: E402
    paginate_request_history_kline,
    to_futu_time,
)
from app.data.kline_errors import KlineRateLimited, is_rate_limited_error  # noqa: E402
from app.data.source_router import resolve_kline_source  # noqa: E402

CFG = {
    "futu": {"host": "127.0.0.1", "port": 11111},
    "yahoo_base_url": "http://yahoo.test/v8/finance/chart",
    "finnhub_api_key": "",
    "finnhub_base_url": "http://finnhub.test",
}


class FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


def _bar_row(ts="2026-01-02 09:30:00"):
    return {
        "time_key": ts,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 100,
    }


def _local_rows(n=80):
    rows = []
    for i in range(n):
        rows.append({
            "ts": f"2026-01-02T09:{i % 60:02d}:00",
            "timestamp": f"2026-01-02T09:{i % 60:02d}:00",
            "open": 10.0 + i * 0.01,
            "high": 11.0 + i * 0.01,
            "low": 9.5,
            "close": 10.5 + i * 0.01,
            "volume": 100 + i,
        })
    return rows


def test_router_prefers_futu_for_us_when_adapter_available():
    with patch("app.data.source_router.is_futu_adapter_available", return_value=True):
        assert resolve_kline_source("AAPL") == "futu"
        assert resolve_kline_source("SPCX") == "futu"
        assert resolve_kline_source("US.AAPL") == "futu"
        assert resolve_kline_source("AAPL", "yahoo") == "futu"
        assert resolve_kline_source("AAPL", "finnhub") == "futu"
        assert resolve_kline_source("600519.SH") == "futu"
        assert resolve_kline_source("00700.HK") == "futu"
    with patch("app.data.source_router.is_futu_adapter_available", return_value=False):
        assert resolve_kline_source("AAPL") == "yahoo"
        assert resolve_kline_source("SPCX") == "yahoo"


def test_futu_history_pagination_uses_page_req_key():
    calls = []

    def fake_request(code, **kwargs):
        calls.append({"code": code, "page_req_key": kwargs.get("page_req_key")})
        if kwargs.get("page_req_key") is None:
            return 0, FakeFrame([_bar_row("2026-01-02 09:30:00")]), "next-page"
        return 0, FakeFrame([_bar_row("2026-01-02 09:35:00")]), None

    rows, err = paginate_request_history_kline(
        fake_request,
        code="US.SPCX",
        start="2026-01-01 00:00:00",
        end="2026-01-02 00:00:00",
        ktype="K_5M",
        autype="QFQ",
        ret_ok=0,
    )
    assert err is None
    assert [c["page_req_key"] for c in calls] == [None, "next-page"]
    assert [r["timestamp"] for r in rows] == ["2026-01-02 09:30:00", "2026-01-02 09:35:00"]
    assert to_futu_time("2026-01-01T00:00:00") == "2026-01-01 00:00:00"


def test_futu_get_klines_calls_request_history_kline():
    from app.data.adapter import FutuAdapter

    with patch("app.data.adapter.get_effective_config", return_value=CFG):
        adapter = FutuAdapter()
    ctx = MagicMock()
    ctx.request_history_kline.return_value = (0, FakeFrame([_bar_row()]), None)
    adapter._quote_ctx = ctx
    adapter._connected = True

    bars = adapter.get_klines(
        "AAPL", "5m", "2026-01-01T00:00:00", "2026-01-02T00:00:00",
    )
    assert ctx.request_history_kline.called
    assert ctx.get_cur_kline.called is False
    assert ctx.subscribe.called is False
    assert len(bars) == 1
    assert ctx.request_history_kline.call_args[0][0] == "US.AAPL"

    for tf in ("30m", "1h", "60m", "1d"):
        ctx.request_history_kline.reset_mock()
        adapter.get_klines("SPCX", tf, "2026-01-01T00:00:00", "2026-01-02T00:00:00")
        assert ctx.request_history_kline.called, tf
        assert ctx.request_history_kline.call_args[0][0] == "US.SPCX"


def test_futu_get_klines_connects_instead_of_empty():
    from app.data.adapter import FutuAdapter

    with patch("app.data.adapter.get_effective_config", return_value=CFG):
        adapter = FutuAdapter()
    ctx = MagicMock()
    ctx.request_history_kline.return_value = (0, FakeFrame([_bar_row()]), None)

    def fake_connect():
        adapter._connected = True
        adapter._quote_ctx = ctx
        adapter.last_error = None
        return True

    adapter._connected = False
    adapter._quote_ctx = None
    adapter.connect = fake_connect  # type: ignore[method-assign]
    bars = adapter.get_klines("AAPL", "30m", "2026-01-01T00:00:00", "2026-01-02T00:00:00")
    assert len(bars) == 1
    assert ctx.request_history_kline.called


def test_yahoo_429_soft_error_not_hard_exception():
    from app.data.adapter import YahooAdapter

    http_err = HTTPError("http://yahoo.test/AAPL", 429, "Too Many Requests", hdrs=None, fp=None)
    with patch("app.data.adapter.get_effective_config", return_value=CFG):
        adapter = YahooAdapter(base_url=CFG["yahoo_base_url"])
    adapter.connect()
    with patch("app.data.yahoo_http.YAHOO_429_RETRIES", 0), \
            patch("app.data.yahoo_http.urllib.request.urlopen", side_effect=http_err), \
            patch("app.data.yahoo_http.time.sleep"):
        bars = adapter.get_klines("AAPL", "5m", "2026-01-01T00:00:00", "2026-01-02T00:00:00")
    assert bars == []
    assert is_rate_limited_error(adapter.last_error)
    assert "429" in (adapter.last_error or "")


def test_ensure_429_returns_local_bars():
    from app.data.history_backfill import ensure_local_kline_range

    local = _local_rows(3)
    with patch("app.data.history_backfill.is_kline_range_covered", return_value=False), \
            patch(
                "app.data.history_backfill.backfill_kline_range",
                side_effect=KlineRateLimited("Yahoo HTTP 429 Too Many Requests", retry_after=30),
            ), \
            patch("app.data.history_backfill.get_kline_bars", return_value=local), \
            patch("app.data.history_backfill.resolve_history_source", return_value="futu"), \
            patch("app.data.history_backfill.normalize_symbol", side_effect=lambda s: s.upper()), \
            patch("app.data.history_backfill.normalize_ts", side_effect=lambda s: s):
        result = ensure_local_kline_range(
            "AAPL", "5m", "2026-01-01T00:00:00", "2026-01-02T00:00:00",
        )
    assert result["bars"] == local
    assert result.get("degraded") is True


def test_chan_yahoo_429_is_not_502():
    from fastapi import HTTPException
    from app.api.chan import chan_analyze

    with patch(
        "app.api.chan.ensure_local_kline_range",
        side_effect=KlineRateLimited("Yahoo HTTP 429 Too Many Requests", retry_after=30),
    ), patch("app.api.chan.get_kline_bars", return_value=[]):
        try:
            asyncio.run(chan_analyze(symbol="AAPL", timeframe="5m", limit=400))
            raise AssertionError("expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code != 502, exc.status_code
            assert exc.status_code == 429


def test_chan_local_bars_keep_analyze_alive():
    from app.api.chan import chan_analyze

    rows = _local_rows(80)
    payload = {"fractals": [], "bi": [], "duan": [], "buy_sell": [], "bar_count": len(rows)}
    with patch(
        "app.api.chan.ensure_local_kline_range",
        side_effect=KlineRateLimited("Yahoo HTTP 429 Too Many Requests", retry_after=30),
    ), patch("app.api.chan.get_kline_bars", return_value=rows), \
            patch("app.api.chan.analyze", return_value=dict(payload)):
        # Direct handler call skips FastAPI Query unwrap; pass int limit.
        out = asyncio.run(chan_analyze(symbol="AAPL", timeframe="5m", limit=400))
    assert out["symbol"] == "AAPL"
    assert out["bar_count"] == 80
    assert out["source"] in ("futu", "yahoo")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
