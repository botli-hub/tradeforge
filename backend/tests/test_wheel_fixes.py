"""Wheel 评审修复:张数、费用、愿接冻结、事件未知、Roll 与重放。"""
import inspect
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.wheel import _suggest, roll_options  # noqa: E402
from app.core.wheel_decision import decide_position  # noqa: E402
from app.core.wheel_decision_lib import build_assign_checklist  # noqa: E402
from app.core.wheel_replay import replay_with_decision_tree  # noqa: E402
from app.core.wheel_risk import expiry_week_violations, weekly_put_cap_violation  # noqa: E402
from app.core.wheel_roll import validate_roll_economics  # noqa: E402
from app.core.wheel_score import score_contract, DEFAULT_SCAN_CFG  # noqa: E402
from app.core.wheel_sizing import (  # noqa: E402
    buffer_sigma,
    effective_position_floor,
    estimate_pot,
    expected_move,
    limit_ladder,
    net_annualized,
    option_tick,
    suggest_contract_qty,
)
from app.services.alert_engine import pnl_bucket, position_fingerprint  # noqa: E402


def _item(**kw):
    base = dict(
        side="PUT", strike=100.0, spot=110.0, dte=35, current_price=1.0,
        buyback_ask=1.0, open_price=3.0, profit_pct=None, itm=False,
        delta=0.2, expiring=False, qty=1, contract_size=100,
    )
    base.update(kw)
    return base


def test_suggest_qty_unknown_vs_exhausted():
    assert suggest_contract_qty(strike=100) is None
    assert suggest_contract_qty(strike=None, equity=100000) is None
    assert suggest_contract_qty(
        strike=50, equity=100000, max_symbol_pct=0.25, idle_cash=80000,
    ) == 5
    assert suggest_contract_qty(
        strike=100, symbol_headroom=0, equity=100000, idle_cash=100000,
    ) == 0


def test_net_annualized_subtracts_fee():
    r = net_annualized(2.0, 100.0, 30, 0.65, 100)
    assert r["annualized_gross"] > r["annualized"]
    assert r["premium_net"] < 2


def test_expected_move_pot_and_buffer_sigma():
    em = expected_move(100, 0.20, 36.5)
    assert em is not None and 6 < em < 7
    assert estimate_pot(0.25) == 0.5
    sig = buffer_sigma("PUT", 100, 90, 0.20, 36.5)
    assert sig is not None and sig > 1


def test_structure_score_is_opt_in_and_headroom_is_not_multiplied():
    cfg = dict(DEFAULT_SCAN_CFG)
    base = score_contract(30.0, "PUT", 0.25, 2.0, False, None, {"trend": "UP"}, cfg)
    headed = score_contract(
        30.0, "PUT", 0.25, 2.0, False, None, {"trend": "UP"}, cfg, headroom_ratio=1,
    )
    structured = score_contract(
        30.0, "PUT", 0.25, 2.0, False, None, {"trend": "UP"}, cfg,
        include_structure=True, dte=7, pop=0.75,
    )
    assert base["score"] == 30.0
    assert headed["score"] == base["score"]
    assert headed["factors"]["headroom"] > 1
    assert structured["score"] != base["score"]
    assert structured["factors"]["dte_pref"] < 1


def test_entry_floor_does_not_tighten_by_default():
    assert effective_position_floor(90, 80, 0) == 90
    assert effective_position_floor(90, 100, 0) == 90
    assert effective_position_floor(90, 70, 10) == 70
    assert effective_position_floor(None, 88, 0) == 88


def test_earnings_unknown_is_not_stored_as_no_event(monkeypatch):
    import app.core.earnings as earnings
    earnings._CACHE.clear()
    monkeypatch.setattr(
        "app.core.config.get_effective_config",
        lambda: {"finnhub_api_key": ""},
    )
    info = earnings.earnings_lookup("AAPL")
    assert info["status"] == "unknown"
    assert earnings.get_next_earnings("AAPL") is None
    assert earnings._CACHE["AAPL"][1]["status"] == "unknown"


def test_call_extrinsic_below_dividend_flags_early_assign():
    r = decide_position(
        _item(
            side="CALL", strike=100, spot=101, delta=0.20, itm=True, dte=20,
            days_to_ex_div=1, dividend_amount=1.5, buyback_ask=1.2,
            current_price=1.2, open_price=2, profit_pct=-10, cost_basis=90,
        ),
        15, 50,
    )
    assert r["early_assign_risk"]
    assert r["action_code"] == "ROLL_ADJUST"


def test_put_early_assign_does_not_change_action():
    r = decide_position(
        _item(
            itm=False, delta=0.85, spot=110, strike=100, dte=30,
            current_price=0.2, buyback_ask=0.2, open_price=1.0, profit_pct=10,
        ),
        15, 50,
    )
    assert r["put_early_assign_risk"]
    assert r["action_code"] != "ROLL_ADJUST"


def test_prepare_assign_still_offers_adjust_card():
    r = decide_position(
        _item(itm=True, delta=0.62, spot=95, profit_pct=-20, stance="acquire", floor_price=110),
        15, 50,
    )
    assert r["action_code"] == "PREPARE_ASSIGN"
    assert r["prefer_card"] == "no_roll"
    assert r["also_compare"] == ["adjust_strike"]


def test_collateral_covers_only_when_cash_is_known():
    common = dict(
        side="PUT", strike=100, qty=1, size=100, floor_price=110,
        strike_above_floor=False, itm=True, deep_itm=True, expiring=False, early_assign=False,
    )
    assert build_assign_checklist(**common)["collateral_covers"] is True
    short = build_assign_checklist(**common, cash=100, csp_collateral=10000)
    assert short["collateral_covers"] is False


def test_roll_spot_is_initialized_before_use():
    src = inspect.getsource(roll_options)
    assert src.find("spot = None") < src.find("float(spot)")
    suggest = inspect.getsource(_suggest)
    assert "willing_price_missing" in suggest


def test_roll_credit_and_floor_rules():
    assert validate_roll_economics(side="PUT", buyback=1, sell=2, new_strike=95) is None
    assert validate_roll_economics(
        side="PUT", buyback=2, sell=1, original_credit=1, new_strike=95,
    )
    assert validate_roll_economics(
        side="PUT", buyback=1, sell=2, new_strike=110, floor_price=100,
    )
    assert validate_roll_economics(
        side="PUT", buyback=1, sell=2, new_strike=110, floor_price=None,
    ) is None


def test_limit_ladder_starts_at_mid_and_floors_at_bid():
    ladder = limit_ladder(1.0, 1.20)
    assert ladder[0]["limit"] == 1.10
    assert ladder[-1]["limit"] == 1.0
    assert option_tick(3.5) == 0.10
    assert option_tick(1.5) == 0.05


def test_fingerprint_changes_when_pnl_bucket_changes():
    base = {"contract_code": "X", "action_code": "CLOSE", "action_priority": 2, "dte": 10}
    assert pnl_bucket(None) == "pnl?"
    assert position_fingerprint(base) == position_fingerprint(dict(base))
    assert position_fingerprint(base) != position_fingerprint(dict(base, profit_pct=60))


def test_expiry_week_and_weekly_cap_defaults_off():
    nav = {"option_rows": [{"expiry": "2026-10-16", "strike": 100, "qty": 1, "contract_size": 100}], "equity": 1000}
    assert expiry_week_violations(nav, 1000, None) == []
    assert expiry_week_violations(nav, 1000, 0) == []
    assert expiry_week_violations(nav, 1000, 0.40)
    assert weekly_put_cap_violation(3, 0) is None
    assert weekly_put_cap_violation(2, 2) is None
    assert weekly_put_cap_violation(3, 2)


def test_replay_smoke_is_not_a_validated_edge():
    start = date(2026, 1, 5)
    bars = []
    quotes = []
    expiry = (start + timedelta(days=40)).isoformat()
    for i in range(8):
        day = start + timedelta(days=i)
        bars.append({"date": day.isoformat(), "close": 100})
        quotes.append({
            "date": day.isoformat(),
            "contract_code": "P1",
            "side": "PUT",
            "strike": 90,
            "expiry": expiry,
            "delta": 0.25,
            "bid": 2.0,
            "ask": 2.1,
        })
    r = replay_with_decision_tree(bars, quotes, {"warmup_bars": 3, "min_annualized": 0, "dte": 30})
    assert r["ok"]
    assert r["validated_edge"] is False
    assert r["trade_count"] >= 1


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except TypeError:
                if name == "test_earnings_unknown_is_not_stored_as_no_event":
                    print(f"SKIP {name}")
                    continue
                raise
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
