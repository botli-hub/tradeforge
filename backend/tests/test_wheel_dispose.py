"""过线分叉 / 台账权利金 / CSP 机会成本"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_decision import decide_position  # noqa: E402
from app.core.wheel_dispose import (  # noqa: E402
    csp_opportunity_cost,
    item_must_manage,
)
from app.core.premium_ledger import (  # noqa: E402
    open_premium_from_trades,
    resolve_premium,
)


def _item(**kw):
    base = dict(
        side="PUT", strike=100.0, spot=110.0, dte=35, current_price=1.0,
        buyback_ask=1.0, open_price=3.0, profit_pct=None, itm=False,
        delta=0.2, expiring=False, qty=1, contract_size=100,
    )
    base.update(kw)
    return base


def test_acquire_hold_theta_past_line_forces_must_manage():
    """过现有止盈线:acquire + HOLD_THETA 不能藏,必须处理,续拿=方向赌注."""
    r = decide_position(
        _item(
            dte=5, profit_pct=55.0, current_price=0.4, buyback_ask=0.4,
            expiring=True, stance="acquire",
        ),
        15, 50,
    )
    assert r["action_code"] == "HOLD_THETA"
    assert r["decision_tree"]["hold_for_theta"]
    assert r["decision_tree"]["profit_hit"]
    assert r["must_manage"] is True
    assert r["action_priority"] <= 3
    assert item_must_manage(r)
    fork = r["dispose_fork"]
    assert fork is not None
    assert fork["bag"]["label"] == "落袋"
    assert fork["hold"]["directional_bet"] is True
    copy = fork["hold"]["copy"]
    assert "方向赌注" in copy
    assert "不是默认" in copy
    assert "吃θ" in copy
    blob = " ".join(r.get("reasons") or []) + (r.get("secondary_hint") or "")
    assert "方向赌注" in blob


def test_hold_theta_cannot_hide_from_today_must():
    item = decide_position(
        _item(
            dte=5, profit_pct=55.0, current_price=0.4, buyback_ask=0.4,
            expiring=True, stance="acquire",
        ),
        15, 50,
    )
    assert item["action_code"] == "HOLD_THETA"
    assert item_must_manage(item)
    must = [i for i in [item] if item_must_manage(i) or (i.get("action_priority") or 9) <= 3]
    assert must and must[0]["dispose_fork"]


def test_hold_theta_below_line_not_forced():
    r = decide_position(
        _item(
            dte=5, profit_pct=45.0, current_price=0.4, buyback_ask=0.4,
            expiring=True, stance="acquire",
        ),
        15, 50,
    )
    assert r["action_code"] == "HOLD_THETA"
    assert not r.get("must_manage")
    assert not r.get("dispose_fork")
    assert not item_must_manage(r)


def test_missing_ledger_premium_does_not_claim_take_profit():
    r = decide_position(
        _item(
            cycle_id="cyc-uncal",
            ledger_trades=[],
            profit_pct=60.0,
            open_price=3.0,
            buyback_ask=1.6,
            current_price=1.6,
            dte=35,
        ),
        15, 50,
    )
    prem = r.get("premium") or {}
    assert prem.get("calibrated") is False
    assert r.get("premium_uncalibrated") is True
    assert not (r.get("decision_tree") or {}).get("profit_hit")
    hint = r.get("action_hint") or ""
    reasons = " ".join(r.get("reasons") or [])
    branch = r.get("decision_branch") or ""
    blob = hint + reasons + branch
    assert "已止盈" not in blob
    assert "过线" not in blob
    assert r["action_code"] != "CLOSE"
    assert "未校准" in reasons or "未校准" in hint
    assert not r.get("must_manage")


def test_ledger_fills_calibrate_and_can_claim():
    trades = [{
        "trade_type": "SELL_PUT", "price": 3.0, "qty": 1, "contract_size": 100,
        "traded_at": "2026-08-01T10:00:00",
    }]
    r = decide_position(
        _item(
            cycle_id="cyc-ok",
            ledger_trades=trades,
            profit_pct=None,
            open_price=0,
            buyback_ask=1.0,
            current_price=1.0,
            dte=35,
        ),
        15, 50,
    )
    assert r["premium"]["calibrated"] is True
    assert r["premium"]["source"] == "ledger"
    assert r["action_code"] == "CLOSE"
    assert "止盈" in (r["action_hint"] or "")
    assert r["must_manage"] is True


def test_csp_opportunity_cost_computes_and_does_not_crash():
    r = decide_position(
        _item(profit_pct=60.0, buyback_ask=1.0, side="PUT", strike=100, qty=1),
        15, 50,
    )
    oc = r.get("csp_opportunity_cost")
    assert oc is not None
    assert oc.get("aid_only") is True
    assert oc.get("collateral_released") == 10000.0
    assert oc.get("reopen") is not None
    assert "不自动下单" in (oc.get("note") or "")

    messy = csp_opportunity_cost({"side": "PUT"}, {})
    assert messy is None or messy.get("aid_only") is True

    none_call = csp_opportunity_cost({"side": "CALL", "strike": 100}, {})
    assert none_call is None

    broken = csp_opportunity_cost({"side": "PUT", "strike": "x", "qty": None}, {"quant_thresholds": {}})
    assert broken is None or broken.get("aid_only") is True


def test_open_premium_vwap_and_reset_on_close():
    trades = [
        {"trade_type": "SELL_PUT", "price": 2.0, "qty": 1, "traded_at": "2026-08-01"},
        {"trade_type": "SELL_PUT", "price": 4.0, "qty": 1, "traded_at": "2026-08-02"},
    ]
    p = open_premium_from_trades(trades, side="PUT")
    assert p is not None
    assert abs(p["open_price"] - 3.0) < 1e-6
    trades2 = trades + [
        {"trade_type": "BUY_PUT_CLOSE", "price": 1.0, "qty": 1, "traded_at": "2026-08-03"},
    ]
    assert open_premium_from_trades(trades2, side="PUT") is None


def test_resolve_premium_synthetic_without_cycle():
    prem = resolve_premium({"open_price": 3.0, "side": "PUT"})
    assert prem["calibrated"] is True
    assert prem["source"] == "item"


def test_profit_target_field_unchanged():
    r = decide_position(_item(profit_pct=60.0), 15, 50)
    q = r.get("quant_thresholds") or {}
    assert q.get("profit_target_pct") == 50
    fork = r.get("dispose_fork") or {}
    assert fork.get("profit_target_pct") == 50


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
