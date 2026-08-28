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
