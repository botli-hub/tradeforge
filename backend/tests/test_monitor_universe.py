"""触线 / 缠论宇宙与 wheel enabled 解耦。不连 OpenD。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import DEFAULT_CONFIG  # noqa: E402
from app.core.monitor_universe import (  # noqa: E402
    filter_monitor_symbols,
    monitor_exclude,
    touch_scan_targets,
)
from app.services.chan_alerts import resolve_universe  # noqa: E402

WHEEL_FIVE = ["SPCH", "SPCX", "TSLL", "TSLA", "TQQQ"]


def _targets():
    return [
        {"symbol": sym, "enabled": True, "floor_price": 1}
        for sym in WHEEL_FIVE
    ]


def test_default_excludes_tsll_spch_from_touch_and_chan():
    assert monitor_exclude(DEFAULT_CONFIG, "touch") == ["TSLL", "SPCH"]
    assert monitor_exclude(DEFAULT_CONFIG, "chan") == ["TSLL", "SPCH"]
    assert DEFAULT_CONFIG["wheel_timing"]["expiry_mode"] == "near_weekly_monthly"
    assert DEFAULT_CONFIG["wheel_timing"]["weekly_weeks"] == 4
    assert DEFAULT_CONFIG["wheel_timing"]["monthly_months"] == 3


def test_enabled_five_stay_enabled_touch_drops_tsll_spch():
    targets = _targets()
    touched = touch_scan_targets(targets, DEFAULT_CONFIG)
    assert [t["symbol"] for t in touched] == ["SPCX", "TSLA", "TQQQ"]
    assert [t["symbol"] for t in targets if t.get("enabled")] == WHEEL_FIVE
    assert all(t["enabled"] is True for t in targets)


def test_touch_and_chan_excludes_can_differ():
    cfg = {
        "monitor_universe": {
            "touch_exclude": ["TSLL"],
            "chan_exclude": ["SPCH"],
        }
    }
    assert [t["symbol"] for t in touch_scan_targets(_targets(), cfg)] == [
        "SPCH", "SPCX", "TSLA", "TQQQ",
    ]
    assert resolve_universe(cfg, targets=_targets()) == [
        "SPCX", "TSLL", "TSLA", "TQQQ",
    ]


def test_shared_exclude_applies_to_both():
    cfg = {"monitor_universe": {"exclude": ["TQQQ"]}}
    assert "TQQQ" not in [t["symbol"] for t in touch_scan_targets(_targets(), cfg)]
    assert "TQQQ" not in resolve_universe(cfg, targets=_targets())


def test_chan_explicit_symbols_still_honor_exclude():
    cfg = {
        "monitor_universe": {"chan_exclude": ["TSLL", "SPCH"]},
        "chan_alerts": {"symbols": ["TSLL", "SPCX", "SPCH", "spcx"]},
    }
    assert resolve_universe(cfg, targets=_targets()) == ["SPCX"]


def test_disabled_target_stays_out_of_touch_even_if_not_excluded():
    targets = _targets()
    targets[1]["enabled"] = 0  # SPCX
    assert [t["symbol"] for t in touch_scan_targets(targets, {"monitor_universe": {}})] == [
        "SPCH", "TSLL", "TSLA", "TQQQ",
    ]


def test_filter_monitor_symbols_order_and_blanks():
    assert filter_monitor_symbols(
        [" tsll ", "SPCX", "TSLL", "", "TQQQ"],
        {"monitor_universe": {"touch_exclude": ["TSLL"]}},
        "touch",
    ) == ["SPCX", "TQQQ"]


def test_scan_all_skips_excluded_keeps_other_enabled():
    from app.core.leaps_monitor import WheelTimingMonitor

    wt = WheelTimingMonitor(DEFAULT_CONFIG)
    assert wt.monitor.expiry_mode == "near_weekly_monthly"
    assert wt.monitor.weekly_weeks == 4
    assert wt.monitor.monthly_months == 3
    seen = []

    def fake_one(t, ti, n_targets, is_intraday, report, prog_lock):
        seen.append(t["symbol"])
        return []

    targets = _targets() + [{"symbol": "OFF", "enabled": False, "floor_price": 1}]
    with patch("app.data.wheel_repository.get_targets", return_value=targets), \
         patch("app.data.leaps_repository.upsert_timing_history"), \
         patch("app.core.wheel_timing_progress.update"), \
         patch.object(wt, "_scan_one_target", side_effect=fake_one):
        out = wt.scan_all()
    assert out == []
    assert sorted(seen) == ["SPCX", "TQQQ", "TSLA"]
    assert all(t["enabled"] for t in targets if t["symbol"] in WHEEL_FIVE)
