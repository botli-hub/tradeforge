"""Yahoo HTTP 拉取：429 软失败 + 退避重试。"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Callable, Optional, Union

from app.data.kline_errors import KlineRateLimited

YAHOO_429_RETRIES = 2
YAHOO_429_BACKOFF_SEC = 1.0
YAHOO_429_BACKOFF_CAP_SEC = 30.0


def _retry_after_seconds(exc: urllib.error.HTTPError, fallback: float) -> float:
    try:
        headers = exc.headers
        raw = headers.get("Retry-After") if headers is not None else None
        if raw is not None:
            return max(float(raw), 0.0)
    except Exception:
        pass
    return max(float(fallback), 0.0)


def urlopen_with_429_backoff(
    request: Union[urllib.request.Request, str],
    timeout: float = 15,
    retries: Optional[int] = None,
    backoff_sec: Optional[float] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """读取响应体。持续 429 时退避重试，耗尽后抛 KlineRateLimited（不是硬错误）。"""
    attempts = (YAHOO_429_RETRIES if retries is None else retries) + 1
    base = YAHOO_429_BACKOFF_SEC if backoff_sec is None else backoff_sec
    last_exc: Optional[BaseException] = None

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            last_exc = exc
            wait = _retry_after_seconds(exc, base * (2 ** attempt))
            wait = min(wait, YAHOO_429_BACKOFF_CAP_SEC)
            if attempt < attempts - 1:
                sleep(wait)
                continue
            raise KlineRateLimited(
                "Yahoo HTTP 429 Too Many Requests",
                retry_after=max(int(wait) or 60, 1),
            ) from exc

    raise KlineRateLimited("Yahoo HTTP 429 Too Many Requests") from last_exc
