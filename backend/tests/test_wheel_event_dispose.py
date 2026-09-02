"""持仓期事件日强制分叉:入窗 must_manage / 到期后不触发 / 缺数据不崩."""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_decision import decide_position  # noqa: E402
from app.core.wheel_dispose import item_must_manage  # noqa: E402
from app.core.wheel_event_dispose import (  # noqa: E402
    CALL_BAG,
    CALL_HOLD,
    PUT_BAG,
    PUT_HOLD,
    apply_event_dispose,
    build_event_dispose_fork,
    event_covers_expiry,
    event_window_fingerprint,
    format_event_alert,
    in_event_window,
    lookup_holding_events,
    pick_window_event,
    process_event_window_alerts,
)


TODAY = date(2026, 9, 2)


def _item(**kw):
    base = dict(
        symbol="AAPL",
        side="PUT",
        strike=180.0,
        spot=190.0,
        dte=16,
        expiry="2026-09-18",
        current_price=2.0,
        buyback_ask=2.0,
        open_price=3.0,
        profit_pct=33.0,
        itm=False,
        delta=0.2,
        expiring=False,
        qty=1,
        contract_size=100,
        contract_code="US.AAPL260918P00180000",
        cycle_id="cyc-event-1",
    )
    base.update(kw)
    return base


def _pos(**kw):
    cfg = {
        "profit_target_pct": 50,
        "event_dispose_enabled": True,
        "event_window_days": 7,
        "event_urgent_days": 2,
    }
    cfg.update(kw)
    return cfg


def test_covering_earnings_inside_window_must_manage():
    """覆盖存续且 T-5 入窗 → 必须处理,Put 文案分叉."""
    today = date.today()
    earn = (today + timedelta(days=5)).isoformat()
    expiry = (today + timedelta(days=16)).isoformat()
    item = _item(earnings_date=earn, expiry=expiry, dte=16)
    r = decide_position(item, 15, 50, _pos())
    assert r["must_manage"] is True
    assert item_must_manage(r)
    cover = r.get("event_cover") or {}
    assert cover.get("type") == "earnings"
    assert cover.get("date") == earn
    assert cover.get("in_window") is True
    fork = r["dispose_fork"]
    assert fork["kind"] == "event_dispose"
    blob = (fork["bag"]["copy"] or "") + (fork["hold"]["copy"] or "")
    assert PUT_BAG in blob
    assert PUT_HOLD in blob
    assert earn in blob
    reasons = " ".join(r.get("reasons") or [])
    assert "财报" in reasons
    assert earn in reasons
    assert "DTE" in reasons
    q = r.get("quant_thresholds") or {}
    assert q.get("profit_target_pct") == 50
    assert r.get("action_code") != "AUTO"


def test_event_after_expiry_does_not_trigger():
    """事件在到期日之后:不算覆盖,不进必须处理."""
    today = date.today()
    expiry = (today + timedelta(days=16)).isoformat()
    earn = (today + timedelta(days=25)).isoformat()
    item = _item(earnings_date=earn, dte=16, expiry=expiry, profit_pct=33.0)
    r = decide_position(item, 15, 50, _pos())
    assert not r.get("event_cover")
    assert (r.get("dispose_fork") or {}).get("kind") != "event_dispose"
    # 未过止盈线,也不该被事件拽进必须处理
    assert not r.get("must_manage")
    assert not item_must_manage(r)


def test_missing_data_does_not_crash():
    """无财报 / 查找抛错 / 港股:安静降级,不误报."""
    item = _item(symbol="SPCX", earnings_date=None)
    r = apply_event_dispose(
        {"action_code": "HOLD_THETA", "reasons": [], "quant_thresholds": {"profit_target_pct": 50}},
        item,
        _pos(),
        today=TODAY,
        earnings_fn=lambda s: None,
        dividend_fn=lambda s: None,
    )
    assert r.get("must_manage") is not True
    assert not r.get("event_cover")

    boom = apply_event_dispose(
        {"action_code": "NONE", "reasons": []},
        _item(symbol="00700.HK", expiry="2026-09-18"),
        _pos(),
        today=TODAY,
        earnings_fn=lambda s: (_ for _ in ()).throw(RuntimeError("finnhub down")),
        dividend_fn=lambda s: (_ for _ in ()).throw(RuntimeError("div down")),
    )
    assert boom.get("must_manage") is not True
    assert not boom.get("event_cover")

    empty = lookup_holding_events(
        {"symbol": "SPCX", "side": "PUT", "expiry": "2026-09-18"},
        today=TODAY,
        earnings_fn=lambda s: None,
        dividend_fn=lambda s: None,
    )
    assert empty == []

    # 过期字段/坏日期也不崩
    messy = lookup_holding_events(
        {"symbol": "AAPL", "side": "PUT", "expiry": "not-a-date", "dte": "x", "earnings_date": "??"},
        today=TODAY,
        earnings_fn=lambda s: "bogus",
    )
    assert messy == []


def test_event_day_counts_as_in_window():
    assert in_event_window(0, 7) is True
    assert in_event_window(7, 7) is True
    assert in_event_window(8, 7) is False
    assert in_event_window(-1, 7) is False
    evs = lookup_holding_events(
        _item(earnings_date="2026-09-02", expiry="2026-09-18"),
        today=TODAY,
        earnings_fn=lambda s: "2026-09-02",
    )
    chosen = pick_window_event(evs, window_days=7, urgent_days=2)
    assert chosen is not None
    assert chosen["days_to_event"] == 0
    assert chosen["urgent"] is True


def test_t2_urgent_and_t7_not_urgent():
    t2 = pick_window_event(
        [{"type": "earnings", "date": "2026-09-04", "days_to_event": 2, "covering": True}],
        window_days=7, urgent_days=2,
    )
    assert t2 and t2["urgent"] is True
    t7 = pick_window_event(
        [{"type": "earnings", "date": "2026-09-09", "days_to_event": 7, "covering": True}],
        window_days=7, urgent_days=2,
    )
    assert t7 and t7["in_window"] is True and t7["urgent"] is False


def test_covers_strictly_before_expiry():
    assert event_covers_expiry("2026-09-17", "2026-09-18") is True
    assert event_covers_expiry("2026-09-18", "2026-09-18") is False
    assert event_covers_expiry("2026-09-19", "2026-09-18") is False


def test_call_copy_differs_from_put():
    put = build_event_dispose_fork(
        _item(side="PUT"),
        {"type": "earnings", "type_label": "财报", "date": "2026-09-07",
         "days_to_event": 5, "days_to_expiry": 16, "in_window": True},
        {"quant_thresholds": {"profit_target_pct": 50}},
    )
    call = build_event_dispose_fork(
        _item(side="CALL", symbol="TSLA"),
        {"type": "dividend", "type_label": "除息", "date": "2026-09-05",
         "days_to_event": 3, "days_to_expiry": 16, "in_window": True},
        {"quant_thresholds": {"profit_target_pct": 50}},
    )
    assert PUT_BAG in put["bag"]["copy"]
    assert PUT_HOLD in put["hold"]["copy"]
    assert CALL_BAG in call["bag"]["copy"]
    assert CALL_HOLD in call["hold"]["copy"]
    assert "不自动下单" in put["bag"]["copy"]
    assert put["hold"]["directional_bet"] is True


def test_call_exdiv_covering_triggers():
    item = _item(
        side="CALL", symbol="AAPL", earnings_date=None,
        dividend={"date": "2026-09-06", "amount": 0.25},
        expiry="2026-09-18",
    )
    r = apply_event_dispose(
        {"action_code": "HOLD_THETA", "reasons": [], "side": "CALL",
         "quant_thresholds": {"profit_target_pct": 50}},
        item, _pos(), today=TODAY,
        earnings_fn=lambda s: None,
        dividend_fn=lambda s: {"date": "2026-09-06"},
    )
    assert r["must_manage"] is True
    assert r["event_cover"]["type"] == "dividend"
    assert CALL_HOLD in (r["dispose_fork"]["hold"]["copy"] or "")


def test_disabled_config_noop():
    r = apply_event_dispose(
        {"action_code": "HOLD_THETA", "reasons": []},
        _item(earnings_date="2026-09-07"),
        _pos(event_dispose_enabled=False),
        today=TODAY,
        earnings_fn=lambda s: "2026-09-07",
    )
    assert not r.get("must_manage")
    assert not r.get("dispose_fork")


def test_does_not_change_profit_target_number():
    today = date.today()
    r = decide_position(
        _item(
            earnings_date=(today + timedelta(days=5)).isoformat(),
            expiry=(today + timedelta(days=16)).isoformat(),
            dte=16,
            profit_pct=33.0,
        ),
        15, 50, _pos(),
    )
    assert (r.get("quant_thresholds") or {}).get("profit_target_pct") == 50
    fork = r.get("dispose_fork") or {}
    assert fork.get("profit_target_pct") in (50, 50.0, None) or fork.get("profit_target_pct") == 50


def test_telegram_first_window_entry_once():
    item = apply_event_dispose(
        {"action_code": "HOLD_THETA", "reasons": [], "quant_thresholds": {"profit_target_pct": 50}},
        _item(earnings_date="2026-09-07"),
        _pos(),
        today=TODAY,
        earnings_fn=lambda s: "2026-09-07",
    )
    item.update(_item(earnings_date="2026-09-07"))
    sent = []

    def capture(body, row):
        sent.append(body)
        return {"ok": True, "reason": "dry_run", "sent": False}

    cfg = {
        "wheel_position": {**_pos(), "alert_push_minutes": 15},
        "wheel_alerts": {"quiet_hours_start": 0, "quiet_hours_end": 0},
        "telegram": {},
    }
    state = {}
    first = process_event_window_alerts(
        [item], cfg=cfg, dry_run=True, send_fn=capture,
        now=datetime(2026, 9, 2, 15, 0, 0), state=state,
    )
    assert first["sent_count"] == 1
    assert PUT_BAG in sent[0] and PUT_HOLD in sent[0]
    assert "不自动下单" in sent[0]
    fp = event_window_fingerprint(item, item["event_cover"])
    assert fp in state
    second = process_event_window_alerts(
        [item], cfg=cfg, dry_run=True, send_fn=capture,
        now=datetime(2026, 9, 2, 16, 0, 0), state=state,
    )
    assert second["sent_count"] == 0
    assert second["skipped"]["dup"] == 1
    assert len(sent) == 1


def test_telegram_skipped_when_alerts_off():
    item = apply_event_dispose(
        {"action_code": "HOLD_THETA", "reasons": []},
        _item(earnings_date="2026-09-07"),
        _pos(),
        today=TODAY,
        earnings_fn=lambda s: "2026-09-07",
    )
    item.update(_item(earnings_date="2026-09-07"))
    out = process_event_window_alerts(
        [item],
        cfg={"wheel_position": {**_pos(), "alert_push_minutes": 0}},
        dry_run=True,
        now=datetime(2026, 9, 2, 15, 0, 0),
        state={},
    )
    assert out["sent_count"] == 0
    assert out["reason"] == "alerts_off"


def test_format_event_alert_has_no_order():
    text = format_event_alert(_item(), {
        "type": "earnings", "type_label": "财报", "date": "2026-09-07",
        "days_to_event": 5, "days_to_expiry": 16, "in_window": True,
    })
    assert "AAPL" in text and "财报" in text and "2026-09-07" in text
    assert "下单" in text  # 声明「不自动下单」
    assert "市价" not in text


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
