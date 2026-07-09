"""wheel_score 综合打分单元测试(纯函数,不依赖数据库/Futu)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_score import (  # noqa: E402
    DEFAULT_SCAN_CFG, get_scan_cfg, spread_pct, liquidity_factor,
    trend_factor, is_iv_high, score_contract,
)

CFG = dict(DEFAULT_SCAN_CFG)


def test_spread_pct():
    assert spread_pct(1.0, 1.0) == 0.0
    assert spread_pct(0.95, 1.05) == 10.0
    assert spread_pct(0, 1.0) is None       # 无 bid
    assert spread_pct(1.0, None) is None    # 无 ask
    assert spread_pct(1.1, 1.0) is None     # ask < bid 异常报价


def test_liquidity_factor():
    assert liquidity_factor(2.0, CFG) == 1.0            # ≤ soft 不惩罚
    assert liquidity_factor(4.0, CFG) == 1.0
    assert liquidity_factor(10.0, CFG) == 0.7           # max 边界
    assert liquidity_factor(10.1, CFG) is None          # 超限过滤
    mid = liquidity_factor(7.0, CFG)
    assert mid is not None and 0.7 < mid < 1.0          # 线性区间
    assert liquidity_factor(None, CFG) == 0.7           # 单边报价按最差


def test_trend_factor():
    up = {"trend": "UP"}
    weak = {"trend": "WEAK"}
    down = {"trend": "DOWN"}
    assert trend_factor("PUT", up, CFG) == 1.0
    assert trend_factor("PUT", weak, CFG) == CFG["trend_penalty_below_ema50"]
    assert trend_factor("PUT", down, CFG) == CFG["trend_penalty_below_ema200"]
    assert trend_factor("CALL", down, CFG) == 1.0       # 卖 Call 不惩罚
    assert trend_factor("PUT", None, CFG) == 1.0        # 无数据不惩罚


def test_is_iv_high():
    assert is_iv_high({"iv_rank": 75}) is True
    assert is_iv_high({"iv_rank": 30, "iv_hv_ratio": 1.4}) is True
    assert is_iv_high({"iv_rank": 30, "iv_hv_ratio": 1.0}) is False
    assert is_iv_high(None) is False


def test_score_basic():
    r = score_contract(30.0, "PUT", 0.25, 2.0, False, None, {"trend": "UP"}, CFG)
    assert r is not None
    assert r["score"] == 30.0  # 无任何惩罚/加成
    assert r["factors"]["liquidity"] == 1.0


def test_score_penalties_and_bonus():
    # 财报 + 趋势弱
    r = score_contract(30.0, "PUT", 0.25, 2.0, True, None, {"trend": "DOWN"}, CFG)
    assert r is not None
    expected = 30.0 * CFG["earnings_penalty"] * CFG["trend_penalty_below_ema200"]
    assert abs(r["score"] - round(expected, 2)) < 0.01
    # IV rank 加成
    r2 = score_contract(30.0, "PUT", 0.25, 2.0, False, {"iv_rank": 50}, {"trend": "UP"}, CFG)
    assert r2 is not None
    assert abs(r2["score"] - 30.0 * (1 + CFG["iv_rank_bonus"] * 0.5)) < 0.01


def test_score_iv_high_prefers_low_delta():
    vol = {"iv_rank": 80}
    lo = score_contract(30.0, "PUT", 0.16, 2.0, False, vol, None, CFG)
    hi = score_contract(30.0, "PUT", 0.30, 2.0, False, vol, None, CFG)
    assert lo is not None and hi is not None
    assert lo["score"] > hi["score"]  # 同等年化,低 delta 排前


def test_score_spread_filter():
    assert score_contract(30.0, "PUT", 0.25, 15.0, False, None, None, CFG) is None


def test_get_scan_cfg_override():
    merged = get_scan_cfg({"wheel_scan": {"max_spread_pct": 6.0}})
    assert merged["max_spread_pct"] == 6.0
    assert merged["earnings_penalty"] == DEFAULT_SCAN_CFG["earnings_penalty"]


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
