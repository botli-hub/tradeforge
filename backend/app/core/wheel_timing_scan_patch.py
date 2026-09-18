"""兼容导入:曾 monkey-patch WheelTimingMonitor.scan_all → Put/Call 1h+1d。

现逻辑已合入 leaps_monitor.WheelTimingMonitor.scan_all(含标的级并行 + OpenD 复用)。
保留本模块供 main / leaps API `import wheel_timing_scan_patch` 副作用,避免破窗。
不自动下单。
"""
from __future__ import annotations

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # 无 monkey-patch:以 leaps_monitor 为准
    _INSTALLED = True


try:
    install()
except Exception:
    pass
