"""触线扫描进度(独立模块,避免 leaps ↔ leaps_monitor 循环导入导致进度写不进去)。"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "signals_found": 0,
    "report": [],
    "error": None,
    "telegram_configured": False,
    "telegram_sent": 0,
    "phase": "idle",  # idle | timing | done | error
    "symbol": None,
    "side": None,
    "expiry": None,
    "contract_i": 0,
    "contract_n": 0,
    "target_i": 0,
    "target_n": 0,
    "message": "",
    "updated_at": None,
}


def get_state() -> Dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def update(**kwargs: Any) -> None:
    with _LOCK:
        _STATE.update(kwargs)
        _STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")


def reset_for_start(*, telegram_configured: bool = False) -> None:
    update(
        running=True,
        started_at=datetime.now().isoformat(timespec="seconds"),
        finished_at=None,
        signals_found=0,
        report=[],
        error=None,
        telegram_configured=telegram_configured,
        telegram_sent=0,
        phase="timing",
        symbol=None,
        side=None,
        expiry=None,
        contract_i=0,
        contract_n=0,
        target_i=0,
        target_n=0,
        message="触线扫描启动…",
    )


def mark_done(*, signals_found: int = 0, telegram_sent: int = 0, report: Optional[List[Any]] = None) -> None:
    update(
        running=False,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        signals_found=signals_found,
        telegram_sent=telegram_sent,
        report=report if report is not None else _STATE.get("report") or [],
        phase="done",
        message=f"触线完成 · 触发 {signals_found} 条",
        contract_i=0,
        contract_n=0,
    )


def mark_error(err: str) -> None:
    update(
        running=False,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        error=err,
        phase="error",
        message=f"触线失败: {err}",
    )
