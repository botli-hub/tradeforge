"""推送引擎单元测试:指纹/静默/去重/闸门/模板。"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.alert_engine import (  # noqa: E402
    dte_bucket,
    filter_scan_opportunities,
    format_position_alert,
    format_scan_alerts,
    in_quiet_hours,
    is_cooled,
    is_urgent_item,
    opportunity_fingerprint,
    position_fingerprint,
    process_position_alerts,
    select_position_items,
)


def test_quiet_hours_overnight():
    # 22–7 跨午夜
    assert in_quiet_hours(datetime(2026, 7, 18, 23, 0), 22, 7)
    assert in_quiet_hours(datetime(2026, 7, 18, 3, 0), 22, 7)
    assert not in_quiet_hours(datetime(2026, 7, 18, 10, 0), 22, 7)
    # start==end 关闭
    assert not in_quiet_hours(datetime(2026, 7, 18, 23, 0), 22, 22)


def test_quiet_hours_daytime():
    assert in_quiet_hours(datetime(2026, 7, 18, 13, 0), 12, 14)
    assert not in_quiet_hours(datetime(2026, 7, 18, 15, 0), 12, 14)


def test_dte_bucket():
    assert dte_bucket(2) == "dte0-3"
    assert dte_bucket(7) == "dte4-7"
    assert dte_bucket(10) == "dte8-14"
    assert dte_bucket(21) == "dte15-21"
    assert dte_bucket(30) == "dte22+"


def test_urgent_and_fingerprint():
    deep = {
        "contract_code": "ARM250718P95",
        "action_code": "PREPARE_ASSIGN",
        "action_priority": 1,
        "deep_itm": True,
        "itm": True,
        "dte": 5,
    }
    assert is_urgent_item(deep)
    fp1 = position_fingerprint(deep)
    # 同状态指纹稳定
    assert position_fingerprint(deep) == fp1
    # DTE 桶变化 → 新指纹
    deep2 = dict(deep, dte=15)
    assert position_fingerprint(deep2) != fp1
    # 动作变化 → 新指纹
    deep3 = dict(deep, action_code="CLOSE", action_priority=2, deep_itm=False, itm=False)
    assert position_fingerprint(deep3) != fp1


def test_format_position_csp_checklist():
    text = format_position_alert({
        "symbol": "ARM",
        "side": "PUT",
        "strike": 95,
        "dte": 5,
        "profit_pct": -12,
        "action_hint": "准备接货",
        "action_code": "PREPARE_ASSIGN",
        "action_priority": 1,
        "deep_itm": True,
        "itm": True,
        "expiring": True,
        "assign_checklist": {
            "assign_notional": 9500,
            "floor_ok": True,
            "next_step_hint": "接货后可卖CC",
        },
    })
    assert "管仓" in text
    assert "ARM" in text
    assert "准备接货" in text
    assert "清单" in text
    assert "Wheel" in text


def test_format_position_cc():
    text = format_position_alert({
        "symbol": "NVDA",
        "side": "CALL",
        "strike": 140,
        "dte": 12,
        "profit_pct": 55,
        "remaining_annualized": 18,
        "action_hint": "吃θ持有",
        "action_code": "HOLD_THETA",
        "action_priority": 5,
        "itm": False,
    })
    assert "CC" in text or "卖Call" in text
    assert "NVDA" in text


def test_select_and_process_dry_run(monkeypatch=None):
    items = [
        {
            "contract_code": "A1", "symbol": "AAA", "side": "PUT", "strike": 100,
            "action_code": "ROLL", "action_priority": 2, "dte": 18, "profit_pct": 10,
            "itm": False,
        },
        {
            "contract_code": "B1", "symbol": "BBB", "side": "CALL", "strike": 50,
            "action_code": "NONE", "action_priority": 9, "dte": 40, "profit_pct": 5,
            "itm": False,
        },
        {
            "contract_code": "C1", "symbol": "CCC", "side": "PUT", "strike": 80,
            "action_code": "PREPARE_ASSIGN", "action_priority": 1, "dte": 3,
            "profit_pct": -20, "itm": True, "deep_itm": True, "expiring": True,
        },
    ]
    selected = select_position_items(items, priority_max=3)
    codes = {i["contract_code"] for i in selected}
    assert "A1" in codes
    assert "C1" in codes
    assert "B1" not in codes

    # dry_run 不依赖 DB/TG
    out = process_position_alerts(
        items,
        cfg={
            "wheel_position": {"notify_mode": "realtime", "alert_push_minutes": 15},
            "wheel_alerts": {
                "position_cooldown_hours": 0,  # 不去重
                "quiet_hours_start": 0,
                "quiet_hours_end": 0,  # 关静默
            },
            "telegram": {},
        },
        force=True,
        dry_run=True,
    )
    assert out["candidates"] >= 2
    assert out["sent_count"] >= 1
    assert out["preview"]


def test_scan_filter_put_blocked():
    opps = [
        {"symbol": "A", "side": "PUT", "strike": 10, "expiry": "2026-08-01", "score": 9, "annualized": 30},
        {"symbol": "B", "side": "CALL", "strike": 20, "expiry": "2026-08-01", "score": 8, "annualized": 25},
        {"symbol": "C", "side": "PUT", "strike": 15, "expiry": "2026-08-08", "score": 7, "annualized": 20},
    ]
    selected, fps = filter_scan_opportunities(
        opps,
        top_n=5,
        put_blocked=True,
        skip_blocked_puts=True,
        only_new=False,
        dedupe_hours=0,
        state={},
    )
    assert all((o.get("side") or "").upper() != "PUT" for o in selected)
    assert len(selected) == 1
    assert selected[0]["symbol"] == "B"
    assert len(fps) == 1


def test_scan_min_score_and_dedupe():
    opps = [
        {"symbol": "A", "side": "PUT", "strike": 10, "expiry": "2026-08-01", "score": 9, "annualized": 30},
        {"symbol": "B", "side": "PUT", "strike": 11, "expiry": "2026-08-01", "score": 3, "annualized": 30},
    ]
    selected, _ = filter_scan_opportunities(
        opps, top_n=5, min_score=5, only_new=False, state={},
    )
    assert len(selected) == 1
    assert selected[0]["symbol"] == "A"

    fp = opportunity_fingerprint(opps[0])
    # 已冷却
    assert is_cooled(fp, 12.0, {fp: datetime.now().isoformat()}, datetime.now())
    selected2, _ = filter_scan_opportunities(
        opps, top_n=5, min_score=5, only_new=True, dedupe_hours=12,
        state={fp: datetime.now().isoformat()},
    )
    assert selected2 == []


def test_format_scan_with_gate():
    text = format_scan_alerts(
        [{"symbol": "AAPL", "side": "CALL", "expiry": "2026-08-15", "strike": 200,
          "delta": 0.25, "dte": 28, "bid": 2.0, "annualized": 18, "score": 7}],
        put_blocked=True,
    )
    assert "闸门" in text or "Put" in text
    assert "AAPL" in text
