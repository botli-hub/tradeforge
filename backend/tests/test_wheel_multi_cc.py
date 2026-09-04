"""一轮多笔 Covered Call:登记/覆盖/平一留一/成本基础/体检展开."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_cc_legs import (  # noqa: E402
    CcLegError,
    apply_cc_close,
    apply_sell_call,
    covered_shares,
    expand_open_option_rows,
    open_cc_legs_from_trades,
    uncovered_shares_of,
)
from app.data.wheel_repository import WheelError, _apply, _enrich_cycle, _new_state  # noqa: E402


def _t(trade_type, **kw):
    row = {
        "trade_type": trade_type,
        "qty": kw.pop("qty", 1),
        "price": kw.pop("price", 0),
        "fee": kw.pop("fee", 0),
        "contract_size": kw.pop("contract_size", 100),
        "traded_at": kw.pop("traded_at", "2026-09-01T10:00:00"),
    }
    row.update(kw)
    return row


def _replay(trades):
    s = _new_state()
    for t in trades:
        _apply(s, t)
    return s


def _holding_200():
    return _replay([
        _t("BUY_SHARES", qty=200, price=100, traded_at="2026-08-01T10:00:00"),
    ])


def test_cc_open_second_distinct_sell_call_ok_when_shares_cover_two():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="US.XYZ260911C00145000", traded_at="2026-09-01T12:00:00"))
    assert s["status"] == "CC_OPEN"
    _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10,
                 contract_code="US.XYZ261009C00150000", traded_at="2026-09-01T13:00:00"))
    assert s["status"] == "CC_OPEN"
    assert len(s["open_cc_legs"]) == 2
    strikes = sorted(leg["strike"] for leg in s["open_cc_legs"])
    assert strikes == [145, 150]
    assert covered_shares(s["open_cc_legs"]) == 200
    # 主腿摘要=最近到期(145C 9/11)
    assert s["open_strike"] == 145
    assert str(s["open_expiry"]).startswith("2026-09-11")


def test_second_sell_call_rejected_when_shares_only_cover_one():
    s = _replay([_t("BUY_SHARES", qty=100, price=100)])
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="A"))
    try:
        _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.0,
                     contract_code="B"))
        raise AssertionError("expected WheelError")
    except WheelError as e:
        msg = str(e)
        assert "超过持股" in msg or "覆盖" in msg
    assert s["status"] == "CC_OPEN"
    assert len(s["open_cc_legs"]) == 1


def test_duplicate_same_contract_rejected():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="US.XYZ260911C00145000"))
    try:
        _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.50,
                     contract_code="US.XYZ260911C00145000"))
        raise AssertionError("expected WheelError")
    except WheelError as e:
        assert "已在本轮在场" in str(e)
    assert len(s["open_cc_legs"]) == 1


def test_close_one_leg_leaves_other_open_and_cc_open():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="CC145", traded_at="2026-09-01T12:00:00"))
    _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10,
                 contract_code="CC150", traded_at="2026-09-01T13:00:00"))
    _apply(s, _t("BUY_CALL_CLOSE", strike=145, expiry="2026-09-11", price=1.00,
                 contract_code="CC145", traded_at="2026-09-02T10:00:00"))
    assert s["status"] == "CC_OPEN"
    assert len(s["open_cc_legs"]) == 1
    assert s["open_cc_legs"][0]["strike"] == 150
    assert s["open_strike"] == 150
    assert s["shares"] == 200


def test_expire_one_leg_keeps_other():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="CC145"))
    _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10,
                 contract_code="CC150"))
    _apply(s, _t("EXPIRE", strike=145, expiry="2026-09-11", contract_code="CC145"))
    assert s["status"] == "CC_OPEN"
    assert [leg["strike"] for leg in s["open_cc_legs"]] == [150]


def test_cost_basis_reduced_by_both_premiums():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="CC145", fee=0))
    _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10,
                 contract_code="CC150", fee=0))
    # 2.33*100 + 3.10*100 = 543; CB = 100 - 543/200 = 97.285
    assert abs(s["total_premium"] - 543.0) < 1e-6
    c = _enrich_cycle({
        **s, "started_at": "2026-08-01T10:00:00", "id": "x", "symbol": "XYZ",
    })
    assert c["cost_basis"] is not None
    assert abs(c["cost_basis"] - (100 - 543 / 200)) < 1e-4


def test_open_positions_helper_returns_both_legs():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="CC145"))
    _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10,
                 contract_code="CC150"))
    cycle = _enrich_cycle({
        **s, "id": "cyc-1", "symbol": "XYZ", "started_at": "2026-08-01T10:00:00",
    })
    rows = expand_open_option_rows([cycle])
    assert len(rows) == 2
    codes = {r["open_contract_code"] for r in rows}
    assert codes == {"CC145", "CC150"}
    assert all(r["status"] == "CC_OPEN" for r in rows)
    assert all(r["id"] == "cyc-1" for r in rows)


def test_existing_single_leg_cycle_still_works():
    s = _replay([
        _t("BUY_SHARES", qty=100, price=95),
        _t("SELL_CALL", strike=100, expiry="2026-09-18", price=1.5, contract_code="ONLY"),
    ])
    assert s["status"] == "CC_OPEN"
    assert len(s["open_cc_legs"]) == 1
    assert s["open_contract_code"] == "ONLY"
    _apply(s, _t("BUY_CALL_CLOSE", price=0.4, contract_code="ONLY"))
    assert s["status"] == "HOLDING"
    assert s["open_cc_legs"] == []
    assert s["open_contract_code"] is None
    # 权利金净额 150-40=110; CB = 95 - 110/100 = 93.9
    c = _enrich_cycle({**s, "started_at": "2026-08-01T10:00:00", "id": "s", "symbol": "AAA"})
    assert abs(c["cost_basis"] - 93.9) < 1e-4


def test_called_away_one_leg_does_not_wipe_other():
    s = _holding_200()
    _apply(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33,
                 contract_code="CC145"))
    _apply(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10,
                 contract_code="CC150"))
    _apply(s, _t("CALLED_AWAY", strike=145, qty=1, contract_code="CC145"))
    assert s["status"] == "CC_OPEN"
    assert s["shares"] == 100
    assert len(s["open_cc_legs"]) == 1
    assert s["open_cc_legs"][0]["contract_code"] == "CC150"


def test_legacy_open_contract_fields_still_expand():
    cycle = {
        "id": "old",
        "symbol": "AAA",
        "status": "CC_OPEN",
        "shares": 100,
        "open_contract_code": "OLD1",
        "open_option_type": "CALL",
        "open_strike": 120,
        "open_expiry": "2026-09-25",
        "open_qty": 1,
        "open_price": 1.2,
        "open_contract_size": 100,
    }
    rows = expand_open_option_rows([cycle])
    assert len(rows) == 1
    assert rows[0]["open_contract_code"] == "OLD1"
    assert uncovered_shares_of(cycle) == 0


def test_ledger_trades_are_source_of_truth():
    trades = [
        _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2.33, contract_code="CC145"),
        _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3.10, contract_code="CC150"),
        _t("BUY_CALL_CLOSE", strike=145, expiry="2026-09-11", price=1.0, contract_code="CC145"),
    ]
    legs = open_cc_legs_from_trades(trades)
    assert len(legs) == 1
    assert legs[0]["strike"] == 150


def test_apply_helpers_reject_over_cover_without_repository():
    s = {
        "status": "HOLDING", "shares": 100, "share_cost": 100,
        "total_premium": 0.0, "total_fees": 0.0, "open_cc_legs": [],
    }
    apply_sell_call(s, _t("SELL_CALL", strike=145, expiry="2026-09-11", price=2))
    try:
        apply_sell_call(s, _t("SELL_CALL", strike=150, expiry="2026-10-09", price=3))
        raise AssertionError("expected CcLegError")
    except CcLegError:
        pass


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
