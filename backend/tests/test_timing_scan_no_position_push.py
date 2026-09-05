"""时机扫描不得附带管仓推送(process_position_alerts / 裸奔)。

管仓仅走 _position_alert_loop(alert_push_minutes) 与手动 push_position_alerts。
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]


def _wheel_scan_body() -> str:
    src = (BACKEND / "app" / "api" / "leaps.py").read_text(encoding="utf-8")
    start = src.index("def _run_wheel_scan")
    end = src.index("\n@router.get(\"/signals/", start)
    return src[start:end]


def test_run_wheel_scan_source_has_no_position_alert_side_path():
    body = _wheel_scan_body()
    assert "process_position_alerts" not in body
    assert "check_open_positions_core" not in body
    assert "UNCOV." not in body
    assert "管仓|裸奔" not in body
    assert "alert_from_wheel_signal" in body
    assert "run_chan_alert_cycle" in body


def test_run_wheel_scan_does_not_call_process_position_alerts():
    """运行空扫描时不应触发管仓体检/推送(不依赖 numpy/富途)。"""
    from app.api import leaps

    fake_monitor = MagicMock()
    fake_monitor.scan_all.return_value = []

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

    wheel_timing_scan_patch = types.ModuleType("app.core.wheel_timing_scan_patch")

    alert_engine = types.ModuleType("app.services.alert_engine")
    alert_engine.process_position_alerts = MagicMock()
    alert_engine.send_and_log = MagicMock()

    wheel_api = types.ModuleType("app.api.wheel")
    wheel_api.check_open_positions_core = MagicMock(
        return_value={"items": [{"symbol": "AAPL"}]}
    )

    notifier = types.ModuleType("app.services.notifier")
    notifier.timing_channel_kind = MagicMock(return_value=None)
    notifier.resolve_telegram_channel = MagicMock(
        return_value={"enabled": False}
    )
    notifier.TelegramNotifier = MagicMock()

    modules = {
        "app.core.leaps_monitor": leaps_monitor,
        "app.core.touch_best": touch_best,
        "app.core.sim_wheel": sim_wheel,
        "app.core.wheel_timing_scan_patch": wheel_timing_scan_patch,
        "app.services.chan_alerts": chan_alerts,
        "app.services.alert_engine": alert_engine,
        "app.api.wheel": wheel_api,
        "app.services.notifier": notifier,
    }

    with patch.object(leaps, "_load_config", return_value={
        "wheel_timing": {"push_min_iv_rank": 50, "push_strong_only": True},
        "futu": {"host": "127.0.0.1", "port": 11111},
    }), patch.dict(sys.modules, modules):
        leaps._run_wheel_scan(symbol=None)

    alert_engine.process_position_alerts.assert_not_called()
    wheel_api.check_open_positions_core.assert_not_called()
    chan_alerts.run_chan_alert_cycle.assert_called_once()
    fake_monitor.scan_all.assert_called_once()


def test_position_alert_hook_owns_uncovered():
    """裸奔迁入 services 管仓挂钩,手动/定时 API 仍覆盖。"""
    src = (BACKEND / "app" / "services" / "__init__.py").read_text(encoding="utf-8")
    assert "UNCOV." in src
    assert "管仓|裸奔" in src
    assert "process_position_alerts" in src


def test_leaps_module_ast_wheel_scan_no_position_imports():
    """静态: _run_wheel_scan 函数体内无管仓相关 import。"""
    path = BACKEND / "app" / "api" / "leaps.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_run_wheel_scan":
            fn = node
            break
    assert fn is not None
    banned = {"process_position_alerts", "check_open_positions_core", "send_and_log"}
    found = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.ImportFrom):
            for alias in n.names:
                if alias.name in banned:
                    found.add(alias.name)
        elif isinstance(n, ast.Name) and n.id in banned:
            found.add(n.id)
    assert not found, f"_run_wheel_scan still references {found}"
