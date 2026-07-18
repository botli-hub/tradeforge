"""Wheel 标的准入评分

综合波动、趋势、历史 wheel 表现,输出 0-100 分与建议动作。
floor(愿接最高价)只作标签/激进度,不因「floor>现价」重罚。
"""
from typing import Any, Dict, List, Optional


def floor_stance(
    floor: Optional[float],
    spot: Optional[float],
) -> Dict[str, Any]:
    """愿接松紧标签(不进主分重罚)。

    floor = CSP 愿接最高价 / Put strike 上限。
    floor ≥ spot 表示愿接很紧(允许近价 Put),不是配置错误。
    """
    tags: List[str] = []
    stance = "unknown"
    ratio = None
    if not floor or floor <= 0 or not spot or spot <= 0:
        return {
            "stance": stance,
            "tags": tags,
            "floor_spot_ratio": None,
            "aggressiveness": "unknown",
        }
    ratio = round(float(floor) / float(spot), 3)
    if floor >= spot * 0.98:
        stance = "tight"
        tags.append("近价愿接(允许近价Put)")
        aggressiveness = "激进"
    elif floor < spot * 0.70:
        stance = "distant"
        tags.append("愿接偏远(机会少)")
        aggressiveness = "保守"
    elif floor < spot * 0.85:
        stance = "conservative"
        tags.append("愿接偏保守")
        aggressiveness = "偏保守"
    else:
        stance = "balanced"
        tags.append("愿接适中")
        aggressiveness = "中性"
    return {
        "stance": stance,
        "tags": tags,
        "floor_spot_ratio": ratio,
        "aggressiveness": aggressiveness,
    }


def score_symbol(symbol: str) -> Dict[str, Any]:
    from app.core.volatility import get_daily_closes, compute_hv, brief_profile
    from app.core.wheel_score import trend_profile, compute_atr
    from app.data import wheel_repository as repo

    target = repo.get_target(symbol)
    closes = get_daily_closes(symbol, limit=320)
    vol = brief_profile(symbol)
    spot = closes[-1] if closes else None
    trend = trend_profile(symbol, spot) if spot else None
    hv20 = compute_hv(closes, 20) if closes else None
    atr = compute_atr(closes, 20) if closes else None

    # 历史表现
    cycles = [c for c in repo.get_cycles(symbol=symbol, include_closed=True)]
    closed = [c for c in cycles if c["status"] == "CLOSED"]
    premium = 0.0
    realized = 0.0
    for c in closed:
        realized += c.get("realized_pnl") or 0
    trades = repo.get_trades(symbol=symbol, limit=500)
    for t in trades:
        tt = t["trade_type"]
        notional = (t.get("qty") or 1) * (t.get("price") or 0) * (t.get("contract_size") or 100)
        fee = t.get("fee") or 0
        if tt in ("SELL_PUT", "SELL_CALL"):
            premium += notional - fee
        elif tt in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE"):
            premium -= notional + fee

    factors: Dict[str, Any] = {}
    factor_detail: List[Dict[str, Any]] = []
    score = 50.0  # 中性起点
    tags: List[str] = []

    def _add_factor(key: str, delta: float, label: str, note: str = ""):
        factors[key] = delta
        factor_detail.append({
            "key": key,
            "delta": delta,
            "label": label,
            "note": note,
        })

    # 趋势
    tname = (trend or {}).get("trend")
    if tname == "UP":
        score += 15
        _add_factor("trend", 15, "趋势", "UP")
    elif tname == "WEAK":
        score -= 5
        _add_factor("trend", -5, "趋势", "WEAK")
        tags.append("趋势转弱")
    elif tname == "DOWN":
        score -= 20
        _add_factor("trend", -20, "趋势", "DOWN")
        tags.append("下跌趋势")
    else:
        _add_factor("trend", 0, "趋势", "数据不足")
        tags.append("趋势数据不足")

    # 波动:HV 甜蜜区 15-40%
    if hv20 is not None:
        if 15 <= hv20 <= 40:
            score += 15
            _add_factor("hv", 15, "历史波动", f"HV20={hv20:.1f}% 甜蜜区")
        elif hv20 < 12:
            score -= 10
            _add_factor("hv", -10, "历史波动", f"HV20={hv20:.1f}% 过低")
            tags.append("波动过低")
        elif hv20 > 55:
            score -= 12
            _add_factor("hv", -12, "历史波动", f"HV20={hv20:.1f}% 过高")
            tags.append("波动过高")
        else:
            score += 5
            _add_factor("hv", 5, "历史波动", f"HV20={hv20:.1f}%")
    else:
        _add_factor("hv", 0, "历史波动", "无数据")

    # IV rank
    ivr = vol.get("iv_rank")
    if ivr is not None:
        if ivr >= 50:
            score += 10
            _add_factor("iv_rank", 10, "IV Rank", f"IVR={ivr:.0f}")
        elif ivr < 25:
            score -= 8
            _add_factor("iv_rank", -8, "IV Rank", f"IVR={ivr:.0f} 低位")
            tags.append("IV低位")
        else:
            _add_factor("iv_rank", 0, "IV Rank", f"IVR={ivr:.0f}")
    else:
        _add_factor("iv_rank", 0, "IV Rank", "无数据")

    # 历史 wheel
    if closed:
        avg_pnl = realized / len(closed)
        if avg_pnl > 0:
            d = min(15, avg_pnl / 50)
            score += d
            _add_factor("history_pnl", round(d, 2), "历史轮子", f"均盈 ${avg_pnl:.0f}")
        else:
            d = -min(15, abs(avg_pnl) / 50)
            score += d
            _add_factor("history_pnl", round(d, 2), "历史轮子", f"均亏 ${avg_pnl:.0f}")
            tags.append("历史轮子偏亏")
    else:
        _add_factor("history_pnl", 0, "历史轮子", "无已结束轮子")
        tags.append("无已结束轮子")

    # floor: 标签 + 极轻提示,禁止 floor>现价 重罚
    floor_px = (target or {}).get("floor_price")
    try:
        floor_px = float(floor_px) if floor_px is not None else None
    except (TypeError, ValueError):
        floor_px = None
    fs = floor_stance(floor_px, spot)
    tags.extend(fs["tags"])
    floor_score_delta = 0.0
    floor_note = "愿接最高价不进主分重罚"
    if fs["stance"] == "distant":
        # 愿接过远仅轻提示机会少,非错误
        floor_score_delta = -2.0
        floor_note = "愿接偏远,Put 机会偏少(-2)"
        score += floor_score_delta
    elif fs["stance"] == "balanced":
        floor_score_delta = 2.0
        floor_note = "愿接适中(+2)"
        score += floor_score_delta
    elif fs["stance"] == "tight":
        floor_note = "近价愿接=更愿接货/允许近价Put,不扣分"
    _add_factor("floor", floor_score_delta, "愿接价floor", floor_note)

    # 与智能建议偏离(可选轻标签,不重罚)
    suggest_delta_pct = None
    suggested_floor = None
    try:
        from app.core.wheel_floor import suggest_floor
        if spot:
            sug = suggest_floor(symbol, spot, floor_px, ivr)
            sf = sug.get("suggested_floor")
            if sf is not None:
                suggested_floor = float(sf)
                if floor_px and spot:
                    suggest_delta_pct = round((floor_px - sf) / spot * 100, 1)
                    if abs(suggest_delta_pct) >= 8:
                        tags.append(f"愿接与市场结构参考偏离{suggest_delta_pct:+.0f}%spot")
    except Exception:
        pass

    # K线
    if len(closes) >= 200:
        score += 5
        _add_factor("data", 5, "日K数据", f"{len(closes)}根")
    elif len(closes) < 60:
        score -= 10
        _add_factor("data", -10, "日K数据", f"{len(closes)}根不足")
        tags.append("日K不足")
    else:
        _add_factor("data", 0, "日K数据", f"{len(closes)}根")

    score = max(0, min(100, round(score, 1)))
    if score >= 70:
        action = "优先轮动"
    elif score >= 50:
        action = "可交易"
    elif score >= 35:
        action = "降权/减仓"
    else:
        action = "建议移出或禁用"

    return {
        "symbol": symbol,
        "score": score,
        "action": action,
        "tags": tags,
        "factors": factors,
        "factor_detail": factor_detail,
        "floor_stance": fs["stance"],
        "aggressiveness": fs["aggressiveness"],
        "metrics": {
            "spot": spot,
            "hv20": hv20,
            "atr20": atr,
            "iv_rank": ivr,
            "trend": tname,
            "closed_cycles": len(closed),
            "premium_net": round(premium, 2),
            "realized_pnl": round(realized, 2),
            "kline_days": len(closes),
            "floor_price": floor_px,
            "suggested_floor": suggested_floor,
            "floor_spot_ratio": fs["floor_spot_ratio"],
            "suggest_delta_pct_spot": suggest_delta_pct,
            "enabled": bool((target or {}).get("enabled", True)),
            "floor_definition": "CSP愿接最高价;Put strike必须≤floor;Call用成本底线不用floor",
        },
    }


def score_all_targets() -> Dict[str, Any]:
    from app.data import wheel_repository as repo
    rows = [score_symbol(t["symbol"]) for t in repo.get_targets()]
    rows.sort(key=lambda x: x["score"], reverse=True)
    return {
        "items": rows,
        "prefer": [r for r in rows if r["score"] >= 70],
        "review": [r for r in rows if r["score"] < 35],
        "floor_glossary": (
            "floor=愿接最高价(Put行权价上限)。"
            "floor≥现价表示更愿接/允许近价Put,不是错误。"
            "Call用持股成本底线,与floor无关。"
        ),
    }
