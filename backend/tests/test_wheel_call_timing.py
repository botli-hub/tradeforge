"""卖 Call 时机:挂机(HOLDING)+触线观察(可不持股)。不含交易建议断言。"""
from app.core.wheel_call_timing import (
    GRADE_PRIORITY, GRADE_READY, GRADE_SKIP, GRADE_WATCH,
    attach_cc_timing, evaluate_cc_timing, is_holding_call_opp,
    normalize_stance, split_holding_cc, strike_floor,
)


def test_stance_default_acquire():
    assert normalize_stance(None) == "acquire"
    assert normalize_stance("只收租") == "income"


def test_skip_under_100_shares():
    r = evaluate_cc_timing(stance="income", shares=80, cost_basis=100, spot=110, ema_touch=True)
    assert r["grade"] == GRADE_SKIP
    assert r["show_find_call"] is False


def test_no_touch_is_watch_even_income():
    r = evaluate_cc_timing(stance="income", shares=100, cost_basis=100, spot=101)
    assert r["grade"] == GRADE_WATCH
    assert r["show_find_call"] is False
    assert r["timing_ready"] is False
    assert r["ema_touch"] is False


def test_income_touch_is_ready():
    r = evaluate_cc_timing(stance="income", shares=100, cost_basis=100, spot=101, ema_touch=True, ema_type="EMA50")
    assert r["grade"] == GRADE_READY
    assert r["show_find_call"] is True
    assert r["timing_ready"] is True


def test_income_touch_uncovered_is_priority():
    r = evaluate_cc_timing(
        stance="income", shares=200, cost_basis=50, spot=50.5,
        uncovered_days=3, candidate_ann=18, candidate_dte=30, candidate_spread=4,
        min_annualized=12, dte_min=10, dte_max=55,
        ema_touch=True,
    )
    assert r["grade"] == GRADE_PRIORITY
    assert r["candidate_ok"] is True


def test_acquire_no_touch_no_find():
    r = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=100, spot=104)
    assert r["grade"] == GRADE_WATCH
    assert r["show_find_call"] is False


def test_acquire_touch_below_cushion_is_ready():
    r = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=100, spot=101, ema_touch=True)
    assert r["grade"] == GRADE_READY
    assert r["show_find_call"] is True
    assert r["cushion_pct"] == 1.0


def test_acquire_touch_left_cost_is_priority():
    r = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=100, spot=104, ema_touch=True)
    assert r["grade"] == GRADE_PRIORITY
    assert r["show_find_call"] is True


def test_acquire_touch_iv_is_priority():
    r = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=100.2, iv_rank=70, ema_touch=True,
    )
    assert r["grade"] == GRADE_PRIORITY
    assert r["iv_lift"] is True


def test_cfg_overrides_cushion_only_lifts_priority():
    r = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=102, ema_touch=True,
        cfg={"wheel_timing": {"call_acquire_cushion_pct": 5}, "wheel_position": {"profit_target_pct": 50}},
    )
    assert r["grade"] == GRADE_READY
    assert r["show_find_call"] is True
    r2 = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=106, ema_touch=True,
        cfg={"wheel_timing": {"call_acquire_cushion_pct": 5}},
    )
    assert r2["grade"] == GRADE_PRIORITY


def test_call_opp_allows_non_holding():
    # Call 可不持股观察; Put 仍放行; HOLDING 仍放行
    assert is_holding_call_opp({"side": "PUT", "context": {"stage": "IDLE"}}) is True
    assert is_holding_call_opp({"side": "CALL", "context": {"stage": "HOLDING"}}) is True
    assert is_holding_call_opp({"side": "CALL", "context": {"stage": "IDLE"}}) is True
    assert is_holding_call_opp({"side": "CALL", "context": {"stage": "CSP_OPEN"}}) is True


def test_attach_wait_without_touch():
    hint = {"symbol": "AAA", "cycle_id": 1, "notes": ["x"], "cc_contracts": 1}
    t = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=10, spot=10.1)
    row = attach_cc_timing(hint, t)
    assert row["cc_grade"] == GRADE_WATCH
    assert row["next_step"] == "WAIT_CC_TIMING"
    assert row["show_find_call"] is False
    buckets = split_holding_cc([row])
    assert len(buckets["watch"]) == 1


def test_attach_ready_with_touch():
    hint = {"symbol": "AAA", "cycle_id": 1, "notes": ["x"], "cc_contracts": 1}
    t = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=10, spot=10.1, ema_touch=True)
    row = attach_cc_timing(hint, t)
    assert row["cc_grade"] == GRADE_READY
    assert row["next_step"] == "SELL_CALL"
    assert row["show_find_call"] is True


def test_strike_floor_uses_sell_above_when_higher():
    assert strike_floor(100, 120) == 120


def test_strike_floor_ignores_none():
    assert strike_floor(100, None) == 100
    assert strike_floor(100, 0) == 100


def test_strike_floor_is_max_of_cb_and_sell_above():
    assert strike_floor(100, 80) == 100
    assert strike_floor(90, 110) == 110
    assert strike_floor(None, 50) == 50
    assert strike_floor(None, None) is None


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
