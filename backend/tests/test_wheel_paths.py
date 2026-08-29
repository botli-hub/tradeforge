"""可成交报价 + 三列路径"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_decision import decide_position  # noqa: E402
from app.core.wheel_paths import close_claimable, quote_quality  # noqa: E402


def _item(**kw):
    base = dict(
        side="PUT", strike=100.0, spot=110.0, dte=35, current_price=1.0,
        buyback_ask=1.0, open_price=3.0, profit_pct=None, itm=False,
        delta=0.2, expiring=False, qty=1, contract_size=100,
    )
    base.update(kw)
    return base


def test_quote_wide_spread():
    q = quote_quality(1.20, 1.80, 1.50)
    assert q["spread_pct"] is not None and q["spread_pct"] > 8
    assert q["wide_spread"] is True
    assert q["fillable"] is False
    assert q["conservative"] > q["ask"]


def test_quote_tight_fillable():
    q = quote_quality(1.48, 1.52, 1.50)
    assert q["wide_spread"] is False
    assert q["fillable"] is True


def test_wide_spread_does_not_close_on_book_50():
    """账面 55% 但点差 40%,不能宣称止盈 CLOSE."""
    r = decide_position(
        _item(
            profit_pct=55.0, buyback_ask=1.80, buyback_bid=1.20,
            current_price=1.50, dte=35, days_held=3,
        ),
        15, 50,
    )
    assert r["paths"]["quote"]["wide_spread"] is True
    assert r["action_code"] != "CLOSE"
    assert r["paths"]["recommend"] in ("hold", "assign")


def test_tight_spread_velocity_still_closes():
    r = decide_position(
        _item(
            profit_pct=55.0, buyback_ask=1.20, buyback_bid=1.16,
            current_price=1.18, dte=35, days_held=3,
        ),
        15, 50,
    )
    assert r["action_code"] == "CLOSE"
    assert r["paths"]["recommend"] == "close"


def test_acquire_deep_itm_paths_recommend_assign():
    r = decide_position(
        _item(itm=True, delta=0.62, spot=95, profit_pct=-20.0, floor_price=105, stance="acquire"),
        15, 50,
    )
    assert r["action_code"] == "PREPARE_ASSIGN"
    p = r["paths"]
    assert p["recommend"] == "assign"
    assert p["assign"]["floor_ok"] is True
    assert p["assign"]["effective_cost"] == 97  # 100-3
    assert p["roll"]["close_cost"] is not None
    assert p["close"]["freed"] == 10000.0


def test_above_floor_paths_recommend_roll():
    r = decide_position(
        _item(
            itm=True, delta=0.62, spot=95, strike=100, profit_pct=-20.0,
            floor_price=90, stance="acquire",
        ),
        15, 50,
    )
    assert r["paths"]["recommend"] == "roll"
    assert r["paths"]["assign"]["floor_ok"] is False


def test_close_claimable_needs_fillable():
    q = quote_quality(1.2, 1.8, 1.5)
    assert close_claimable(
        profit_pct=55, profit_target=50, quote=q, profit_conservative=40,
    ) is False


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
