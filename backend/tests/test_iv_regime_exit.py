"""IV 环境档 + 止盈组合年化统计"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_iv_regime import (  # noqa: E402
    classify_regime,
    DEFAULT_REGIME_CFG,
    median_ivr,
)
from app.core.wheel_attribution import (  # noqa: E402
    _pair_open_close,
    exit_efficiency_stats,
    open_missed_50_count,
)


def test_classify_hysteresis():
    rc = DEFAULT_REGIME_CFG
    assert classify_regime(30, "mid", rc) == "low"
    assert classify_regime(38, "low", rc) == "low"  # 未过 low_exit 40
    assert classify_regime(42, "low", rc) == "mid"
    assert classify_regime(65, "mid", rc) == "high"
    assert classify_regime(57, "high", rc) == "high"  # 未破 high_exit 55
    assert classify_regime(50, "high", rc) == "mid"
    assert classify_regime(None, "high", rc) == "high"


def test_median_ivr():
    med, n = median_ivr([
        {"iv_rank": 20}, {"iv_rank": 60}, {"iv_rank": None}, {"iv_rank": 40},
    ])
    assert n == 3
    assert med == 40.0


def test_pair_open_close():
    trades = [
        {"cycle_id": "c1", "symbol": "A", "trade_type": "SELL_PUT", "price": 2.0,
         "qty": 1, "contract_size": 100, "strike": 100, "fee": 1,
         "traded_at": "2026-01-01T10:00:00"},
        {"cycle_id": "c1", "symbol": "A", "trade_type": "BUY_PUT_CLOSE", "price": 0.8,
         "qty": 1, "contract_size": 100, "strike": 100, "fee": 1,
         "traded_at": "2026-01-10T10:00:00"},
    ]
    legs = _pair_open_close(trades)
    assert len(legs) == 1
    assert legs[0]["profit_pct"] == 60.0  # (2-0.8)/2
    assert legs[0]["exit_bucket"] == "ge50"
    assert legs[0]["net_premium"] == 118.0  # 120 - 2 fee
    assert legs[0]["days"] == 9
    assert legs[0]["annualized_on_capital"] is not None


def test_open_missed_50():
    r = open_missed_50_count([
        {"symbol": "X", "profit_pct": 55, "side": "PUT"},
        {"symbol": "Y", "profit_pct": 20, "side": "CALL"},
    ])
    assert r["n"] == 1
    assert r["items"][0]["symbol"] == "X"


def test_exit_stats_empty_ok():
    # 可能有真实库数据;只断言结构
    r = exit_efficiency_stats()
    assert "n_legs" in r
    assert "buckets" in r or r["n_legs"] == 0
    assert r.get("focus") == "portfolio_annualized" or r["n_legs"] == 0
