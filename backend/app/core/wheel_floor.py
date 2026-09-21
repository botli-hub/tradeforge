"""动态 floor / 卖 Call strike 建议

愿接价 = 推荐价(市场结构 suggest)。不再保留独立可手改 floor。
DB 列 floor_price 仅作推荐价缓存;手写会被同步覆盖。
"""
from typing import Any, Dict, List, Optional


def _closes(symbol: str, limit: int = 320) -> List[float]:
    from app.core.volatility import get_daily_closes
    return get_daily_closes(symbol, limit=limit)


def suggest_floor(
    symbol: str,
    spot: Optional[float] = None,
    current_floor: Optional[float] = None,
    iv_rank: Optional[float] = None,
) -> Dict[str, Any]:
    """返回市场结构推荐愿接价(唯一愿接源;可写回 floor_price 缓存)。"""
    from app.core.volatility import compute_ema
    from app.core.wheel_score import compute_atr

    closes = _closes(symbol)
    if spot is None or spot <= 0:
        spot = closes[-1] if closes else None
    # 无日K时仍尽量给参考:用现价×0.9 或沿用当前缓存
    if not closes:
        fallback = None
        if spot and spot > 0:
            fallback = round(float(spot) * 0.90, 2)
        elif current_floor and float(current_floor) > 0:
            fallback = float(current_floor)
        return {
            "symbol": symbol,
            "suggested_floor": fallback,
            "spot": spot,
            "components": {},
            "message": "无本地日K,推荐价为现价×0.9或缓存愿接",
            "is_reference_only": False,
            "definition": "愿接=推荐价(Put行权价上限),不是止损线;不可手改",
        }
    if spot is None or spot <= 0:
        spot = closes[-1]

    ema200 = compute_ema(closes, 200)
    ema50 = compute_ema(closes, 50)
    atr = compute_atr(closes, 20)
    # 近 60 日低点
    lookback = closes[-60:] if len(closes) >= 60 else closes
    low60 = min(lookback) if lookback else spot

    # IV 高位愿接更低:k 增大
    k = 1.5
    if iv_rank is not None and iv_rank >= 70:
        k = 2.0
    elif iv_rank is not None and iv_rank <= 30:
        k = 1.2

    atr_floor = (spot - k * atr) if atr else None
    candidates = [x for x in [ema200, low60 * 1.02, atr_floor] if x and x > 0]
    # 推荐价取候选中位数偏保守(偏低一点但不超过 spot*0.98)
    if candidates:
        candidates.sort()
        mid = candidates[len(candidates) // 2]
        suggested = round(min(mid, spot * 0.98), 2)
    else:
        suggested = round(spot * 0.90, 2)

    # 不低于现价的 70%(防止离谱)
    suggested = max(suggested, round(spot * 0.70, 2))

    return {
        "symbol": symbol,
        "spot": round(spot, 4),
        "suggested_floor": suggested,
        "current_floor": current_floor,
        "delta_vs_current": round(suggested - current_floor, 2) if current_floor else None,
        "components": {
            "ema200": ema200,
            "ema50": ema50,
            "low60": round(low60, 4),
            "atr20": round(atr, 4) if atr else None,
            "atr_floor": round(atr_floor, 4) if atr_floor else None,
            "iv_rank": iv_rank,
            "atr_k": k,
        },
        "rationale": (
            f"推荐愿接(唯一源):EMA200 / 近60日低点 / spot−{k}×ATR;"
            + ("IV高位加大缓冲;" if (iv_rank or 0) >= 70 else "标准缓冲;")
            + "Put strike必须≤推荐价;Call用成本底线"
        ),
        "definition": "愿接=推荐价(Put行权价上限),不是止损线;不可手改",
        "is_reference_only": False,
    }


def resolve_willing_price(
    symbol: str,
    spot: Optional[float] = None,
    iv_rank: Optional[float] = None,
    current_floor: Optional[float] = None,
) -> Optional[float]:
    """唯一愿接价 = 推荐价。suggest 失败时回退缓存 floor。"""
    sug = suggest_floor(symbol, spot, current_floor, iv_rank)
    sf = sug.get("suggested_floor")
    try:
        if sf is not None and float(sf) > 0:
            return round(float(sf), 2)
    except (TypeError, ValueError):
        pass
    try:
        if current_floor is not None and float(current_floor) > 0:
            return round(float(current_floor), 2)
    except (TypeError, ValueError):
        pass
    return None


def apply_willing_floor(
    target: Dict[str, Any],
    *,
    spot: Optional[float] = None,
    iv_rank: Optional[float] = None,
    sync_db: bool = False,
) -> Dict[str, Any]:
    """把 target.floor_price 对齐为推荐价;可选写回 DB 清掉手改残留。

    返回 mutate 后的 target,并填充 suggested_floor(=愿接)。
    """
    symbol = (target.get("symbol") or "").strip().upper()
    if not symbol:
        return target
    cached = target.get("floor_price")
    try:
        cached_f = float(cached) if cached is not None else None
    except (TypeError, ValueError):
        cached_f = None

    sug = suggest_floor(symbol, spot, cached_f, iv_rank)
    willing = None
    try:
        sf = sug.get("suggested_floor")
        if sf is not None and float(sf) > 0:
            willing = round(float(sf), 2)
    except (TypeError, ValueError):
        willing = None
    if willing is None and cached_f and cached_f > 0:
        willing = round(cached_f, 2)

    sug_spot = sug.get("spot")
    try:
        if sug_spot is not None and float(sug_spot) > 0:
            target["suggested_floor_spot"] = round(float(sug_spot), 2)
            if spot is None and target.get("spot") is None:
                target["spot"] = target["suggested_floor_spot"]
    except (TypeError, ValueError):
        pass

    target["suggested_floor"] = willing
    target["suggested_floor_delta"] = 0.0 if willing is not None else None
    target["suggested_floor_note"] = sug.get("rationale") or sug.get("message")
    if willing is not None:
        old = cached_f
        target["floor_price"] = willing
        if sync_db and (old is None or abs(float(old) - willing) > 1e-9):
            try:
                from app.data import wheel_repository as repo
                repo.update_target(symbol, floor_price=willing)
                repo.log_floor_change(symbol, old, willing, source="recommend")
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "sync floor_price cache failed %s: %s", symbol, e,
                )
    return target


def sync_target_willing_floor(symbol: str, *, spot: Optional[float] = None) -> Optional[float]:
    """按推荐价写回某标的 floor_price 缓存。返回愿接价。"""
    from app.data import wheel_repository as repo
    from app.core.volatility import brief_profile

    t = repo.get_target(symbol.strip().upper())
    if not t:
        # 无 target 时仍可算推荐价(添加前预览)
        iv = None
        try:
            iv = (brief_profile(symbol) or {}).get("iv_rank")
        except Exception:
            pass
        return resolve_willing_price(symbol, spot, iv, None)

    iv = None
    try:
        iv = (brief_profile(symbol) or {}).get("iv_rank")
    except Exception:
        pass
    apply_willing_floor(t, spot=spot, iv_rank=iv, sync_db=True)
    try:
        return float(t["floor_price"]) if t.get("floor_price") else None
    except (TypeError, ValueError):
        return None


def suggest_call_strikes(
    symbol: str,
    spot: float,
    cost_basis: Optional[float],
    delta_min: float = 0.15,
    delta_max: float = 0.30,
) -> Dict[str, Any]:
    """卖 Call 行权价锚点:成本基础、阻力、整数关口。"""
    from app.core.volatility import compute_ema
    from app.core.wheel_score import compute_atr

    closes = _closes(symbol)
    ema20 = compute_ema(closes, 20) if closes else None
    atr = compute_atr(closes, 20) if closes else None
    # 近 20 日高点作阻力
    high20 = max(closes[-20:]) if closes and len(closes) >= 5 else spot

    anchors = []
    if cost_basis and cost_basis > 0:
        anchors.append({"label": "cost_basis", "strike": round(cost_basis, 2), "note": "被call不亏成本"})
        # 小利润 call
        anchors.append({
            "label": "basis_plus_2pct",
            "strike": round(cost_basis * 1.02, 2),
            "note": "成本+2%锁定利润",
        })
    if ema20:
        anchors.append({"label": "ema20", "strike": round(max(ema20, cost_basis or 0), 2), "note": "短期均线上方"})
    if atr:
        anchors.append({
            "label": "spot_plus_1atr",
            "strike": round(spot + atr, 2),
            "note": "现价+1ATR",
        })
    # 整数关口
    round_strike = round(spot * 1.03 / 5) * 5  # 约 3% OTM 取整到 5
    anchors.append({"label": "round_otm", "strike": float(round_strike), "note": "约3%OTM整数关"})

    # 过滤:至少不低于 cost_basis
    floor = cost_basis or 0
    filtered = [a for a in anchors if a["strike"] >= floor * 0.999]
    filtered.sort(key=lambda x: x["strike"])

    return {
        "symbol": symbol,
        "spot": spot,
        "cost_basis": cost_basis,
        "resistance_high20": round(high20, 4) if high20 else None,
        "anchors": filtered,
        "delta_range": [delta_min, delta_max],
        "tip": "优先选 strike≥cost_basis 且靠近阻力/整数关的合约;大涨时可抬高 strike 保留上行空间",
    }


# 兼容旧名/旧语义
suggest_call_floor = suggest_call_strikes  # noqa: F401
