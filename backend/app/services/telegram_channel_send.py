"""Channel-aware Telegram send used by alert_engine."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from app.services.notifier import TelegramNotifier


def send_telegram_logged(
    text: str,
    *,
    category: str,
    fingerprint: str = "",
    title: str = "",
    meta: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    cfg: Optional[Dict[str, Any]] = None,
    channel_kind: Optional[str] = None,
    log_push_fn: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """按 channel_kind 发 TG 并记日志。拆分频道缺配置 → channel_silent,不回落全局 bot。"""
    if dry_run:
        return {"ok": True, "reason": "dry_run", "sent": False}
    cfg = cfg or {}
    kind = channel_kind or ("chan" if category == "chan" else "legacy")
    notifier = TelegramNotifier.from_channel(kind, cfg)

    def _noop_log(**kwargs: Any) -> None:
        return None

    log_fn = log_push_fn or _noop_log
    if not notifier._enabled:
        reason = "channel_silent" if notifier.channel_silent else "not_configured"
        log_fn(
            category=category, body=text, status="skipped", reason=reason,
            fingerprint=fingerprint, title=title, meta=meta,
        )
        return {"ok": False, "reason": reason, "sent": False}
    detail = notifier.send_detailed(text)
    ok = bool(detail.get("ok"))
    log_fn(
        category=category,
        body=text,
        status="sent" if ok else "failed",
        reason=detail.get("reason") or ("ok" if ok else "fail"),
        fingerprint=fingerprint,
        title=title,
        meta=meta,
    )
    return {"ok": ok, "reason": detail.get("reason"), "sent": ok}
