"""触线择优:同标的多合约只推/进 Sim 最优(年化→theta→权利金→近DTE→strike)。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.touch_best import (  # noqa: E402
    annualized_of,
    pick_best,
    rank_tuple,
    select_best_touch_signals,
    signal_side,
    theta_abs_of,
)


def _sig(**kw):
    base = {
        "symbol": "AAPL",
        "side": "PUT",
        "signal_level": "WHEEL_PUT",
        "contract_code": "US.AAPL260320P00100000",
        "strike": 100.0,
        "dte": 30,
        "bid": 2.0,
        "annualized": 20.0,
        "theta": 0.05,
    }
    base.update(kw)
    return base


def test_signal_side_from_level():
    assert signal_side({"signal_level": "WHEEL_CALL"}) == "CALL"
    assert signal_side(SimpleNamespace(signal_level="WHEEL_PUT", side=None)) == "PUT"


def test_multi_contract_same_symbol_one_winner():
    a = _sig(contract_code="A", annualized=18, theta=0.10, bid=1.5, strike=95)
    b = _sig(contract_code="B", annualized=25, theta=0.01, bid=1.0, strike=100)
    c = _sig(contract_code="C", annualized=25, theta=0.08, bid=1.2, strike=105)
    winners = select_best_touch_signals([a, b, c])
    assert len(winners) == 1
    # 同年金化 25: theta 更高的 C 胜 B
    assert winners[0]["contract_code"] == "C"


def test_annualized_primary_over_theta():
    low_ann = _sig(contract_code="LOW", annualized=10, theta=0.99)
    high_ann = _sig(contract_code="HIGH", annualized=40, theta=0.01)
    assert pick_best([low_ann, high_ann])["contract_code"] == "HIGH"


def test_theta_missing_treated_as_zero():
    with_th = _sig(contract_code="T", annualized=20, theta=0.05)
    no_th = _sig(contract_code="N", annualized=20)
    no_th.pop("theta", None)
    assert theta_abs_of(no_th) == 0.0
    assert pick_best([with_th, no_th])["contract_code"] == "T"


def test_put_and_call_both_kept():
    put = _sig(side="PUT", signal_level="WHEEL_PUT", contract_code="P1", annualized=15)
    call = _sig(side="CALL", signal_level="WHEEL_CALL", contract_code="C1", annualized=12)
    winners = select_best_touch_signals([put, call])
    codes = {w["contract_code"] for w in winners}
    assert codes == {"P1", "C1"}


def test_different_symbols_independent():
    a1 = _sig(symbol="AAPL", contract_code="A1", annualized=10)
    a2 = _sig(symbol="AAPL", contract_code="A2", annualized=30)
    m1 = _sig(symbol="MSFT", contract_code="M1", annualized=5)
    winners = select_best_touch_signals([a1, a2, m1])
    codes = {w["contract_code"] for w in winners}
    assert codes == {"A2", "M1"}


def test_tiebreak_premium_then_dte_then_strike():
    # same ann + theta
    x = _sig(contract_code="X", annualized=20, theta=0.1, bid=1.0, dte=40, strike=90)
    y = _sig(contract_code="Y", annualized=20, theta=0.1, bid=2.0, dte=40, strike=90)
    assert pick_best([x, y])["contract_code"] == "Y"
    y2 = _sig(contract_code="Y2", annualized=20, theta=0.1, bid=2.0, dte=10, strike=90)
    y3 = _sig(contract_code="Y3", annualized=20, theta=0.1, bid=2.0, dte=10, strike=100)
    assert pick_best([y2, y3])["contract_code"] == "Y3"  # nearer DTE tie → higher strike


def test_dataclass_like_namespace():
    sig = SimpleNamespace(
        symbol="NVDA",
        signal_level="WHEEL_PUT",
        side=None,
        annualized=22.5,
        theta=-0.12,
        bid=3.0,
        dte=21,
        strike=120,
        contract_code="N1",
    )
    assert annualized_of(sig) == 22.5
    assert theta_abs_of(sig) == 0.12
    assert rank_tuple(sig)[0] == 22.5


def test_call_1h_and_1d_same_symbol_one_winner():
    h = _sig(
        side="CALL", signal_level="WHEEL_CALL", timeframe="1h",
        contract_code="H", annualized=18, theta=0.02,
    )
    d = _sig(
        side="CALL", signal_level="WHEEL_CALL", timeframe="1d",
        contract_code="D", annualized=28, theta=0.01,
    )
    winners = select_best_touch_signals([h, d])
    assert len(winners) == 1
    assert winners[0]["contract_code"] == "D"
