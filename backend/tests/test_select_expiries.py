"""到期日选取 + 核心 DTE 优先"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_expiries import (  # noqa: E402
    chain_scope_kwargs,
    classify_expiry_cycle,
    pick_windowed_expiries,
    select_expiries,
    select_near_term_expiries,
)


def test_prefers_core_over_near_month():
    # 模拟今天: pad 后 14-52, 核心 21-45
    eligible = [
        ("2026-07-31", 14),
        ("2026-08-07", 21),
        ("2026-08-14", 28),
        ("2026-08-21", 35),  # 用户合约
        ("2026-08-28", 42),
        ("2026-09-18", 63),  # 若在窗口外不会出现;这里假设在
    ]
    # 只取窗口内
    eligible = [e for e in eligible if 14 <= e[1] <= 52]
    sel, skip = select_expiries(
        eligible, max_n=3, core_dte_min=21, core_dte_max=45, prefer_core=True,
    )
    dates = [e[0] for e in sel]
    # 核心优先时不应把 07-31 占满前 3 而挤掉 08-21
    assert "2026-08-21" in dates, dates
    assert "2026-07-31" not in dates or len(dates) > 3


def test_max_six_includes_aug21():
    eligible = [
        ("2026-07-31", 14),
        ("2026-08-07", 21),
        ("2026-08-14", 28),
        ("2026-08-21", 35),
        ("2026-08-28", 42),
    ]
    sel, skip = select_expiries(
        eligible, max_n=6, core_dte_min=21, core_dte_max=45, prefer_core=True,
    )
    assert "2026-08-21" in [e[0] for e in sel]
    assert skip == []


def test_old_behavior_near_first():
    eligible = [
        ("2026-07-31", 14),
        ("2026-08-07", 21),
        ("2026-08-14", 28),
        ("2026-08-21", 35),
    ]
    sel, skip = select_expiries(eligible, max_n=3, prefer_core=False)
    assert [e[0] for e in sel] == ["2026-07-31", "2026-08-07", "2026-08-14"]
    assert "2026-08-21" in [e[0] for e in skip]


def test_suggest_max6_covers_42dte_weekly():
    """旧 _suggest 对 21–45 取前 3 会丢掉 42DTE 周权; max_expiries=6 应覆盖。"""
    today = date(2026, 7, 17)
    expirations = [
        "2026-07-31",  # 14
        "2026-08-07",  # 21
        "2026-08-14",  # 28
        "2026-08-21",  # 35
        "2026-08-28",  # 42  ← 旧 in_range[:3] 漏掉
        "2026-09-18",  # 63
    ]
    sel, skip, elig = pick_windowed_expiries(
        expirations, dte_min=21, dte_max=45,
        today=today, max_n=6, prefer_core=True, pad_days=7,
    )
    dates = [str(e[0])[:10] for e in sel]
    assert "2026-08-28" in dates, dates
    assert "2026-08-21" in dates, dates
    # pad 近月可补,但不能挤掉核心
    assert "2026-08-07" in dates
    assert "2026-09-18" not in [str(e[0])[:10] for e in elig]


def test_suggest_prefer_core_beats_pad_near_month_when_capped():
    """max_n=3 + prefer_core: 14DTE 不得占满而挤掉 35DTE。"""
    today = date(2026, 7, 17)
    expirations = [
        "2026-07-31",  # 14 pad
        "2026-08-07",  # 21
        "2026-08-14",  # 28
        "2026-08-21",  # 35
        "2026-08-28",  # 42
    ]
    sel, skip, _elig = pick_windowed_expiries(
        expirations, dte_min=21, dte_max=45,
        today=today, max_n=3, prefer_core=True, pad_days=7,
    )
    dates = [str(e[0])[:10] for e in sel]
    assert "2026-07-31" not in dates, dates
    assert set(dates) == {"2026-08-07", "2026-08-14", "2026-08-21"}
    assert "2026-08-28" in [str(e[0])[:10] for e in skip]


def test_suggest_old_slice3_without_prefer_drops_core():
    today = date(2026, 7, 17)
    expirations = [
        "2026-07-31",
        "2026-08-07",
        "2026-08-14",
        "2026-08-21",
    ]
    sel, skip, _elig = pick_windowed_expiries(
        expirations, dte_min=21, dte_max=45,
        today=today, max_n=3, prefer_core=False, pad_days=7,
    )
    dates = [str(e[0])[:10] for e in sel]
    assert dates == ["2026-07-31", "2026-08-07", "2026-08-14"]
    assert "2026-08-21" in [str(e[0])[:10] for e in skip]


def _fridays(start: date, days: int):
    out = []
    d = start
    end = start + timedelta(days=days)
    while d <= end:
        if d.weekday() == 4:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_near_term_three_weeks_weekly_and_three_monthlies():
    """2026-10-13(周二): 3 周内周权 + 接下来 3 张月权;更远周权与第 4 张月权不扫。"""
    today = date(2026, 10, 13)
    assert today.weekday() == 1
    expirations = _fridays(today, 120)
    sel, skip = select_near_term_expiries(expirations, today=today, weekly_weeks=3, monthly_months=3)
    chosen = {str(e[0])[:10]: e[2] for e in sel}
    # 10-16 是第三个周五 → 月权,不占周权名额
    assert chosen["2026-10-16"] == "monthly"
    assert chosen["2026-10-23"] == "weekly"
    assert chosen["2026-10-30"] == "weekly"
    assert chosen["2026-11-20"] == "monthly"
    assert chosen["2026-12-18"] == "monthly"
    assert "2026-11-06" not in chosen  # 第 4 周周权
    assert "2027-01-15" not in chosen  # 第 4 张月权
    skipped = {str(e[0])[:10] for e in skip}
    assert "2026-11-06" in skipped
    assert "2027-01-15" in skipped
    assert all(e[1] <= 21 for e in sel if e[2] == "weekly")
    assert sum(1 for e in sel if e[2] == "monthly") == 3


def test_cycle_overrides_third_friday_heuristic():
    today = date(2026, 10, 13)
    expirations = ["2026-10-16", "2026-11-20", "2026-12-18", "2027-01-15"]
    sel, _skip = select_near_term_expiries(
        expirations,
        today=today,
        cycles={"2026-10-16": "ExpirationCycle.WEEK"},
        weekly_weeks=3,
        monthly_months=3,
    )
    kinds = {str(e[0])[:10]: e[2] for e in sel}
    assert kinds["2026-10-16"] == "weekly"
    assert kinds["2026-11-20"] == "monthly"
    assert "2027-01-15" in kinds  # 10-16 不再占月权名额


def test_chain_scope_defaults_and_dte_window_override():
    scope = chain_scope_kwargs({})
    assert scope == {"expiry_mode": "near_weekly_monthly", "weekly_weeks": 3, "monthly_months": 3}
    override = chain_scope_kwargs({
        "wheel_timing": {"expiry_mode": "dte_window", "weekly_weeks": 2, "monthly_months": "4"},
    })
    assert override["expiry_mode"] == "dte_window"
    assert override["weekly_weeks"] == 2
    assert override["monthly_months"] == 4
    assert classify_expiry_cycle("MONTH", date(2026, 10, 23)) == "monthly"
    assert classify_expiry_cycle(None, date(2026, 10, 23)) == "weekly"


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
