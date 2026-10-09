"""Roll 场景跟持仓树:接货成功路径不调 strike,年化分母同一套,非主腿 CC 有卡片."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.core.wheel_decision import decide_position
from app.core.wheel_decision_lib import capital_employed, remaining_annualized
from app.core.wheel_roll import build_decision_cards, decide_roll_scenario, resolve_roll_leg
from app.data import database as db
from app.data import wheel_repository as repo


def _roll(**kw):
    base = dict(
        side="PUT",
        dte=20,
        profit_pct=-20.0,
        itm=True,
        # 调用方旧旗标故意与树相反,动作必须跟树
        deep_itm=False,
        delta=0.40,
        remaining_ann=99.0,
        min_annualized=15,
        profit_target=50,
        strike=100,
        spot=96,
        floor_price=110,
        stance="acquire",
        open_price=2.0,
        buyback_ask=4.0,
    )
    base.update(kw)
    return decide_roll_scenario(**base)


def _same_item(**kw):
    """与 _roll 同一组字段,直接喂持仓树."""
    fields = dict(
        side="PUT", strike=100, spot=96, dte=20, itm=True, delta=0.40,
        profit_pct=-20.0, open_price=2.0, buyback_ask=4.0, current_price=4.0,
        floor_price=110, stance="acquire",
    )
    fields.update(kw)
    return fields


def test_deep_itm_put_inside_floor_does_not_adjust():
    """深 ITM、允许接货、strike 未超愿接 → 准备接货,不推荐调 strike."""
    s = _roll()
    pos = decide_position(_same_item(), 15, 50)
    assert pos["deep_itm"] and pos["action_code"] == "PREPARE_ASSIGN"
    assert s["position_action"] == "PREPARE_ASSIGN"
    assert s["prefer_card"] != "adjust_strike"
    assert s["recommended_action"] != "roll_adjust"
    assert s["prefer_card"] == "no_roll"
    cards = build_decision_cards(
        [], side="PUT", cur_strike=100, buyback_ask=4, size=100, open_price=2,
        scenario=s, allow_down_strike=False,
    )
    assert cards["highlighted"] != "adjust_strike"
    assert set(cards["cards"]) == {"roll_out", "adjust_strike", "no_roll"}


def test_premium_only_deep_itm_offers_strike_adjust():
    s = _roll(stance="income")
    assert s["position_action"] == "ROLL_ADJUST"
    assert s["recommended_action"] == "roll_adjust"
    assert s["prefer_card"] == "adjust_strike"
    rent = _roll(stance="只收租", floor_price=110)
    assert rent["position_action"] == "ROLL_ADJUST"
    assert rent["prefer_card"] == "adjust_strike"


def test_strike_above_floor_offers_strike_adjust():
    s = _roll(floor_price=90, stance="acquire")
    pos = decide_position(_same_item(floor_price=90, stance="acquire"), 15, 50)
    assert pos["strike_above_floor"] and pos["action_code"] == "ROLL_ADJUST"
    assert s["position_action"] == "ROLL_ADJUST"
    assert s["recommended_action"] == "roll_adjust"
    assert s["prefer_card"] == "adjust_strike"


def test_income_expiring_itm_put_rolls_not_assign():
    """未深 ITM 的临期 ITM:允许接货仍是接货;只收租才调 strike."""
    common = dict(
        delta=0.30, spot=99, strike=100, floor_price=110, dte=5,
        deep_itm=True,  # 旧逻辑会因临期 ITM 强推调 strike,树不会
    )
    acquire = _roll(stance="acquire", **common)
    assert acquire["position_action"] == "PREPARE_ASSIGN"
    assert acquire["prefer_card"] != "adjust_strike"
    income = _roll(stance="income", **common)
    assert income["position_action"] == "ROLL_ADJUST"
    assert income["prefer_card"] == "adjust_strike"


def test_call_remaining_ann_matches_capital_employed():
    """备兑剩余年化分母是持股成本/现价,与持仓树 capital_employed 相同."""
    fields = dict(
        side="CALL", strike=200, spot=100, cost_basis=90, dte=30,
        buyback_ask=2.0, current_price=2.0, open_price=3.0,
        profit_pct=20.0, itm=False, delta=0.2,
    )
    s = decide_roll_scenario(
        side="CALL", dte=30, profit_pct=20.0, itm=False, deep_itm=False, delta=0.2,
        remaining_ann=12.17, min_annualized=15, profit_target=50,
        strike=200, spot=100, cost_basis=90, buyback_ask=2.0, open_price=3.0,
    )
    pos = decide_position(fields, 15, 50)
    cap = capital_employed(side="CALL", strike=200, spot=100, cost_basis=90)
    assert cap == 90
    assert pos["decision_tree"]["capital_employed"] == 90
    assert s["capital_employed"] == pos["decision_tree"]["capital_employed"]
    expect = remaining_annualized(2.0, cap, 30)
    assert expect == pos["remaining_annualized"]
    assert s["remaining_annualized"] == pos["remaining_annualized"]
    assert abs(s["remaining_annualized"] - 27.04) < 0.2
    # 旧 Roll 用 strike 200 会得到约 12.17
    assert abs(s["remaining_annualized"] - 12.17) > 1


def test_call_roll_uses_spot_when_cost_missing():
    s = decide_roll_scenario(
        side="CALL", dte=30, profit_pct=10.0, itm=False, deep_itm=False, delta=0.2,
        remaining_ann=None, min_annualized=15, profit_target=50,
        strike=200, spot=100, buyback_ask=2.0, open_price=3.0,
    )
    pos = decide_position(
        dict(
            side="CALL", strike=200, spot=100, dte=30, buyback_ask=2.0,
            current_price=2.0, open_price=3.0, profit_pct=10.0, itm=False, delta=0.2,
        ),
        15, 50,
    )
    cap = capital_employed(side="CALL", strike=200, spot=100, cost_basis=None)
    assert cap == 100
    assert s["capital_employed"] == cap == pos["decision_tree"]["capital_employed"]
    assert s["remaining_annualized"] == pos["remaining_annualized"]
    assert s["remaining_annualized"] == remaining_annualized(2.0, 100, 30)


def _two_call_cycle():
    s = repo._new_state()
    s["symbol"] = "XYZ"
    trades = [
        dict(trade_type="BUY_SHARES", qty=200, price=100, fee=0, contract_size=100,
             traded_at="2026-08-01T10:00:00"),
        dict(trade_type="SELL_CALL", qty=1, price=2.0, fee=0, contract_size=100,
             strike=145, expiry="2027-01-15", contract_code="C1",
             traded_at="2026-09-01T12:00:00"),
        dict(trade_type="SELL_CALL", qty=1, price=3.0, fee=0, contract_size=100,
             strike=150, expiry="2027-03-19", contract_code="C2",
             traded_at="2026-09-01T13:00:00"),
    ]
    for t in trades:
        repo._apply(s, t)
    return s


def test_second_cc_leg_resolves_and_gets_roll_cards():
    cycle = _two_call_cycle()
    assert cycle["open_contract_code"] == "C1"
    assert cycle["open_strike"] == 145
    second = resolve_roll_leg(cycle, "C2")
    assert second["open_contract_code"] == "C2"
    assert second["open_strike"] == 150
    assert resolve_roll_leg(cycle, "US.C2")["open_contract_code"] == "C2"
    assert resolve_roll_leg(cycle, None)["open_contract_code"] == "C1"
    scenario = decide_roll_scenario(
        side="CALL", dte=second.get("open_dte") or 40, profit_pct=-10.0,
        itm=False, deep_itm=False, delta=0.25, remaining_ann=None,
        min_annualized=15, profit_target=50,
        strike=float(second["open_strike"]), spot=140,
        cost_basis=cycle.get("cost_basis"), share_cost=cycle.get("share_cost"),
        open_price=float(second["open_price"]), buyback_ask=1.5,
    )
    decision = build_decision_cards(
        [], side="CALL", cur_strike=float(second["open_strike"]),
        buyback_ask=1.5, size=100, open_price=float(second["open_price"]),
        scenario=scenario, allow_down_strike=False,
    )
    assert set(decision["cards"]) == {"roll_out", "adjust_strike", "no_roll"}
    assert decision["cards"]["no_roll"]["available"] is True
    assert decision["cards"]["no_roll"]["key"] == "no_roll"


@pytest.fixture
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "wheel.db")
    db.init_db()
    return tmp_path


def test_roll_options_emits_card_for_second_cc_leg(books, monkeypatch):
    """/roll-options 用 close_contract_code 给非主腿同一套三卡片,而不是主腿."""
    from app.core import dividends, earnings, opend
    monkeypatch.setattr(opend, "is_opend_alive", lambda *a, **k: False)
    monkeypatch.setattr(earnings, "get_next_earnings", lambda symbol: None)
    monkeypatch.setattr(dividends, "get_next_dividend", lambda symbol: None)

    first = repo.record_trade(
        "XYZ", "BUY_SHARES", qty=200, price=100, traded_at="2026-08-01T10:00:00",
    )
    cid = first["id"]
    repo.record_trade(
        "XYZ", "SELL_CALL", contract_code="C1", strike=145, expiry="2027-01-15",
        price=2, qty=1, cycle_id=cid, traded_at="2026-09-01T12:00:00",
    )
    repo.record_trade(
        "XYZ", "SELL_CALL", contract_code="C2", strike=150, expiry="2027-03-19",
        price=3, qty=1, cycle_id=cid, traded_at="2026-09-01T13:00:00",
    )
    stored = repo.get_cycle(cid)
    assert stored["open_contract_code"] == "C1"

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.wheel import router

    app = FastAPI()
    app.include_router(router, prefix="/api/wheel")
    client = TestClient(app)

    second = client.get("/api/wheel/roll-options", params={
        "cycle_id": cid, "close_contract_code": "C2",
    })
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["current"]["contract_code"].removeprefix("US.") == "C2"
    assert body["current"]["strike"] == 150
    assert set(body["cards"]) == {"roll_out", "adjust_strike", "no_roll"}
    assert body["cards"]["no_roll"]["available"] is True

    primary = client.get("/api/wheel/roll-options", params={"cycle_id": cid})
    assert primary.status_code == 200, primary.text
    p = primary.json()
    assert p["current"]["contract_code"].removeprefix("US.") == "C1"
    assert p["current"]["strike"] == 145
    assert set(p["cards"]) == set(body["cards"])
