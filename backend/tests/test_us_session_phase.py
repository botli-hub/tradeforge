"""us_session_phase: America/New_York RTH, not fixed UTC 14:30–21:00."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_today import us_session_phase  # noqa: E402
from app.services.alert_engine import scan_session_allows  # noqa: E402
from app.services.chan_alerts import is_us_rth  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
SH = ZoneInfo("Asia/Shanghai")

# 2026-07-15 Wed = EDT (UTC-4). 2026-01-14 Wed = EST (UTC-5).
# 2026-10-15 Thu 仍是 EDT(冬令 11-01 才切);这是 session_only 扫到 22:30 CST 的 bug 日。
EDT_WED = (2026, 7, 15)
EST_WED = (2026, 1, 14)
EDT_BUG = (2026, 10, 15)


def _utc(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def _ny(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=NY)


def _sh(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=SH)


def test_edt_open_is_1330_utc_not_1430():
    """夏令真实开盘 13:30 UTC(09:30 EDT / 21:30 上海);14:30 UTC 已是 10:30 EDT。"""
    y, m, d = EDT_WED
    assert us_session_phase(_utc(y, m, d, 13, 29)) == "pre"
    assert us_session_phase(_utc(y, m, d, 13, 30)) == "open"
    # 旧实现把 14:30 UTC 当开盘,夏令会把 13:30–14:30 错判成 pre
    assert us_session_phase(_utc(y, m, d, 14, 29)) == "open"
    assert us_session_phase(_utc(y, m, d, 14, 30)) == "open"


def test_edt_close_is_2000_utc():
    y, m, d = EDT_WED
    assert us_session_phase(_utc(y, m, d, 19, 59)) == "open"
    assert us_session_phase(_utc(y, m, d, 20, 0)) == "after"
    assert us_session_phase(_utc(y, m, d, 23, 59)) == "after"
    assert us_session_phase(_utc(y, m, d + 1, 0, 0)) == "closed"  # 20:00 EDT 翌日 00:00 UTC


def test_est_open_is_1430_utc():
    """冬令开盘仍是 14:30 UTC(09:30 EST / 22:30 上海)。"""
    y, m, d = EST_WED
    assert us_session_phase(_utc(y, m, d, 13, 30)) == "pre"  # 08:30 EST
    assert us_session_phase(_utc(y, m, d, 14, 29)) == "pre"
    assert us_session_phase(_utc(y, m, d, 14, 30)) == "open"
    assert us_session_phase(_utc(y, m, d, 21, 0)) == "after"


def test_est_close_is_2100_utc():
    y, m, d = EST_WED
    assert us_session_phase(_utc(y, m, d, 20, 59)) == "open"
    assert us_session_phase(_utc(y, m, d, 21, 0)) == "after"
    assert us_session_phase(_utc(y, m, d + 1, 0, 59)) == "after"  # 19:59 EST
    assert us_session_phase(_utc(y, m, d + 1, 1, 0)) == "closed"  # 20:00 EST


def test_ny_wall_clock_same_in_summer_and_winter():
    """墙上 09:30–16:00 ET 两边都是 open,不随 UTC 偏移改写。"""
    for ymd in (EDT_WED, EST_WED):
        y, m, d = ymd
        assert us_session_phase(_ny(y, m, d, 3, 59)) == "closed"
        assert us_session_phase(_ny(y, m, d, 4, 0)) == "pre"
        assert us_session_phase(_ny(y, m, d, 9, 29)) == "pre"
        assert us_session_phase(_ny(y, m, d, 9, 30)) == "open"
        assert us_session_phase(_ny(y, m, d, 15, 59)) == "open"
        assert us_session_phase(_ny(y, m, d, 16, 0)) == "after"
        assert us_session_phase(_ny(y, m, d, 19, 59)) == "after"
        assert us_session_phase(_ny(y, m, d, 20, 0)) == "closed"


def test_shanghai_clock_matches_real_us_open():
    """CST 21:30 = EDT 开盘;CST 22:30 = EST 开盘。"""
    assert us_session_phase(_sh(2026, 7, 15, 21, 29)) == "pre"
    assert us_session_phase(_sh(2026, 7, 15, 21, 30)) == "open"
    assert us_session_phase(_sh(2026, 1, 14, 22, 29)) == "pre"
    assert us_session_phase(_sh(2026, 1, 14, 22, 30)) == "open"


def test_bug_date_2026_10_15_edt_open_at_2130_shanghai():
    """复现:2026-10-15 EDT,SPCX 约 22:00 CST 已可触,旧门禁拖到 22:30+。"""
    y, m, d = EDT_BUG
    assert us_session_phase(_sh(y, m, d, 21, 30)) == "open"
    assert us_session_phase(_sh(y, m, d, 22, 0)) == "open"
    assert us_session_phase(_utc(y, m, d, 13, 30)) == "open"
    assert us_session_phase(_utc(y, m, d, 14, 29)) == "open"


def test_weekend_closed():
    # 2026-07-18 Sat / 2026-07-19 Sun,即使钟点落在 RTH 也对 closed
    assert us_session_phase(_ny(2026, 7, 18, 10, 0)) == "closed"
    assert us_session_phase(_ny(2026, 7, 19, 10, 0)) == "closed"
    assert us_session_phase(_utc(2026, 7, 18, 14, 0)) == "closed"


def test_is_us_rth_and_scan_callers_follow_ny():
    """wheel timing / chan session_only / alert rth 共用这一开盘定义。"""
    edt_open = _utc(2026, 7, 15, 13, 30)
    edt_pre = _utc(2026, 7, 15, 13, 29)
    est_open = _utc(2026, 1, 14, 14, 30)
    est_pre = _utc(2026, 1, 14, 14, 29)

    assert is_us_rth(edt_open) is True
    assert is_us_rth(edt_pre) is False
    assert is_us_rth(est_open) is True
    assert is_us_rth(est_pre) is False

    assert scan_session_allows("rth", edt_open) is True
    assert scan_session_allows("rth", edt_pre) is False
    assert scan_session_allows("rth", est_open) is True
    assert scan_session_allows("eod", edt_open) is False
    assert scan_session_allows("eod", _utc(2026, 7, 15, 20, 0)) is True
    assert scan_session_allows("always", edt_pre) is True
