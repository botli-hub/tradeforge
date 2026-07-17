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
    assert "止盈平仓" in (r["action_hint"] or "")
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


def test_hold_theta_otm_dte14_high_remaining():
    """ARM CC 类:浮盈>50% 但 DTE14 OTM 且剩余年化仍高 → 吃 θ,不硬止盈"""
    # rem_ann = 4.9/340*365/14*100 ≈ 37.6
    r = decide_position(
        _item(
            side="CALL", strike=340.0, spot=300.0, dte=14, itm=False,
            profit_pct=53.3, buyback_ask=4.9, current_price=4.9,
        ),
        15, 50,
    )
    assert r["action_code"] == "HOLD_THETA"
    assert "剩余年化" in (r["action_hint"] or "") or "theta" in (r["action_hint"] or "").lower()
    assert r["decision_tree"]["residual_worth_keeping"]


def test_profit_hit_still_closes_when_dte_long():
    """DTE 仍长(如35)时,浮盈达标仍止盈,不无限拖"""
    r = decide_position(
        _item(dte=35, profit_pct=55.0, buyback_ask=1.5, current_price=1.5, strike=100.0),
        15, 50,
    )
    assert r["action_code"] == "CLOSE"


def test_healthy_otm_near_dte_no_force_roll():
    """OTM + 剩余年化尚可 + DTE18 未止盈 → 不必机械 21DTE Roll"""
    r = decide_position(
        _item(dte=18, profit_pct=20.0, buyback_ask=2.0, current_price=2.0, strike=100.0, spot=110.0),
        15, 50,
    )
    # rem ≈ 2/100*365/18*100 ≈ 40.5
    assert r["action_code"] in ("NONE", "HOLD_THETA")
    assert not (r.get("roll_21dte") and r["action_code"] == "ROLL")


def test_max_hold_profit_caps_theta():
    """浮盈极高(≥80%)且非极临期 → 止盈优先于吃 θ"""
    r = decide_position(
        _item(
            side="CALL", strike=340.0, spot=300.0, dte=14, itm=False,
            profit_pct=85.0, buyback_ask=1.5, current_price=1.5,
        ),
        15, 50,
    )
    assert r["action_code"] == "CLOSE"
    assert r["decision_tree"].get("profit_cap_close")


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
    # 剩余年化偏低 + 未达软止盈 → 21DTE Roll(健康 OTM 不再机械 roll)
    # rem = 0.4/100*365/18*100 ≈ 8.1 < 15; profit 10 < soft 30
    r = decide_position(_item(dte=18, profit_pct=10.0, buyback_ask=0.4, current_price=0.4), 15, 50)
    assert r["roll_21dte"]
    assert r["action_code"] == "ROLL"
    assert r["prefer_card"] == "roll_out"


def test_underwater_otm_not_healthy():
    """浮亏 OTM:不得标成健康收租/吃θ;剩余权利金高≠值得拿"""
    # 开 3.0 买回 4.0 → 浮亏; rem 仍可能很高
    r = decide_position(
        _item(
            side="PUT", strike=240.0, spot=245.0, dte=30, itm=False,
            profit_pct=-32.0, buyback_ask=4.0, current_price=4.0, open_price=3.0,
        ),
        15, 50,
    )
    assert r["decision_tree"]["underwater"]
    assert not r["decision_tree"]["hold_for_theta"]
    assert not r["decision_tree"]["residual_worth_keeping"]
    assert r["action_code"] == "NONE"
    assert "浮亏" in (r["action_hint"] or "")
    assert r["decision_confidence"] is not None
    assert r["decision_confidence"] <= 60  # 条件持有,置信偏低


def test_underwater_strike_above_floor_close():
    """CSP 浮亏且 strike > 接货底线 → 优先止损/不宜等接货"""
    r = decide_position(
        _item(
            side="PUT", strike=240.0, spot=250.0, dte=35, itm=False,
            profit_pct=-25.0, buyback_ask=3.5, current_price=3.5,
            floor_price=220.0,
        ),
        15, 50,
    )
    assert r["strike_above_floor"]
    assert r["action_code"] == "CLOSE"
    assert "底线" in (r["action_hint"] or "")
    assert r["decision_confidence"] >= 80


def test_underwater_threatened_rolls():
    """浮亏 + 临近 DTE + 安全垫薄 → Roll 防守,不是放任"""
    # buffer = (245-240)/245 ≈ 2% < 5%; dte 18 ≤ 21
    r = decide_position(
        _item(
            side="PUT", strike=240.0, spot=245.0, dte=18, itm=False,
            profit_pct=-20.0, buyback_ask=3.0, current_price=3.0,
            floor_price=250.0,  # strike 在底线内,不走 strike_above_floor
        ),
        15, 50,
    )
    # floor 250 > strike 240 → not strike_above_floor
    assert not r["strike_above_floor"]
    assert r["decision_tree"]["threatened_underwater"]
    assert r["action_code"] == "ROLL"
    assert r["prefer_card"] == "roll_out"


def test_decision_confidence_profit_close():
    r = decide_position(_item(profit_pct=60.0, buyback_ask=1.0), 15, 50)
    assert r["action_code"] == "CLOSE"
    assert r["decision_confidence"] >= 80


def test_cc_shallow_div_early_assign():
    """浅 ITM CC + 除息窗口:也要标 early_assign"""
    r = decide_position(
        _item(
            side="CALL", strike=100, spot=100.8, itm=True, delta=0.48,
            profit_pct=-5.0, buyback_ask=2.0, days_to_ex_div=5,
        ),
        15, 50,
    )
    assert r["shallow_itm"]
    assert r["early_assign_risk"]
    assert r["action_code"] == "ROLL_ADJUST"


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
