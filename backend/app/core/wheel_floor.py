"""动态 floor / 卖 Call strike 建议

基于本地日K:EMA200、近低点、ATR;结合 IV 环境微调。
仅输出建议,不自动改写 target.floor_price。
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
    """返回市场结构参考愿接价(非「正确floor」;不自动写库)。"""
    from app.core.volatility import compute_ema
    from app.core.wheel_score import compute_atr

    closes = _closes(symbol)
    if not closes:
        return {
            "symbol": symbol,
            "suggested_floor": current_floor,
            "spot": spot,
            "components": {},
            "message": "无本地日K,无法计算",
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
    # 建议 floor 取候选中位数偏保守(偏低一点但不超过 spot*0.98)
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
            f"市场结构参考(非强制):EMA200 / 近60日低点 / spot−{k}×ATR;"
            + ("IV高位加大缓冲;" if (iv_rank or 0) >= 70 else "标准缓冲;")
            + "最终愿接价须你确认;Put strike必须≤floor;Call用成本底线"
        ),
        "definition": "floor=CSP愿接最高价(Put行权价上限),不是止损线",
        "is_reference_only": True,
    }


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
