"""后端 grade ↔ 前端 TradeTier 同一套映射"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_opportunities import attach_trade_tier, grade_to_trade_tier  # noqa: E402


def test_dual_actionable_is_priority():
    assert grade_to_trade_tier("dual", actionable=True) == "PRIORITY"


def test_dual_earnings_demotes_to_queue():
    assert grade_to_trade_tier("dual", actionable=True, covers_earnings=True) == "QUEUE"
    assert grade_to_trade_tier(
        "dual", actionable=True, covers_earnings=True, demote_earnings=False,
    ) == "PRIORITY"


def test_score_and_timing_are_queue():
    assert grade_to_trade_tier("score", actionable=True) == "QUEUE"
    assert grade_to_trade_tier("timing", actionable=True) == "QUEUE"


def test_watch_blocked_not_actionable_are_watch():
    assert grade_to_trade_tier("watch", actionable=True) == "WATCH"
    assert grade_to_trade_tier("blocked", actionable=True) == "WATCH"
    assert grade_to_trade_tier("dual", actionable=False) == "WATCH"
    assert grade_to_trade_tier("score", actionable=False) == "WATCH"


def test_manage_kind():
    assert grade_to_trade_tier("watch", kind="MANAGE") == "MANAGE"


def test_attach_trade_tier_on_item():
    item = {"grade": "dual", "actionable": True, "covers_earnings": False}
    attach_trade_tier(item)
    assert item["trade_tier"] == "PRIORITY"
    item2 = {"grade": "score", "actionable": True}
    attach_trade_tier(item2)
    assert item2["trade_tier"] == "QUEUE"


def test_income_put_is_hard_block():
    from app.core.wheel_opportunities import _grade_actionable, _red_flags
    flags = _red_flags(
        side="PUT", trend="DOWN", covers_earnings=False, exceeds_capital=False,
        below_floor=False, earnings_hard=True, stance="income",
    )
    assert "不愿接货·不做" in flags
    g, a = _grade_actionable("dual", "READY", 80.0, 50.0, flags)
    assert g == "blocked" and a is False


def test_acquire_downtrend_not_flagged():
    from app.core.wheel_opportunities import _red_flags
    flags = _red_flags(
        side="PUT", trend="DOWN", covers_earnings=False, exceeds_capital=False,
        below_floor=False, earnings_hard=True, stance="acquire",
    )
    assert "趋势DOWN" not in flags
    assert "不愿接货·不做" not in flags


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
