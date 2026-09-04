"""Telegram 分频道:chan / timing_put / timing_call;缺配置静默不回落旧 bot。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.notifier import (  # noqa: E402
    TelegramNotifier,
    resolve_telegram_channel,
    timing_channel_kind,
)
from app.services.alert_engine import send_and_log  # noqa: E402


def _cfg(**telegram) -> Dict[str, Any]:
    return {"telegram": dict(telegram)}


def test_timing_channel_kind_put_call():
    assert timing_channel_kind("WHEEL_PUT") == "timing_put"
    assert timing_channel_kind("WHEEL_CALL") == "timing_call"
    assert timing_channel_kind("PRIMARY") is None
    assert timing_channel_kind(None) is None


def test_chan_uses_telegram_chan_credentials():
    cfg = _cfg(
        bot_token="LEGACY_TOKEN",
        chat_id="LEGACY_CHAT",
        chan={"bot_token": "CHAN_TOKEN", "chat_id": "CHAN_CHAT"},
    )
    r = resolve_telegram_channel("chan", cfg)
    assert r["enabled"] is True
    assert r["silent"] is False
    assert r["bot_token"] == "CHAN_TOKEN"
    assert r["chat_id"] == "CHAN_CHAT"
    assert r["source"] == "telegram.chan"
    n = TelegramNotifier.from_channel("chan", cfg)
    assert n._enabled
    assert n.bot_token == "CHAN_TOKEN"
    assert n.chat_id == "CHAN_CHAT"
    assert n.channel_kind == "chan"


def test_wheel_put_uses_timing_put():
    cfg = _cfg(
        bot_token="LEGACY_TOKEN",
        chat_id="LEGACY_CHAT",
        timing_put={"bot_token": "PUT_TOKEN", "chat_id": "PUT_CHAT"},
        timing_call={"bot_token": "CALL_TOKEN", "chat_id": "CALL_CHAT"},
    )
    assert timing_channel_kind("WHEEL_PUT") == "timing_put"
    r = resolve_telegram_channel("timing_put", cfg)
    assert r["bot_token"] == "PUT_TOKEN"
    assert r["chat_id"] == "PUT_CHAT"
    n = TelegramNotifier.from_channel(timing_channel_kind("WHEEL_PUT"), cfg)
    assert n.bot_token == "PUT_TOKEN"
    assert n.channel_kind == "timing_put"


def test_wheel_call_uses_timing_call():
    cfg = _cfg(
        bot_token="LEGACY_TOKEN",
        chat_id="LEGACY_CHAT",
        timing_put={"bot_token": "PUT_TOKEN", "chat_id": "PUT_CHAT"},
        timing_call={"bot_token": "CALL_TOKEN", "chat_id": "CALL_CHAT"},
    )
    assert timing_channel_kind("WHEEL_CALL") == "timing_call"
    n = TelegramNotifier.from_channel(timing_channel_kind("WHEEL_CALL"), cfg)
    assert n.bot_token == "CALL_TOKEN"
    assert n.chat_id == "CALL_CHAT"
    assert n.channel_kind == "timing_call"


def test_missing_channel_silent_no_legacy_fallback():
    cfg = _cfg(bot_token="LEGACY_TOKEN", chat_id="LEGACY_CHAT")
    for kind in ("chan", "timing_put", "timing_call"):
        r = resolve_telegram_channel(kind, cfg)
        assert r["enabled"] is False
        assert r["silent"] is True
        assert r["bot_token"] == ""
        assert r["chat_id"] == ""
        n = TelegramNotifier.from_channel(kind, cfg)
        assert not n._enabled
        assert n.channel_silent is True
        # 绝不能拿到旧全局 token
        assert n.bot_token != "LEGACY_TOKEN"


def test_partial_channel_missing_chat_is_silent():
    cfg = _cfg(chan={"bot_token": "CHAN_ONLY", "chat_id": ""})
    r = resolve_telegram_channel("chan", cfg)
    assert r["enabled"] is False
    assert r["silent"] is True


def test_legacy_still_uses_top_level():
    cfg = _cfg(bot_token="LEGACY_TOKEN", chat_id="LEGACY_CHAT")
    r = resolve_telegram_channel("legacy", cfg)
    assert r["enabled"] is True
    assert r["bot_token"] == "LEGACY_TOKEN"
    n = TelegramNotifier.from_config(cfg)
    assert n.bot_token == "LEGACY_TOKEN"
    assert n.channel_kind == "legacy"


def test_send_and_log_chan_kind_routes_and_skips_silent():
    cfg = _cfg(bot_token="LEGACY_TOKEN", chat_id="LEGACY_CHAT")
    with patch("app.services.alert_engine.log_push") as log_push:
        out = send_and_log(
            "hello chan",
            category="chan",
            channel_kind="chan",
            cfg=cfg,
            dry_run=False,
        )
    assert out["sent"] is False
    assert out["reason"] == "channel_silent"
    assert log_push.called
    kwargs = log_push.call_args.kwargs
    assert kwargs["reason"] == "channel_silent"
    assert kwargs["category"] == "chan"


def test_send_and_log_uses_chan_credentials_when_present():
    cfg = _cfg(
        bot_token="LEGACY_TOKEN",
        chat_id="LEGACY_CHAT",
        chan={"bot_token": "CHAN_TOKEN", "chat_id": "CHAN_CHAT"},
    )
    sent: List[str] = []

    class FakeNotifier:
        _enabled = True
        channel_silent = False

        def send_detailed(self, text: str):
            sent.append(text)
            return {"ok": True, "reason": "ok"}

    with patch(
        "app.services.notifier.TelegramNotifier.from_channel",
        return_value=FakeNotifier(),
    ) as from_ch, patch("app.services.alert_engine.log_push"):
        out = send_and_log(
            "chan body",
            category="chan",
            channel_kind="chan",
            cfg=cfg,
        )
    assert out["sent"] is True
    assert sent == ["chan body"]
    assert from_ch.call_args.args[0] == "chan"


def test_no_secrets_in_default_config():
    from app.core.config import DEFAULT_CONFIG

    tg = DEFAULT_CONFIG["telegram"]
    assert tg["bot_token"] == ""
    assert tg["chat_id"] == ""
    for k in ("chan", "timing_put", "timing_call"):
        assert k in tg
        assert tg[k]["bot_token"] == ""
        assert tg[k]["chat_id"] == ""
    # 扫描整个 DEFAULT_CONFIG 字符串不得出现假冒真实形态的长 token
    blob = str(DEFAULT_CONFIG)
    assert ":" not in tg["bot_token"]
    assert "123456:" not in blob


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
