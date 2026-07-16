"""持仓决策树单元测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_decision import decide_position  # noqa: E402


def _item(**kw):
    base = dict(
        side="PUT", strike=100.0, spot=110.0, dte=35, current_price=1.0,
        buyback_ask=1.0, open_price=3.0, profit_pct=None, itm=False,
        delta=0.2, expiring=False, qty=1, contract_size=100,
    )
    base.update(kw)
    return base


def test_profit_hit():
    r = decide_position(_item(profit_pct=60.0), 15, 50)
    assert r["action_code"] == "CLOSE"
    assert r["action_hint"] == "止盈平仓"
    assert r["prefer_card"] == "no_roll"


def test_deep_itm_beats_profit():
    r = decide_position(_item(itm=True, delta=0.62, spot=95, profit_pct=55.0), 15, 50)
    assert r["deep_itm"]
    assert r["action_code"] == "ROLL_ADJUST"
    assert r["prefer_card"] == "adjust_strike"
    assert r["action_priority"] == 1


def test_hold_for_theta():
    r = decide_position(
        _item(dte=5, profit_pct=55.0, current_price=0.4, buyback_ask=0.4, expiring=True),
        15, 50,
    )
    assert r["action_code"] == "HOLD_THETA"
    assert r["decision_tree"]["hold_for_theta"]


def test_fee_trap_hold_theta():
    # 买回 0.05 * 100 = $5 < min_close_notional 20, 高浮盈 OTM
    r = decide_position(
        _item(dte=20, profit_pct=80.0, buyback_ask=0.05, current_price=0.05),
        15, 50,
        pos_cfg={"min_close_notional": 20},
    )
    assert r["action_code"] == "HOLD_THETA"
    assert r["decision_tree"]["fee_trap"]


def test_soft_profit_low_yield():
    r = decide_position(_item(current_price=0.3, buyback_ask=0.3, profit_pct=40.0), 15, 50)
    assert r["low_yield"]
    assert r["action_code"] == "REPLACE"
    assert "软止盈" in (r["action_hint"] or "")


def test_pure_low_yield():
    r = decide_position(_item(current_price=0.3, buyback_ask=0.3, profit_pct=10.0), 15, 50)
    assert r["action_code"] == "REPLACE"
    assert r["action_hint"] == "平仓换仓(剩余年化低)"


def test_remaining_ann_uses_buyback_ask():
    # close 0.5 vs last 2.0 — 应用 0.5 算剩余年化
    r = decide_position(
        _item(current_price=2.0, buyback_ask=0.5, dte=30, profit_pct=20.0),
        15, 50,
    )
    # 0.5/100*365/30*100 ≈ 6.08
    assert r["remaining_annualized"] is not None
    assert abs(r["remaining_annualized"] - 6.08) < 0.1
    assert r["low_yield"]


def test_put_expiring_itm_prepare():
    r = decide_position(
        _item(side="PUT", itm=True, delta=0.45, spot=99, dte=5, expiring=True, profit_pct=-10.0),
        15, 50,
    )
    assert r["action_code"] == "PREPARE_ASSIGN"
    assert "接货" in (r["action_hint"] or "")


def test_cc_dividend_early_assign():
    r = decide_position(
        _item(side="CALL", strike=100, spot=105, itm=True, delta=0.6, profit_pct=-20.0,
              days_to_ex_div=3),
        15, 50,
    )
    assert r["early_assign_risk"]
    assert r["action_code"] == "ROLL_ADJUST"


def test_shallow_itm_put_observe():
    # 价内 0.5%, delta 0.4 → 浅 ITM, 无其它触发
    r = decide_position(
        _item(side="PUT", strike=100, spot=99.5, itm=True, delta=0.4, dte=30, profit_pct=10.0,
              buyback_ask=2.0, current_price=2.0),
        15, 50,
    )
    assert r.get("shallow_itm")
    assert r["action_code"] == "NONE" or r["action_priority"] >= 6


def test_21dte_roll_out():
    r = decide_position(_item(dte=18, profit_pct=30.0, buyback_ask=1.5), 15, 50)
    assert r["roll_21dte"]
    assert r["action_code"] == "ROLL"
    assert r["prefer_card"] == "roll_out"


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
