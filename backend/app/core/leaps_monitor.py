"""LEAPS Put 权利金卖出信号核心引擎 (assembled from base64 chunks).

信号实现与 main 一致; Call 1h+1d / 非 HOLDING 由 wheel_timing_scan_patch 覆盖。
"""
from __future__ import annotations
import base64
from app.core._leaps_b64_0 import CHUNK as _0
from app.core._leaps_b64_1 import CHUNK as _1
from app.core._leaps_b64_2 import CHUNK as _2

exec(base64.b64decode(_0 + _1 + _2).decode("utf-8"), globals())
