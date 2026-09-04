"""LEAPS Put 权利金卖出信号核心引擎

信号三条件（S1 & S2 & S3 同时满足）：
  S1: 合约当日最高价（盘中用最新价）≥ EMA50（一级）或 ≥ EMA200（二级强信号）
  S2: 合约当前 IV ≥ 自身 52 周 IV 70 分位
  S3: 标的现价 > 接货底线价
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.data import leaps_repository as repo
from app.core.wheel_expiries import pick_windowed_expiries, select_expiries  # noqa: F401
from app.core.wheel_timing_klines import (
    TIMEFRAME_DAY,
    TIMEFRAME_HOUR,
    bars_on_day,
    ema_touch,
    futu_kl_names,
    normalize_timeframe,
    resolve_scan_timeframe,
    snapshot_bar,
    bar_timestamp,
)

logger = logging.getLogger(__name__)

# ── 富途接口节流:到期日/期权链/快照类接口限频约 10 次/30 秒 ─────────────────────
_QUOTA_LOCK = threading.Lock()
_LAST_QUOTA_CALL = {"t": 0.0}


def _throttle(min_interval: float = 3.2):
    """保证相邻 quota 类请求间隔 >= min_interval 秒(线程安全)"""
    with _QUOTA_LOCK:
        now = time.monotonic()
        wait = _LAST_QUOTA_CALL["t"] + min_interval - now
        if wait > 0:
            time.sleep(wait)
        _LAST_QUOTA_CALL["t"] = time.monotonic()
