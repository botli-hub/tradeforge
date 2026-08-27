"""卖 Call 时机:持股池 + 立场分流。不含交易建议断言。"""
from app.core.wheel_call_timing import (
    GRADE_PRIORITY, GRADE_READY, GRADE_SKIP, GRADE_WATCH,
    attach_cc_timing, evaluate_cc_timing, is_holding_call_opp,
    normalize_stance, split_holding_cc,
)


def test_stance_default_acquire():
    assert normalize_stance(None) == "acquire"
    assert normalize_stance("只收租") == "income"


def test_skip_under_100_shares():
    r = evaluate_cc_timing(stance="income", shares=80, cost_basis=100, spot=110)
    assert r["grade"] == GRADE_SKIP
    assert r["show_find_call"] is False


def test_income_holding_is_ready():
    r = evaluate_cc_timing(stance="income", shares=100, cost_basis=100, spot=101)
    assert r["grade"] == GRADE_READY
    assert r["show_find_call"] is True
    assert r["timing_ready"] is True


def test_income_candidate_or_uncovered_is_priority():
    r = evaluate_cc_timing(
        stance="income", shares=200, cost_basis=50, spot=50.5,
        uncovered_days=3, candidate_ann=18, candidate_dte=30, candidate_spread=4,
        min_annualized=12, dte_min=21, dte_max=45,
    )
    assert r["grade"] == GRADE_PRIORITY
    assert r["candidate_ok"] is True


def test_acquire_below_cushion_is_watch():
    r = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=100, spot=101)
    assert r["grade"] == GRADE_WATCH
    assert r["show_find_call"] is False
    assert r["cushion_pct"] == 1.0


def test_acquire_left_cost_is_ready():
    r = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=100, spot=104)
    assert r["grade"] == GRADE_READY
    assert r["show_find_call"] is True


def test_acquire_left_cost_plus_iv_is_priority():
    r = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=104, iv_rank=60,
    )
    assert r["grade"] == GRADE_PRIORITY


def test_acquire_iv_lift_at_cost_promotes_ready():
    r = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=100.2, iv_rank=70,
    )
    assert r["grade"] == GRADE_READY
    assert r["iv_lift"] is True


def test_cfg_overrides_cushion_not_position_quant():
    r = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=102,
        cfg={"wheel_timing": {"call_acquire_cushion_pct": 5}, "wheel_position": {"profit_target_pct": 50}},
    )
    assert r["grade"] == GRADE_WATCH  # 2% < 5%
    r2 = evaluate_cc_timing(
        stance="acquire", shares=100, cost_basis=100, spot=106,
        cfg={"wheel_timing": {"call_acquire_cushion_pct": 5}},
    )
    assert r2["grade"] == GRADE_READY


def test_call_opp_only_when_holding():
    assert is_holding_call_opp({"side": "PUT", "context": {"stage": "IDLE"}}) is True
    assert is_holding_call_opp({"side": "CALL", "context": {"stage": "HOLDING"}}) is True
    assert is_holding_call_opp({"side": "CALL", "context": {"stage": "IDLE"}}) is False
    assert is_holding_call_opp({"side": "CALL", "context": {"stage": "CSP_OPEN"}}) is False


def test_attach_and_split():
    hint = {"symbol": "AAA", "cycle_id": 1, "notes": ["x"], "cc_contracts": 1}
    t = evaluate_cc_timing(stance="acquire", shares=100, cost_basis=10, spot=10.1)
    row = attach_cc_timing(hint, t)
    assert row["cc_grade"] == GRADE_WATCH
    assert row["next_step"] == "WAIT_CC_TIMING"
    assert row["show_find_call"] is False
    buckets = split_holding_cc([row])
    assert len(buckets["watch"]) == 1
    assert buckets["priority"] == []
