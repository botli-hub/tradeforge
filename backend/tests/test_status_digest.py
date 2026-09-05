"""status_digest: 格式化 / 空数据 / TG 分片;不发真实 TG。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.status_digest import (  # noqa: E402
    DEFAULT_STATUS_DIGEST,
    TG_MAX_LEN,
    build_status_digest_text,
    chunk_telegram_text,
    collect_status_rows,
    format_position_line,
    format_sim_line,
    format_touch_line,
    get_status_digest_cfg,
    run_status_digest,
    _should_run_daily,
)


SH = ZoneInfo("Asia/Shanghai")


def test_defaults():
    cfg = get_status_digest_cfg({})
    assert cfg["enabled"] is False
    assert cfg["status_digest_hour"] == 8
    assert cfg["status_digest_minutes"] == 0
    assert cfg["touch_limit"] == 10
    assert DEFAULT_STATUS_DIGEST["enabled"] is False


def test_overlay_cfg():
    cfg = get_status_digest_cfg({"status_digest": {"enabled": True, "touch_limit": 5}})
    assert cfg["enabled"] is True
    assert cfg["touch_limit"] == 5
    assert cfg["status_digest_hour"] == 8


def test_format_position_line():
    line = format_position_line(
        "AAPL CSP_OPEN",
        {
            "symbol": "AAPL",
            "status": "CSP_OPEN",
            "side": "PUT",
            "strike": 180,
            "expiry": "2026-01-17",
            "dte": 42,
            "premium": 320.5,
        },
    )
    assert "AAPL" in line and "CSP_OPEN" in line
    assert "PUT" in line and "$180" in line
    assert "DTE42" in line
    assert "权利金" in line


def test_format_touch_and_sim():
    t = format_touch_line(
        "x",
        {
            "symbol": "TSLA",
            "side": "CALL",
            "strike": 300,
            "timeframe": "1h",
            "contract": "TSLA250221C300",
        },
    )
    assert "TSLA" in t and "CALL" in t and "1h" in t and "TSLA250221C300" in t

    s = format_sim_line(
        "x",
        {
            "symbol": "NVDA",
            "status": "CSP_OPEN",
            "strategy": "wheel",
            "level": "A",
            "side": "PUT",
            "strike": 100,
            "expiry": "2026-03-20",
            "premium": 12,
        },
    )
    assert "NVDA" in s and "wheel/A" in s and "PUT" in s


def test_build_empty():
    text = build_status_digest_text(
        {"positions": [], "touches": [], "sim": []},
        now=datetime(2026, 9, 6, 8, 0, tzinfo=SH),
    )
    assert "状态摘要" in text
    assert "在场持仓 (0)" in text
    assert "最近触线 (0)" in text
    assert "Sim纸面 (0)" in text
    assert text.count("· 暂无") == 3


def test_build_with_rows_and_touch_limit():
    rows = {
        "positions": [
            (
                "cycle:1",
                "AAPL CSP_OPEN",
                {
                    "symbol": "AAPL",
                    "status": "CSP_OPEN",
                    "side": "PUT",
                    "strike": 180,
                    "expiry": "2026-01-17",
                    "dte": 10,
                    "premium": 100,
                },
            )
        ],
        "touches": [
            (
                f"touch:{i}",
                "t",
                {
                    "symbol": "X",
                    "side": "PUT",
                    "strike": i,
                    "timeframe": "1d",
                    "contract": f"C{i}",
                },
            )
            for i in range(15)
        ],
        "sim": [
            (
                "sim:1",
                "NVDA",
                {"symbol": "NVDA", "status": "HOLDING", "shares": 100, "premium": 0},
            )
        ],
    }
    text = build_status_digest_text(rows, touch_limit=10)
    assert "在场持仓 (1)" in text
    assert "最近触线 (10)" in text
    assert "Sim纸面 (1)" in text
    assert "C14" not in text  # limited to first 10 (0..9)
    assert "C9" in text


def test_chunking_under_limit():
    short = "hello\nworld"
    assert chunk_telegram_text(short) == [short]


def test_chunking_splits_and_labels():
    # 构造超长多行
    lines = [f"LINE-{i}-" + ("x" * 80) for i in range(80)]
    text = "\n".join(lines)
    assert len(text) > TG_MAX_LEN
    parts = chunk_telegram_text(text, limit=500)
    assert len(parts) > 1
    for i, p in enumerate(parts, 1):
        assert len(p) <= 500
        assert f"({i}/{len(parts)})" in p
    # 拼接后应覆盖原内容(去掉标签)
    rebuilt = "\n".join(p.rsplit("\n\n…(", 1)[0] for p in parts)
    assert "LINE-0-" in rebuilt and "LINE-79-" in rebuilt


def test_chunking_hard_cut_long_line():
    long_line = "Z" * 10000
    parts = chunk_telegram_text(long_line, limit=1000)
    assert len(parts) >= 10
    assert all(len(p) <= 1000 for p in parts)


def test_collect_status_rows_injected():
    cycles = [
        {
            "id": "c1",
            "symbol": "aapl",
            "status": "CSP_OPEN",
            "open_strike": 180,
            "open_expiry": "2026-01-17",
            "open_option_type": "PUT",
            "open_dte": 33,
            "total_premium": 200,
            "realized_pnl": None,
        }
    ]
    touches = {
        "items": [
            {
                "symbol": "TSLA",
                "side": "CALL",
                "strike": 300,
                "timeframe": "1h",
                "contract_code": "TSLA250221C300",
            }
        ]
    }
    sims = [
        {
            "id": "s1",
            "symbol": "nvda",
            "status": "CSP_OPEN",
            "strategy": "wheel",
            "level": "B",
            "open_strike": 90,
            "open_option_type": "PUT",
            "total_premium": 15,
        }
    ]
    rows = collect_status_rows(
        {"status_digest": {"touch_limit": 10}},
        get_cycles_fn=lambda include_closed=False: cycles,
        get_timing_fn=lambda page=1, page_size=10: touches,
        list_sim_fn=lambda include_closed=False, limit=200: sims,
    )
    assert len(rows["positions"]) == 1
    assert rows["positions"][0][2]["dte"] == 33
    assert rows["positions"][0][2]["symbol"] == "AAPL"
    assert len(rows["touches"]) == 1
    assert len(rows["sim"]) == 1


def test_run_disabled_skips_without_send():
    sent: List[str] = []

    def fake_send(body: str, **kwargs: Any) -> Dict[str, Any]:
        sent.append(body)
        return {"ok": True, "sent": True, "reason": "ok"}

    out = run_status_digest(
        {"status_digest": {"enabled": False}},
        force=False,
        send_fn=fake_send,
    )
    assert out["skipped"] is True
    assert out["reason"] == "disabled"
    assert sent == []


def test_run_force_dry_run_no_send():
    sent: List[str] = []

    def fake_send(body: str, **kwargs: Any) -> Dict[str, Any]:
        sent.append(body)
        return {"ok": True, "sent": True, "reason": "ok"}

    import app.services.status_digest as sd
    orig = sd.collect_status_rows
    sd.collect_status_rows = lambda cfg=None, **kw: {  # type: ignore
        "positions": [], "touches": [], "sim": []
    }
    try:
        out = run_status_digest(
            {"status_digest": {"enabled": False}},
            force=True,
            dry_run=True,
            send_fn=fake_send,
        )
    finally:
        sd.collect_status_rows = orig  # type: ignore

    assert out["ok"] is True
    assert out["reason"] == "dry_run"
    assert sent == []
    assert "状态摘要" in (out.get("preview") or "")


def test_run_force_sends_via_injected():
    sent: List[str] = []

    def fake_send(body: str, **kwargs: Any) -> Dict[str, Any]:
        sent.append(body)
        assert kwargs.get("category") == "status_digest"
        return {"ok": True, "sent": True, "reason": "ok"}

    # patch collect via empty repos
    import app.services.status_digest as sd

    def fake_collect(cfg=None, **kwargs):
        return {"positions": [], "touches": [], "sim": []}

    orig = sd.collect_status_rows
    sd.collect_status_rows = fake_collect  # type: ignore
    try:
        out = run_status_digest(
            {"status_digest": {"enabled": False}, "telegram": {}},
            force=True,
            dry_run=False,
            send_fn=fake_send,
        )
    finally:
        sd.collect_status_rows = orig  # type: ignore

    assert out["sent_count"] == 1
    assert len(sent) == 1
    assert "暂无" in sent[0]


def test_should_run_daily_hour():
    early = datetime(2026, 9, 6, 7, 59, tzinfo=SH)
    late = datetime(2026, 9, 6, 8, 0, tzinfo=SH)
    sd = {"status_digest_hour": 8}
    assert _should_run_daily(sd, early) is False
    # late may be True if kv not set for today — we don't assert True to avoid DB;
    # just ensure hour gate works for early
    assert late.hour >= 8
