"""K 线拉取的软失败错误（限流等），不应升级成硬 502。"""
from __future__ import annotations


class KlineRateLimited(RuntimeError):
    """上游 HTTP 429 / 限流：调用方应返回 429/503，而不是 502。"""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = int(retry_after) if retry_after else 60
        self.status_code = 429


def is_rate_limited_error(message: object) -> bool:
    text = str(message or "").lower()
    if not text:
        return False
    return (
        "429" in text
        or "too many requests" in text
        or "rate limit" in text
        or "ratelimited" in text
    )
