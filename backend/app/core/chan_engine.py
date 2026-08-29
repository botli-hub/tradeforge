"""缠中说禅 · 走势几何分解.

层级: K 包含处理 → 分型 → 笔(老笔,至少 5 根合并 K) → 线段(特征序列分型)
→ 笔中枢 → 走势类型 → 一二三类买卖点(力度背驰).

这是交易员看图用的结构化分解,不是预测模型.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

TF_LABEL = {
    "1m": "1分钟", "5m": "5分钟", "15m": "15分钟", "30m": "30分钟",
    "60m": "60分钟", "1h": "60分钟", "1d": "日线", "1w": "周线", "1M": "月线",
}


@dataclass
class RawBar:
    ts: str
    open: float
    high: float
    low: float
    close: float
    idx: int = 0


@dataclass
class MergedBar:
    start_idx: int
    end_idx: int
    ts: str
    end_ts: str
    high: float
    low: float
    close: float
    direction: int = 0  # 1 up, -1 down, 0 unknown


@dataclass
class Fenxing:
    kind: str  # top | bottom
    mid_idx: int  # index in merged
    ts: str
    price: float
    high: float
    low: float


@dataclass
class Bi:
    direction: int  # 1 up, -1 down
    start_idx: int
    end_idx: int
    start_ts: str
    end_ts: str
    start_price: float
    end_price: float
    high: float
    low: float
    raw_start: int
    raw_end: int


@dataclass
class Segment:
    direction: int
    start_bi: int
    end_bi: int
    start_ts: str
    end_ts: str
    start_price: float
    end_price: float
    high: float
    low: float
    finished: bool = True


@dataclass
class ZhongShu:
    zg: float
    zd: float
    start_bi: int
    end_bi: int
    start_ts: str
    end_ts: str
    direction: str  # up | down | range
    bi_count: int


@dataclass
class Signal:
    kind: str  # B1 B2 B3 S1 S2 S3
    label: str
    ts: str
    price: float
    note: str


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def bars_from_dicts(rows: List[Dict[str, Any]]) -> List[RawBar]:
    out: List[RawBar] = []
    for i, r in enumerate(rows):
        h = _f(r.get("high"))
        l = _f(r.get("low"))
        o = _f(r.get("open"))
        c = _f(r.get("close"))
        if h <= 0 or l <= 0 or h < l:
            continue
        ts = str(r.get("timestamp") or r.get("ts") or i)
        out.append(RawBar(ts=ts, open=o, high=h, low=l, close=c, idx=len(out)))
    return out


def merge_include(raw: List[RawBar]) -> List[MergedBar]:
    """K 线包含处理. 向上取高高,向下取低低."""
    if not raw:
        return []
    merged: List[MergedBar] = [
        MergedBar(
            start_idx=raw[0].idx, end_idx=raw[0].idx, ts=raw[0].ts, end_ts=raw[0].ts,
            high=raw[0].high, low=raw[0].low, close=raw[0].close, direction=0,
        )
    ]
    direction = 0
    for b in raw[1:]:
        last = merged[-1]
        included = (
            (b.high <= last.high and b.low >= last.low)
            or (b.high >= last.high and b.low <= last.low)
        )
        if included:
            if direction >= 0:
                last.high = max(last.high, b.high)
                last.low = max(last.low, b.low)
            else:
                last.high = min(last.high, b.high)
                last.low = min(last.low, b.low)
            last.close = b.close
            last.end_idx = b.idx
            last.end_ts = b.ts
            continue
        if b.high > last.high and b.low > last.low:
            direction = 1
        elif b.high < last.high and b.low < last.low:
            direction = -1
        last.direction = direction
        merged.append(MergedBar(
            start_idx=b.idx, end_idx=b.idx, ts=b.ts, end_ts=b.ts,
            high=b.high, low=b.low, close=b.close, direction=direction,
        ))
    return merged


def find_fenxing(merged: List[MergedBar]) -> List[Fenxing]:
    out: List[Fenxing] = []
    for i in range(1, len(merged) - 1):
        l, m, r = merged[i - 1], merged[i], merged[i + 1]
        if m.high > l.high and m.high > r.high and m.low >= l.low and m.low >= r.low:
            out.append(Fenxing(
                kind="top", mid_idx=i, ts=m.end_ts, price=m.high,
                high=m.high, low=m.low,
            ))
        elif m.low < l.low and m.low < r.low and m.high <= l.high and m.high <= r.high:
            out.append(Fenxing(
                kind="bottom", mid_idx=i, ts=m.end_ts, price=m.low,
                high=m.high, low=m.low,
            ))
    return out


def build_bis(merged: List[MergedBar], fenxings: List[Fenxing], min_gap: int = 4) -> List[Bi]:
    """老笔: 分型中点至少隔 4 根合并 K(一笔不少于 5 根). 同类取更极端."""
    if not fenxings:
        return []
    picked: List[Fenxing] = []
    pending: Optional[Fenxing] = None
    for fx in fenxings:
        if pending is None:
            pending = fx
            continue
        if fx.kind == pending.kind:
            if fx.kind == "top" and fx.price >= pending.price:
                pending = fx
            elif fx.kind == "bottom" and fx.price <= pending.price:
                pending = fx
            continue
        if fx.mid_idx - pending.mid_idx >= min_gap:
            picked.append(pending)
            pending = fx
    if pending is not None:
        picked.append(pending)

    bis: List[Bi] = []
    for a, b in zip(picked, picked[1:]):
        if a.kind == b.kind:
            continue
        if b.mid_idx - a.mid_idx < min_gap:
            continue
        up = a.kind == "bottom" and b.kind == "top"
        ma, mb = merged[a.mid_idx], merged[b.mid_idx]
        if up:
            direction = 1
            sp, ep = a.price, b.price
        else:
            direction = -1
            sp, ep = a.price, b.price
        bis.append(Bi(
            direction=direction,
            start_idx=a.mid_idx,
            end_idx=b.mid_idx,
            start_ts=ma.end_ts,
            end_ts=mb.end_ts,
            start_price=sp,
            end_price=ep,
            high=max(a.high, b.high, ma.high, mb.high),
            low=min(a.low, b.low, ma.low, mb.low),
            raw_start=ma.start_idx,
            raw_end=mb.end_idx,
        ))
    return bis


def _feat_include(feats: List[Dict[str, Any]], direction: int) -> List[Dict[str, Any]]:
    if not feats:
        return []
    out = [dict(feats[0])]
    d = direction
    for f in feats[1:]:
        last = out[-1]
        inc = (
            (f["high"] <= last["high"] and f["low"] >= last["low"])
            or (f["high"] >= last["high"] and f["low"] <= last["low"])
        )
        if inc:
            if d >= 0:
                last["high"] = max(last["high"], f["high"])
                last["low"] = max(last["low"], f["low"])
            else:
                last["high"] = min(last["high"], f["high"])
                last["low"] = min(last["low"], f["low"])
            last["bi_index"] = f["bi_index"]
            continue
        if f["high"] > last["high"] and f["low"] > last["low"]:
            d = 1
        elif f["high"] < last["high"] and f["low"] < last["low"]:
            d = -1
        out.append(dict(f))
    return out


def build_segments(bis: List[Bi]) -> List[Segment]:
    """线段: 特征序列出现分型则结束. 不足 3 笔视为未完成."""
    if not bis:
        return []
    segs: List[Segment] = []
    start = 0
    while start < len(bis):
        direction = bis[start].direction
        end_bi = None
        feats: List[Dict[str, Any]] = []
        i = start + 1
        while i < len(bis):
            if bis[i].direction == direction:
                i += 1
                continue
            feats.append({"bi_index": i, "high": bis[i].high, "low": bis[i].low})
            merged_f = _feat_include(feats, -direction)
            if len(merged_f) >= 3:
                a, b, c = merged_f[-3], merged_f[-2], merged_f[-1]
                if direction > 0:
                    top = b["high"] > a["high"] and b["high"] > c["high"]
                    if top:
                        end_bi = b["bi_index"]
                        break
                else:
                    bot = b["low"] < a["low"] and b["low"] < c["low"]
                    if bot:
                        end_bi = b["bi_index"]
                        break
            i += 1
        if end_bi is None:
            end_bi = len(bis) - 1
            finished = (end_bi - start + 1) >= 3 and i >= len(bis)
            # 未走出特征分型 → 未完成
            finished = False
            segs.append(_seg_from_bis(bis, start, end_bi, direction, finished))
            break
        segs.append(_seg_from_bis(bis, start, end_bi, direction, True))
        start = end_bi
    return segs


def _seg_from_bis(bis: List[Bi], start: int, end: int, direction: int, finished: bool) -> Segment:
    chunk = bis[start:end + 1]
    return Segment(
        direction=direction,
        start_bi=start,
        end_bi=end,
        start_ts=chunk[0].start_ts,
        end_ts=chunk[-1].end_ts,
        start_price=chunk[0].start_price,
        end_price=chunk[-1].end_price,
        high=max(x.high for x in chunk),
        low=min(x.low for x in chunk),
        finished=finished,
    )


def overlap3(a: Bi, b: Bi, c: Bi) -> Optional[Tuple[float, float]]:
    zg = min(a.high, b.high, c.high)
    zd = max(a.low, b.low, c.low)
    if zg > zd:
        return zg, zd
    return None


def build_zhongshu(bis: List[Bi]) -> List[ZhongShu]:
    """笔中枢: 连续三笔重叠; ZG/ZD 取前三笔,之后重叠视为延伸."""
    hubs: List[ZhongShu] = []
    i = 0
    n = len(bis)
    while i + 2 < n:
        ov = overlap3(bis[i], bis[i + 1], bis[i + 2])
        if not ov:
            i += 1
            continue
        zg, zd = ov
        j = i + 2
        k = i + 3
        while k < n:
            bi = bis[k]
            if bi.low < zg and bi.high > zd:
                j = k
                k += 1
            else:
                break
        chunk = bis[i:j + 1]
        mid = (zg + zd) / 2
        # 方向: 中枢前后离开段
        if i > 0 and bis[i - 1].direction < 0:
            dirc = "down"
        elif i > 0 and bis[i - 1].direction > 0:
            dirc = "up"
        else:
            dirc = "range"
        hubs.append(ZhongShu(
            zg=round(zg, 4), zd=round(zd, 4),
            start_bi=i, end_bi=j,
            start_ts=chunk[0].start_ts, end_ts=chunk[-1].end_ts,
            direction=dirc,
            bi_count=j - i + 1,
        ))
        i = j + 1
    return hubs


def _ema(vals: List[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def macd_hist(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> List[float]:
    if len(closes) < slow + signal:
        return [0.0] * len(closes)
    dif = [a - b for a, b in zip(_ema(closes, fast), _ema(closes, slow))]
    dea = _ema(dif, signal)
    return [a - b for a, b in zip(dif, dea)]


def _area(hist: List[float], start: int, end: int) -> float:
    s, e = max(0, start), min(len(hist) - 1, end)
    if e < s:
        return 0.0
    return abs(sum(hist[s:e + 1]))


def _amp(bi: Bi) -> float:
    return abs(bi.end_price - bi.start_price)


def _divergent(prev: Bi, last: Bi, hist: List[float]) -> bool:
    if prev.direction != last.direction:
        return False
    weaker = _area(hist, last.raw_start, last.raw_end) < _area(hist, prev.raw_start, prev.raw_end) * 0.92
    weaker = weaker or (_amp(last) < _amp(prev) * 0.85)
    if last.direction < 0:
        return last.end_price < prev.end_price and weaker
    return last.end_price > prev.end_price and weaker


def classify_trend(hubs: List[ZhongShu], bis: List[Bi]) -> Dict[str, Any]:
    if not hubs:
        if not bis:
            return {"type": "unknown", "label": "样本不足", "zhongshu_count": 0, "summary": "K 线不足以成笔"}
        last = bis[-1]
        lab = "向上笔" if last.direction > 0 else "向下笔"
        return {
            "type": "no_hub",
            "label": f"未形成中枢 · {lab}",
            "zhongshu_count": 0,
            "summary": f"共 {len(bis)} 笔,尚无三段重叠中枢",
        }
    n = len(hubs)
    if n == 1:
        h = hubs[0]
        return {
            "type": "range",
            "label": "盘整",
            "zhongshu_count": 1,
            "summary": f"单一中枢 {h.zd:g}–{h.zg:g},走的是中枢震荡而非趋势",
        }
    ups = sum(1 for h in hubs if h.zd >= hubs[0].zd and h.zg >= hubs[0].zg)
    downs = sum(1 for h in hubs if h.zg <= hubs[0].zg and h.zd <= hubs[0].zd)
    rising = hubs[-1].zd > hubs[0].zg * 0.98 and hubs[-1].zg > hubs[0].zg
    falling = hubs[-1].zg < hubs[0].zd * 1.02 and hubs[-1].zd < hubs[0].zd
    if falling or (downs >= n - 1 and hubs[-1].zg < hubs[0].zg):
        return {
            "type": "down_trend",
            "label": "下跌趋势",
            "zhongshu_count": n,
            "summary": f"{n} 个中枢下移,后枢 {hubs[-1].zd:g}–{hubs[-1].zg:g}",
        }
    if rising or (ups >= n - 1 and hubs[-1].zd > hubs[0].zd):
        return {
            "type": "up_trend",
            "label": "上涨趋势",
            "zhongshu_count": n,
            "summary": f"{n} 个中枢上移,后枢 {hubs[-1].zd:g}–{hubs[-1].zg:g}",
        }
    return {
        "type": "range",
        "label": "盘整 / 中枢扩展",
        "zhongshu_count": n,
        "summary": f"{n} 个中枢高低互有重叠,按盘整而非单边趋势看",
    }


def find_signals(
    bis: List[Bi],
    hubs: List[ZhongShu],
    hist: List[float],
    trend: Dict[str, Any],
) -> List[Signal]:
    sigs: List[Signal] = []
    if len(bis) < 3:
        return sigs
    t = trend.get("type")

    def add(kind: str, label: str, bi: Bi, note: str) -> None:
        sigs.append(Signal(kind=kind, label=label, ts=bi.end_ts, price=bi.end_price, note=note))

    # 离开段: 中枢之后同向延续的笔
    if hubs:
        last_hub = hubs[-1]
        after = bis[last_hub.end_bi + 1:]
        # 找中枢前一段离开,与中枢后离开比较
        before = bis[:last_hub.start_bi]
        down_after = [b for b in after if b.direction < 0]
        up_after = [b for b in after if b.direction > 0]
        down_before = [b for b in before if b.direction < 0]
        up_before = [b for b in before if b.direction > 0]

        if t in ("down_trend", "range") and down_after:
            last_down = down_after[-1]
            prev_down = down_before[-1] if down_before else (down_after[-2] if len(down_after) > 1 else None)
            if prev_down and _divergent(prev_down, last_down, hist):
                add("B1", "一买", last_down, "下跌离开段力度弱于前一段(背驰)")
            elif t == "range" and last_down.end_price <= last_hub.zd and prev_down and _amp(last_down) < _amp(prev_down):
                add("B1", "类一买", last_down, "盘整下沿离开段缩短,按类一买观察")

        if t in ("up_trend", "range") and up_after:
            last_up = up_after[-1]
            prev_up = up_before[-1] if up_before else (up_after[-2] if len(up_after) > 1 else None)
            if prev_up and _divergent(prev_up, last_up, hist):
                add("S1", "一卖", last_up, "上涨离开段力度弱于前一段(背驰)")
            elif t == "range" and last_up.end_price >= last_hub.zg and prev_up and _amp(last_up) < _amp(prev_up):
                add("S1", "类一卖", last_up, "盘整上沿离开段缩短,按类一卖观察")

        # 二类: 一买后回抽不创新低 / 一卖后不创新高
        kinds = {s.kind for s in sigs}
        if "B1" in kinds and len(after) >= 2:
            b1 = next(s for s in sigs if s.kind == "B1")
            pull = [b for b in after if b.direction < 0 and b.end_ts > b1.ts]
            if pull and pull[0].end_price > b1.price:
                add("B2", "二买", pull[0], "回抽不破一买低点")
        if "S1" in kinds and len(after) >= 2:
            s1 = next(s for s in sigs if s.kind == "S1")
            pull = [b for b in after if b.direction > 0 and b.end_ts > s1.ts]
            if pull and pull[0].end_price < s1.price:
                add("S2", "二卖", pull[0], "反抽不破一卖高点")

        # 三类: 离开后回抽不进中枢
        if after:
            first = after[0]
            if first.direction > 0 and len(after) >= 2:
                pull = after[1]
                if pull.direction < 0 and pull.low > last_hub.zg:
                    add("B3", "三买", pull, f"回抽低点 {pull.low:g} > 中枢 ZG {last_hub.zg:g}")
            if first.direction < 0 and len(after) >= 2:
                pull = after[1]
                if pull.direction > 0 and pull.high < last_hub.zd:
                    add("S3", "三卖", pull, f"反抽高点 {pull.high:g} < 中枢 ZD {last_hub.zd:g}")

    # 去重: 同 kind 只留最后一个
    uniq: Dict[str, Signal] = {}
    for s in sigs:
        uniq[s.kind] = s
    order = ["B1", "B2", "B3", "S1", "S2", "S3"]
    return [uniq[k] for k in order if k in uniq]


def analyze(rows: List[Dict[str, Any]], timeframe: str = "1d") -> Dict[str, Any]:
    raw = bars_from_dicts(rows)
    merged = merge_include(raw)
    fxs = find_fenxing(merged)
    bis = build_bis(merged, fxs)
    segs = build_segments(bis)
    hubs = build_zhongshu(bis)
    closes = [b.close for b in raw]
    hist = macd_hist(closes)
    trend = classify_trend(hubs, bis)
    signals = find_signals(bis, hubs, hist, trend)

    def bi_d(b: Bi) -> Dict[str, Any]:
        return {
            "direction": "up" if b.direction > 0 else "down",
            "start_ts": b.start_ts,
            "end_ts": b.end_ts,
            "start_price": round(b.start_price, 4),
            "end_price": round(b.end_price, 4),
        }

    def seg_d(s: Segment) -> Dict[str, Any]:
        return {
            "direction": "up" if s.direction > 0 else "down",
            "start_ts": s.start_ts,
            "end_ts": s.end_ts,
            "start_price": round(s.start_price, 4),
            "end_price": round(s.end_price, 4),
            "finished": s.finished,
        }

    return {
        "timeframe": timeframe,
        "level_label": TF_LABEL.get(timeframe, timeframe),
        "bar_count": len(raw),
        "merged_count": len(merged),
        "fenxing_count": len(fxs),
        "bi_count": len(bis),
        "segment_count": len(segs),
        "trend": trend,
        "zhongshu": [
            {
                "zg": h.zg, "zd": h.zd,
                "start_ts": h.start_ts, "end_ts": h.end_ts,
                "direction": h.direction,
                "bi_count": h.bi_count,
            }
            for h in hubs
        ],
        "bis": [bi_d(b) for b in bis],
        "segments": [seg_d(s) for s in segs],
        "signals": [
            {"kind": s.kind, "label": s.label, "ts": s.ts, "price": round(s.price, 4), "note": s.note}
            for s in signals
        ],
        "klines": [
            {"timestamp": b.ts, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
            for b in raw
        ],
    }
