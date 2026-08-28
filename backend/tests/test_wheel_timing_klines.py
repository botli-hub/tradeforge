"""Call 触线 1h / Put 日K：无 OpenD。"""
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_timing_klines import (  # noqa: E402
    TIMEFRAME_DAY,
    TIMEFRAME_HOUR,
    bars_on_day,
    call_holding_cycles,
    call_strike_min,
    default_timeframe,
    ema_touch,
    futu_kl_names,
    history_key,
    normalize_timeframe,
    resolve_scan_timeframe,
)


def test_non_holding_not_scanned_for_call():
    cycles = [
        {"id": "idle", "status": "IDLE"},
        {"id": "csp", "status": "CSP_OPEN"},
        {"id": "hold", "status": "HOLDING"},
        {"id": "cc", "status": "CC_OPEN"},
        {"id": "closed", "status": "CLOSED"},
    ]
    pool = call_holding_cycles(cycles)
    assert [c["id"] for c in pool] == ["hold"]
    assert call_holding_cycles([]) == []
    assert call_holding_cycles(None) == []


def test_ema_touch_1h_hit_ema50():
    closes = pd.Series([1.0] * 60)
    hit = ema_touch(
        closes, 1.05,
        ema50_min=50, ema200_min=200, allow_partial_ema=True,
        level_map={"EMA50": "WHEEL_CALL", "EMA200": "WHEEL_CALL"},
    )
    assert hit is not None
    assert hit["ema_type"] == "EMA50"
    assert hit["signal_level"] == "WHEEL_CALL"
    assert hit["ema_value"] > 0
    assert 1.05 >= hit["ema_value"]


def test_ema_touch_miss_below_ema():
    closes = pd.Series([2.0] * 60)
    hit = ema_touch(
        closes, 1.0,
        ema50_min=50, ema200_min=200, allow_partial_ema=True,
        level_map={"EMA50": "WHEEL_CALL", "EMA200": "WHEEL_CALL"},
    )
    assert hit is None


def test_ema_touch_high_also_hits():
    # last/high 同一比较：high 当 trigger
    closes = pd.Series([1.0] * 60)
    high = 1.2
    last = 0.5
    assert ema_touch(closes, last, ema50_min=50, ema200_min=200, allow_partial_ema=True) is None
    hit = ema_touch(closes, high, ema50_min=50, ema200_min=200, allow_partial_ema=True)
    assert hit is not None
    assert hit["ema_type"] == "EMA50"


def test_put_stays_daily_kltype():
    assert default_timeframe("PUT") == TIMEFRAME_DAY
    assert default_timeframe("CALL") == TIMEFRAME_HOUR
    assert default_timeframe("put") == TIMEFRAME_DAY
    assert resolve_scan_timeframe("PUT") == "1d"
    assert resolve_scan_timeframe("CALL") == "1h"
    assert resolve_scan_timeframe("PUT", "1h") == "1h"  # 显式覆盖
    assert resolve_scan_timeframe("CALL", "1d") == "1d"
    assert futu_kl_names("1d") == ("K_DAY", "K_DAY")
    assert futu_kl_names("day") == ("K_DAY", "K_DAY")
    assert futu_kl_names("1h") == ("K_60M", "K_60M")
    assert futu_kl_names(TIMEFRAME_HOUR) == ("K_60M", "K_60M")
    # PUT 默认不得变成 1h
    kl, sub = futu_kl_names(default_timeframe("PUT"))
    assert kl == "K_DAY" and sub == "K_DAY"


def test_history_key_distinguishes_1h_vs_daily():
    code = "US.AAPL260918C00200000"
    k_d = history_key(code, "1d")
    k_h = history_key(code, "1h")
    assert k_d == (code, "1d")
    assert k_h == (code, "1h")
    assert k_d != k_h
    assert history_key(code, "day") == k_d
    assert history_key(code, "60m") == k_h


def test_call_strike_min_uses_sell_above():
    assert call_strike_min(100, 120) == 120
    assert call_strike_min(100, None) == 100
    assert call_strike_min(100, 0) == 100
    assert call_strike_min(90, 80) == 90
    assert call_strike_min(None, None) is None


def test_bars_on_day_matches_1h_prefix():
    hist = [
        {"date": "2026-08-28", "close": 1},
        {"date": "2026-08-28 10:00:00", "close": 2},
        {"date": "2026-08-27 15:00:00", "close": 3},
    ]
    day = bars_on_day(hist, "2026-08-28")
    assert [b["close"] for b in day] == [1, 2]


def test_scan_all_call_only_holding_put_requests_day():
    """scan_all 循环：非 HOLDING 不扫 CALL；PUT 仍传 timeframe=1d / CALL=1h。"""
    from app.core.leaps_monitor import WheelTimingMonitor

    captured = []
    wt = WheelTimingMonitor({"wheel_timing": {}})

    def fake_scan(*_a, **kw):
        captured.append(kw)
        return []

    wt.monitor.scan_symbol = fake_scan
    targets = [{
        "symbol": "AAA", "enabled": True, "floor_price": 10,
        "dte_min": 21, "dte_max": 45, "sell_above": 110,
    }]
    cycles = [
        {"status": "IDLE", "cost_basis": 0},
        {"status": "HOLDING", "cost_basis": 100},
        {"status": "CSP_OPEN", "cost_basis": 0},
        {"status": "CC_OPEN", "cost_basis": 100},
    ]
    with patch("app.data.wheel_repository.get_targets", return_value=targets), \
         patch("app.data.wheel_repository.get_active_cycles", return_value=cycles), \
         patch("app.data.leaps_repository.upsert_timing_history"), \
         patch("app.core.wheel_timing_progress.update"), \
         patch("app.core.wheel_call_timing.get_target_sell_above", return_value=110):
        wt.scan_all()

    sides = [(c.get("option_type"), c.get("timeframe"), c.get("strike_min")) for c in captured]
    put = [c for c in captured if c.get("option_type") == "PUT"]
    call = [c for c in captured if c.get("option_type") == "CALL"]
    assert len(put) == 1, sides
    assert put[0].get("timeframe") == "1d"
    assert futu_kl_names(put[0]["timeframe"])[0] == "K_DAY"
    assert len(call) == 1, "non-HOLDING must not be scanned for CALL"
    assert call[0].get("timeframe") == "1h"
    assert futu_kl_names(call[0]["timeframe"])[0] == "K_60M"
    # sell_above 110 > cost 100
    assert call[0].get("strike_min") == 110


def test_normalize_aliases():
    assert normalize_timeframe("K_DAY") == "1d"
    assert normalize_timeframe("K_60M") == "1h"


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
