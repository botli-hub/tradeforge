"""到期日选取 + 核心 DTE 优先"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.leaps_monitor import select_expiries  # noqa: E402


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
