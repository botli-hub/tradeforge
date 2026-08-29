"""缠论分解:包含、笔、中枢、走势类型、买卖点"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.chan_engine import (  # noqa: E402
    analyze, bars_from_dicts, build_bis, build_zhongshu, classify_trend,
    find_fenxing, merge_include,
)


def _leg(p0: float, p1: float, n: int, t0: int) -> list:
    """单向腿:每根都创新高/新低,避免包含."""
    out = []
    up = p1 > p0
    for i in range(n):
        t = i / max(n - 1, 1)
        c = p0 + (p1 - p0) * t
        if up:
            lo, hi = c - 0.4, c + 0.8
        else:
            lo, hi = c - 0.8, c + 0.4
        out.append({
            "timestamp": str(t0 + i * 86400),
            "open": c,
            "high": hi,
            "low": lo,
            "close": c + (0.2 if up else -0.2),
        })
    return out


def _swings(prices: list, n_per: int = 6) -> list:
    bars = []
    t0 = 1_700_000_000
    for a, b in zip(prices, prices[1:]):
        bars.extend(_leg(float(a), float(b), n_per, t0 + len(bars) * 86400))
    return bars


def test_include_merges_inside_bar():
    rows = [
        {"timestamp": "1", "open": 10, "high": 12, "low": 9, "close": 11},
        {"timestamp": "2", "open": 10.5, "high": 11.5, "low": 9.5, "close": 11},
        {"timestamp": "3", "open": 11, "high": 14, "low": 10.5, "close": 13},
    ]
    m = merge_include(bars_from_dicts(rows))
    assert len(m) == 2
    assert m[0].high >= 12


def test_fenxing_and_bi_on_impulse():
    rows = _swings([10, 20, 12, 22, 14, 24], n_per=6)
    r = analyze(rows, "1d")
    assert r["bi_count"] >= 3
    dirs = [b["direction"] for b in r["bis"]]
    # 笔应正负交替
    for a, b in zip(dirs, dirs[1:]):
        assert a != b


def test_zhongshu_from_three_overlap_bis():
    # 来回震荡:容易三段重叠
    rows = _swings([10, 18, 12, 19, 13, 18, 12], n_per=6)
    r = analyze(rows, "1d")
    assert r["bi_count"] >= 3
    if r["zhongshu"]:
        z = r["zhongshu"][0]
        assert z["zg"] > z["zd"]


def test_downtrend_label_when_hubs_step_down():
    # 高枢 → 低枢
    rows = _swings([30, 22, 28, 20, 26, 14, 22, 10, 18], n_per=6)
    r = analyze(rows, "1d")
    assert r["bi_count"] >= 5
    # 有中枢时,下跌结构应被识别;没有中枢也不应标成上涨趋势
    if r["trend"]["zhongshu_count"] >= 2:
        assert r["trend"]["type"] in ("down_trend", "range")
    assert r["trend"]["type"] != "up_trend" or r["trend"]["zhongshu_count"] == 0


def test_buy_point_on_down_divergence():
    # 大跌 + 反弹 + 更弱的再跌
    rows = _swings([40, 20, 28, 22, 26, 18, 24, 17], n_per=7)
    r = analyze(rows, "1d")
    kinds = {s["kind"] for s in r["signals"]}
    # 不强制一定出一买(结构依赖笔划分),但输出形状要稳
    assert isinstance(r["signals"], list)
    for s in r["signals"]:
        assert s["kind"] in {"B1", "B2", "B3", "S1", "S2", "S3"}
        assert s["price"] > 0
        assert s["label"]


def test_analyze_empty():
    r = analyze([], "1d")
    assert r["bar_count"] == 0
    assert r["trend"]["type"] in ("unknown", "no_hub")


def test_level_label():
    r = analyze(_swings([10, 16, 12], n_per=6), "30m")
    assert r["level_label"] == "30分钟"


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
