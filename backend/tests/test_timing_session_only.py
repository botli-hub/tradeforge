"""wheel_timing.session_only: 自动扫仅美股 RTH;手动 force 绕过。"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fake_modules(fake_monitor: MagicMock):
    leaps_monitor = types.ModuleType("app.core.leaps_monitor")
    leaps_monitor.WheelTimingMonitor = MagicMock(return_value=fake_monitor)
    leaps_monitor.format_wheel_signal = MagicMock(return_value="msg")
    leaps_monitor.signal_strength = MagicMock(return_value="STRONG")

    touch_best = types.ModuleType("app.core.touch_best")
    touch_best.select_best_touch_signals = MagicMock(return_value=[])

    sim_wheel = types.ModuleType("app.core.sim_wheel")
    sim_wheel.alert_from_wheel_signal = MagicMock()
    sim_wheel.sim_on_alert = MagicMock()

    chan_alerts = types.ModuleType("app.services.chan_alerts")
    chan_alerts.run_chan_alert_cycle = MagicMock()
    chan_alerts.is_us_rth = MagicMock(return_value=False)

    wheel_timing_scan_patch = types.ModuleType("app.core.wheel_timing_scan_patch")

    notifier = types.ModuleType("app.services.notifier")
    notifier.timing_channel_kind = MagicMock(return_value=None)
    notifier.resolve_telegram_channel = MagicMock(return_value={"enabled": False})
    notifier.TelegramNotifier = MagicMock()

    return {
        "app.core.leaps_monitor": leaps_monitor,
        "app.core.touch_best": touch_best,
        "app.core.sim_wheel": sim_wheel,
        "app.core.wheel_timing_scan_patch": wheel_timing_scan_patch,
        "app.services.chan_alerts": chan_alerts,
        "app.services.notifier": notifier,
    }, chan_alerts


def test_default_config_session_only_true():
    from app.core.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["wheel_timing"].get("session_only") is True


def test_auto_scan_skips_when_not_rth():
    """自动路径(force=False) + session_only + 非 RTH → 不扫、不推。"""
    from app.api import leaps

    fake_monitor = MagicMock()
    fake_monitor.scan_all.return_value = []
    modules, chan_alerts = _fake_modules(fake_monitor)
    chan_alerts.is_us_rth = MagicMock(return_value=False)

    with patch.object(
        leaps,
        "_load_config",
        return_value={"wheel_timing": {"session_only": True, "push_min_iv_rank": 50}},
    ), patch.dict(sys.modules, modules):
        leaps._run_wheel_scan(symbol=None, force=False)

    fake_monitor.scan_all.assert_not_called()
    chan_alerts.run_chan_alert_cycle.assert_not_called()


def test_auto_scan_runs_during_rth():
    """自动路径 + session_only + RTH → 正常扫描。"""
    from app.api import leaps

    fake_monitor = MagicMock()
    fake_monitor.scan_all.return_value = []
    modules, chan_alerts = _fake_modules(fake_monitor)
    chan_alerts.is_us_rth = MagicMock(return_value=True)

    with patch.object(
        leaps,
        "_load_config",
        return_value={
            "wheel_timing": {
                "session_only": True,
                "push_min_iv_rank": 50,
                "push_strong_only": True,
            },
            "futu": {"host": "127.0.0.1", "port": 11111},
        },
    ), patch.dict(sys.modules, modules):
        leaps._run_wheel_scan(symbol=None, force=False)

    fake_monitor.scan_all.assert_called_once()
    chan_alerts.run_chan_alert_cycle.assert_called_once()


def test_manual_force_bypasses_session_gate():
    """手动 POST /wheel-scan 传 force=True,周末/盘外仍扫。"""
    from app.api import leaps

    fake_monitor = MagicMock()
    fake_monitor.scan_all.return_value = []
    modules, chan_alerts = _fake_modules(fake_monitor)
    chan_alerts.is_us_rth = MagicMock(return_value=False)

    with patch.object(
        leaps,
        "_load_config",
        return_value={
            "wheel_timing": {
                "session_only": True,
                "push_min_iv_rank": 50,
                "push_strong_only": True,
            },
            "futu": {"host": "127.0.0.1", "port": 11111},
        },
    ), patch.dict(sys.modules, modules):
        leaps._run_wheel_scan(symbol="AAPL", force=True)

    fake_monitor.scan_all.assert_called_once()
    # force 绕过时不应调用 is_us_rth(或即使调用也不影响)
    chan_alerts.run_chan_alert_cycle.assert_called_once()


def test_session_only_false_allows_weekend_auto():
    """显式关掉 session_only 时自动扫不看 RTH。"""
    from app.api import leaps

    fake_monitor = MagicMock()
    fake_monitor.scan_all.return_value = []
    modules, chan_alerts = _fake_modules(fake_monitor)
    chan_alerts.is_us_rth = MagicMock(return_value=False)

    with patch.object(
        leaps,
        "_load_config",
        return_value={
            "wheel_timing": {
                "session_only": False,
                "push_min_iv_rank": 50,
                "push_strong_only": True,
            },
            "futu": {"host": "127.0.0.1", "port": 11111},
        },
    ), patch.dict(sys.modules, modules):
        leaps._run_wheel_scan(symbol=None, force=False)

    fake_monitor.scan_all.assert_called_once()


def test_manual_endpoint_passes_force_true():
    """POST /wheel-scan 后台任务以 force=True 调用。"""
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "leaps.py").read_text(
        encoding="utf-8"
    )
    assert "background_tasks.add_task(_run_wheel_scan, body.symbol, True)" in src
    assert "def _run_wheel_scan(symbol: Optional[str] = None, force: bool = False)" in src
