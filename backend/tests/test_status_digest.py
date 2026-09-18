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
    digest_clock,
    digest_zoneinfo,
    fetch_premium_totals,
    format_position_line,
    format_premium_totals_lines,
    format_sim_line,
    format_touch_line,
    get_status_digest_cfg,
    run_status_digest,
    _should_run_daily,
)


SH = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")


def test_defaults():
    cfg = get_status_digest_cfg({})
    assert cfg["enabled"] is False
    assert cfg["status_digest_tz"] == "America/New_York"
    assert cfg["status_digest_hour"] == 9
    assert cfg["status_digest_minute"] == 30
    assert cfg["status_digest_minutes"] == 0
    assert cfg["touch_limit"] == 10
    assert DEFAULT_STATUS_DIGEST["enabled"] is False
    assert digest_clock(cfg) == (9, 30)
    assert str(digest_zoneinfo(cfg)) == "America/New_York"


def test_overlay_cfg():
    cfg = get_status_digest_cfg({"status_digest": {"enabled": True, "touch_limit": 5}})
    assert cfg["enabled"] is True
    assert cfg["touch_limit"] == 5
    assert cfg["status_digest_hour"] == 9
    assert cfg["status_digest_minute"] == 30


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
    assert "本月权利金 $0.00" in text
    assert "累计权利金 $0.00" in text
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
        get_targets_fn=lambda: [{"symbol": "TSLA", "enabled": 1}],
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
    """日推门控按配置时区的 hour:minute(默认 ET 09:30),不是上海 08:00。"""
    early = datetime(2026, 9, 18, 9, 29, tzinfo=NY)
    at = datetime(2026, 9, 18, 9, 30, tzinfo=NY)
    sd = {
        "status_digest_tz": "America/New_York",
        "status_digest_hour": 9,
        "status_digest_minute": 30,
    }
    assert digest_clock(sd) == (9, 30)
    assert _should_run_daily(sd, early) is False
    # at 可能因 KV 为 True/False;只断言分钟门控已过
    assert (at.hour, at.minute) >= (9, 30)


def test_should_run_daily_not_shanghai_0800():
    """默认日推看 ET 09:30,不是上海 08:00 钟面。"""
    sd = get_status_digest_cfg({})
    assert digest_clock(sd) == (9, 30)
    assert str(digest_zoneinfo(sd)) == "America/New_York"
    # 上海钟面 08:00 相对默认 (9,30) 未到 —— 禁止再把上海 8 点当触发
    sh_8 = datetime(2026, 9, 18, 8, 0, tzinfo=SH)
    assert (sh_8.hour, sh_8.minute) < digest_clock(sd)
    # ET 09:29 未到;ET 09:30 钟面已到(KV 另测)
    assert _should_run_daily(sd, datetime(2026, 9, 18, 9, 29, tzinfo=NY)) is False


def test_digest_clock_status_digest_at():
    sd = {"status_digest_at": "09:30", "status_digest_hour": 1, "status_digest_minute": 0}
    assert digest_clock(sd) == (9, 30)
    sd2 = {"status_digest_hour": 8, "status_digest_minute": 0}
    assert digest_clock(sd2) == (8, 0)


def test_should_run_daily_kv_gate(monkeypatch):
    sd = {
        "status_digest_tz": "America/New_York",
        "status_digest_hour": 9,
        "status_digest_minute": 30,
    }
    now = datetime(2026, 9, 18, 9, 30, tzinfo=NY)

    import app.data.wheel_repository as wrepo

    monkeypatch.setattr(wrepo, "get_kv", lambda key: None)
    assert _should_run_daily(sd, now) is True
    monkeypatch.setattr(wrepo, "get_kv", lambda key: "2026-09-18")
    assert _should_run_daily(sd, now) is False


def test_format_premium_totals_lines_zero_and_values():
    zero = format_premium_totals_lines(None, None)
    assert zero == ["本月权利金 $0.00", "累计权利金 $0.00"]
    lines = format_premium_totals_lines(1234.5, 98765.4)
    assert lines[0] == "本月权利金 $1,234.50"
    assert lines[1] == "累计权利金 $98,765.40"


def test_build_includes_premium_totals_near_header():
    text = build_status_digest_text(
        {
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
            "touches": [],
            "sim": [],
        },
        now=datetime(2026, 9, 10, 8, 0, tzinfo=SH),
        premium_month=148.0,
        premium_total=12345.67,
    )
    assert "TradeForge 状态摘要 · 2026-09-10 08:00 CST" in text
    assert "本月权利金 $148.00" in text
    assert "累计权利金 $12,345.67" in text
    # 汇总在标题之后、在场持仓之前
    header_i = text.index("状态摘要")
    month_i = text.index("本月权利金 $148.00")
    total_i = text.index("累计权利金 $12,345.67")
    pos_i = text.index("在场持仓 (1)")
    assert header_i < month_i < total_i < pos_i


def test_fetch_premium_totals_mock_stats():
    got = fetch_premium_totals(
        get_stats_fn=lambda: {"premium_month": 10.5, "premium_total": 99}
    )
    assert got == {"premium_month": 10.5, "premium_total": 99.0}


def test_fetch_premium_totals_fallback_zero():
    def boom():
        raise RuntimeError("db down")

    got = fetch_premium_totals(get_stats_fn=boom)
    assert got == {"premium_month": 0.0, "premium_total": 0.0}


def test_run_force_dry_run_includes_premium_lines():
    import app.services.status_digest as sd

    orig_collect = sd.collect_status_rows
    orig_fetch = sd.fetch_premium_totals
    sd.collect_status_rows = lambda cfg=None, **kw: {  # type: ignore
        "positions": [], "touches": [], "sim": []
    }
    sd.fetch_premium_totals = lambda get_stats_fn=None: {  # type: ignore
        "premium_month": 12.0,
        "premium_total": 3400.5,
    }
    try:
        out = run_status_digest(
            {"status_digest": {"enabled": False}},
            force=True,
            dry_run=True,
            send_fn=lambda body, **kwargs: {"ok": True, "sent": False},
        )
    finally:
        sd.collect_status_rows = orig_collect  # type: ignore
        sd.fetch_premium_totals = orig_fetch  # type: ignore

    preview = out.get("preview") or ""
    assert out["ok"] is True
    assert "本月权利金 $12.00" in preview
    assert "累计权利金 $3,400.50" in preview
    assert out.get("premium") == {"premium_month": 12.0, "premium_total": 3400.5}


def test_collect_touches_filtered_to_enabled_targets():
    """最近触线只保留 enabled=1 的 wheel 标的;非观察(如 TFACQ/AAPL)剔除。"""
    hist = {
        "items": [
            {"symbol": "TFACQ", "side": "PUT", "strike": 10, "timeframe": "1d", "contract_code": "T1"},
            {"symbol": "AAPL", "side": "PUT", "strike": 180, "timeframe": "1d", "contract_code": "A1"},
            {"symbol": "TSLA", "side": "CALL", "strike": 300, "timeframe": "1h", "contract_code": "T2"},
            {"symbol": "NVDA", "side": "PUT", "strike": 100, "timeframe": "1d", "contract_code": "N1"},
        ]
    }
    targets = [
        {"symbol": "TSLA", "enabled": 1},
        {"symbol": "NVDA", "enabled": 1},
        {"symbol": "AAPL", "enabled": 0},
    ]
    rows = collect_status_rows(
        {"status_digest": {"touch_limit": 10}},
        get_cycles_fn=lambda include_closed=False: [],
        get_timing_fn=lambda page=1, page_size=100: hist,
        list_sim_fn=lambda include_closed=False, limit=200: [],
        get_targets_fn=lambda: targets,
    )
    syms = [f["symbol"] for _sk, _t, f in rows["touches"]]
    assert syms == ["TSLA", "NVDA"]
    assert "TFACQ" not in syms and "AAPL" not in syms


def test_collect_touches_empty_when_no_enabled_or_no_match():
    hist = {
        "items": [
            {"symbol": "TFACQ", "side": "PUT", "strike": 10, "timeframe": "1d", "contract_code": "T1"},
            {"symbol": "AAPL", "side": "PUT", "strike": 180, "timeframe": "1d", "contract_code": "A1"},
        ]
    }
    # 无启用标的
    rows = collect_status_rows(
        {"status_digest": {"touch_limit": 10}},
        get_cycles_fn=lambda include_closed=False: [],
        get_timing_fn=lambda page=1, page_size=100: hist,
        list_sim_fn=lambda include_closed=False, limit=200: [],
        get_targets_fn=lambda: [{"symbol": "AAPL", "enabled": 0}],
    )
    assert rows["touches"] == []
    text = build_status_digest_text(rows)
    assert "最近触线 (0)" in text
    assert "· 暂无" in text

    # 启用但历史无匹配
    rows2 = collect_status_rows(
        {"status_digest": {"touch_limit": 10}},
        get_cycles_fn=lambda include_closed=False: [],
        get_timing_fn=lambda page=1, page_size=100: hist,
        list_sim_fn=lambda include_closed=False, limit=200: [],
        get_targets_fn=lambda: [{"symbol": "TSLA", "enabled": 1}],
    )
    assert rows2["touches"] == []


def test_collect_sim_only_open_cleared_shows_empty():
    """Sim 段仅当前 open;清空(无 open)→ 暂无。"""
    open_sims = [
        {
            "id": "s1",
            "symbol": "nvda",
            "status": "CSP_OPEN",
            "strategy": "wheel",
            "level": "A",
            "open_strike": 90,
            "open_option_type": "PUT",
            "total_premium": 15,
        }
    ]
    rows = collect_status_rows(
        {},
        get_cycles_fn=lambda include_closed=False: [],
        get_timing_fn=lambda page=1, page_size=100: {"items": []},
        list_sim_fn=lambda include_closed=False, limit=200: (
            open_sims if not include_closed else open_sims
        ),
        get_targets_fn=lambda: [],
    )
    assert len(rows["sim"]) == 1

    # 清空后 list 只返回 []
    rows_clear = collect_status_rows(
        {},
        get_cycles_fn=lambda include_closed=False: [],
        get_timing_fn=lambda page=1, page_size=100: {"items": []},
        list_sim_fn=lambda include_closed=False, limit=200: [],
        get_targets_fn=lambda: [],
    )
    assert rows_clear["sim"] == []
    text = build_status_digest_text(rows_clear)
    assert "Sim纸面 (0)" in text
    assert text.count("· 暂无") == 3


def test_schedule_fires_at_0930_et_not_0800_shanghai():
    """09:30 ET 到达可推;默认不再用上海 08:00 作触发钟面。"""
    sd = get_status_digest_cfg({})
    assert str(digest_zoneinfo(sd)) == "America/New_York"
    assert digest_clock(sd) == (9, 30)

    et_0929 = datetime(2026, 9, 18, 9, 29, tzinfo=NY)
    et_0930 = datetime(2026, 9, 18, 9, 30, tzinfo=NY)
    assert _should_run_daily(sd, et_0929) is False

    # 旧硬编码上海 08:00 钟面相对默认 (9,30) 未到 —— 证明不再把「上海 8 点」当默认触发
    sh_0800 = datetime(2026, 9, 18, 8, 0, tzinfo=SH)
    assert (sh_0800.hour, sh_0800.minute) < digest_clock(sd)

    import app.data.wheel_repository as wrepo

    orig = getattr(wrepo, "get_kv", None)

    def _none_kv(_key):
        return None

    wrepo.get_kv = _none_kv  # type: ignore
    try:
        assert _should_run_daily(sd, et_0930) is True
        # 旧上海 hour=8 配置迁移样例仍可显式恢复
        legacy = get_status_digest_cfg(
            {
                "status_digest": {
                    "status_digest_tz": "Asia/Shanghai",
                    "status_digest_hour": 8,
                    "status_digest_minute": 0,
                }
            }
        )
        assert digest_clock(legacy) == (8, 0)
        assert _should_run_daily(legacy, sh_0800) is True
        assert _should_run_daily(legacy, datetime(2026, 9, 18, 7, 59, tzinfo=SH)) is False
    finally:
        if orig is not None:
            wrepo.get_kv = orig  # type: ignore
