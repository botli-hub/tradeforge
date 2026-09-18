"""时机扫描不得附带缠论推送(run_chan_alert_cycle)。

缠论仅走 _chan_alert_loop; 自动/手动(force) _run_wheel_scan 均不调用。
与 test_timing_scan_no_position_push 对称: 管仓/缠论各自专责, 不争 OpenD。
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


def test_run_wheel_scan_source_has_no_chan_alert_call():
    body = _wheel_scan_body()
    assert "run_chan_alert_cycle" not in body
    assert "_chan_alert_loop" in body or "缠论" in body  # 责任拆分注释
    assert "alert_from_wheel_signal" in body


def test_run_wheel_scan_force_does_not_call_chan_alert_cycle():
    """手动 force=True 时机扫描也不跑缠论(不依赖 numpy/富途)。"""
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
    sim_wheel.select_touch_batch_for_push = MagicMock(return_value=([], []))

    chan_alerts = types.ModuleType("app.services.chan_alerts")
    chan_alerts.run_chan_alert_cycle = MagicMock()
    chan_alerts.is_us_rth = MagicMock(return_value=True)

    wheel_timing_scan_patch = types.ModuleType("app.core.wheel_timing_scan_patch")

    alert_engine = types.ModuleType("app.services.alert_engine")
    alert_engine.process_position_alerts = MagicMock()
    alert_engine.send_and_log = MagicMock()

    wheel_api = types.ModuleType("app.api.wheel")
    wheel_api.check_open_positions_core = MagicMock(return_value={"items": []})

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
        leaps._run_wheel_scan(symbol=None, force=True)

    chan_alerts.run_chan_alert_cycle.assert_not_called()
    fake_monitor.scan_all.assert_called_once()


def test_chan_alert_loop_owns_run_chan_alert_cycle():
    """main._chan_alert_loop 仍是缠论唯一周期入口。"""
    src = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    assert "def _chan_alert_loop" in src
    assert "run_chan_alert_cycle" in src
    # 责任注释
    assert "不经 _run_wheel_scan" in src or "唯一入口" in src


def test_leaps_module_ast_wheel_scan_no_chan_cycle_call():
    """静态: _run_wheel_scan 函数体内不引用 run_chan_alert_cycle。"""
    path = BACKEND / "app" / "api" / "leaps.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_run_wheel_scan":
            fn = node
            break
    assert fn is not None
    banned = {"run_chan_alert_cycle"}
    found = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.ImportFrom):
            for alias in n.names:
                if alias.name in banned:
                    found.add(alias.name)
        elif isinstance(n, ast.Name) and n.id in banned:
            found.add(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in banned:
            found.add(n.attr)
    assert not found, f"_run_wheel_scan still references {found}"
