"""缠论买卖点 Telegram:增量推送 / 指纹去重 / 开关 / 级别过滤。不连真实 Telegram。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.chan_alerts import (  # noqa: E402
    allowed_timeframes,
    chan_signal_fingerprint,
    due_timeframes,
    format_chan_alert,
    process_chan_signals,
    resolve_universe,
    run_chan_alert_cycle,
)

NOW = datetime(2026, 9, 2, 15, 0, 0)
TS = "2026-09-02T14:55:00"


def _cfg(**chan):
    base = {
        "enabled": True,
        "timeframes": ["5m", "30m"],
        "symbols": ["AAPL"],
        "session_only": False,
        "poll_minutes_5m": 5,
        "poll_minutes_30m": 30,
    }
    base.update(chan)
    return {
        "chan_alerts": base,
        "wheel_alerts": {"quiet_hours_start": 0, "quiet_hours_end": 0},
        "telegram": {},
    }


def _sig(**kw):
    row = {
        "symbol": "AAPL",
        "timeframe": "5m",
        "kind": "B1",
        "label": "一买",
        "ts": TS,
        "price": 182.35,
        "note": "下跌离开段力度弱于前一段(背驰)",
    }
    row.update(kw)
    return row


def test_new_signal_would_push_once():
    sent = []

    def capture(body, row):
        sent.append(body)
        return {"ok": True, "reason": "dry_run", "sent": False}

    out = process_chan_signals(
        [_sig()], cfg=_cfg(), dry_run=True, send_fn=capture, now=NOW, state={},
    )
    assert out["sent_count"] == 1
    assert len(out["would_send"]) == 1
    assert len(sent) == 1
    text = sent[0]
    assert "AAPL" in text
    assert "5分钟" in text
    assert "一买" in text
    assert "182.35" in text
    assert "背驰" in text
    assert "该买" not in text
    assert "该卖" not in text


def test_duplicate_fingerprint_skips():
    sig = _sig()
    fp = chan_signal_fingerprint(sig["symbol"], sig["timeframe"], sig["kind"], sig["ts"])
    out = process_chan_signals(
        [sig, sig], cfg=_cfg(), dry_run=True, now=NOW, state={fp: "2026-09-02T14:00:00"},
    )
    assert out["sent_count"] == 0
    assert out["skipped"]["dup"] == 2
    assert out["would_send"] == []


def test_same_tuple_second_pass_skips():
    """同一 (symbol, timeframe, kind, ts) 最多一次。"""
    state = {}
    first = process_chan_signals([_sig()], cfg=_cfg(), dry_run=True, now=NOW, state=state)
    assert first["sent_count"] == 1
    second = process_chan_signals([_sig()], cfg=_cfg(), dry_run=True, now=NOW, state=state)
    assert second["sent_count"] == 0
    assert second["skipped"]["dup"] == 1


def test_config_off_skips():
    out = process_chan_signals(
        [_sig()], cfg=_cfg(enabled=False), dry_run=True, now=NOW, state={},
    )
    assert out["sent_count"] == 0
    assert out["skipped"]["disabled"] == 1
    assert out["reason"] == "disabled"

    cycle = run_chan_alert_cycle(
        cfg=_cfg(enabled=False),
        now=NOW,
        dry_run=True,
        persist=False,
        last_runs={},
        session_open=True,
        prime_on_empty=False,
        analyze_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not analyze")),
    )
    assert cycle["reason"] == "disabled"
    assert cycle["sent_count"] == 0


def test_1m_1h_not_scanned():
    tfs = allowed_timeframes(_cfg(timeframes=["1m", "5m", "30m", "1h", "1d"]))
    assert tfs == ["5m", "30m"]
    assert "1m" not in tfs
    assert "1h" not in tfs

    due_none = due_timeframes(
        {}, NOW, _cfg(timeframes=["1m", "1h", "1d"]), session_open=True,
    )
    assert due_none == []

    scanned = []

    def fake_analyze(symbol, tf, cfg):
        scanned.append((symbol, tf))
        return {
            "signals": [{
                "kind": "B1", "label": "一买", "ts": TS, "price": 10, "note": "观察背驰",
            }],
        }

    out = run_chan_alert_cycle(
        cfg=_cfg(timeframes=["1m", "5m", "1h", "30m", "1d"]),
        now=NOW,
        analyze_fn=fake_analyze,
        dry_run=True,
        persist=False,
        last_runs={"5m": (NOW - timedelta(minutes=10)).isoformat(),
                   "30m": (NOW - timedelta(minutes=40)).isoformat()},
        session_open=True,
        prime_on_empty=False,
        state={},
    )
    got = {tf for _, tf in scanned}
    assert got == {"5m", "30m"}
    assert ("AAPL", "1m") not in scanned
    assert ("AAPL", "1h") not in scanned
    assert out["due"] == ["5m", "30m"]


def test_process_drops_disallowed_timeframe():
    out = process_chan_signals(
        [_sig(timeframe="1m"), _sig(timeframe="1h", kind="S1", label="一卖")],
        cfg=_cfg(), dry_run=True, now=NOW, state={},
    )
    assert out["sent_count"] == 0
    assert out["skipped"]["tf"] == 2


def test_universe_enabled_wheel_targets():
    targets = [
        {"symbol": "AAPL", "enabled": 1},
        {"symbol": "ARM", "enabled": True},
        {"symbol": "SPCX", "enabled": 1},
        {"symbol": "TSLA", "enabled": 1},
        {"symbol": "NVDA", "enabled": 0},
    ]
    assert resolve_universe({"chan_alerts": {}}, targets=targets) == [
        "AAPL", "ARM", "SPCX", "TSLA",
    ]
    assert resolve_universe(_cfg(symbols=["TSLA", "arm"]), targets=targets) == ["TSLA", "ARM"]


def test_session_closed_skips_scan():
    scanned = []
    out = run_chan_alert_cycle(
        cfg=_cfg(session_only=True),
        now=NOW,
        analyze_fn=lambda s, tf, c: scanned.append((s, tf)) or {"signals": []},
        dry_run=True,
        persist=False,
        last_runs={},
        session_open=False,
        prime_on_empty=False,
    )
    assert scanned == []
    assert out["reason"] == "not_due"


def test_format_has_no_trading_verdict():
    text = format_chan_alert("AAPL", "30m", {
        "kind": "S1", "label": "一卖", "price": 200.5, "note": "上涨离开段力度弱于前一段(背驰)",
    })
    assert "AAPL · 30分钟 · 一卖 · $200.50" in text
    assert "该买" not in text and "该卖" not in text


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
