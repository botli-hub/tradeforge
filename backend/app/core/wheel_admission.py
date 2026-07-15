"""Wheel 标的准入评分

综合流动性代理、波动、趋势、历史 wheel 表现,输出 0-100 分与建议动作。
"""
from typing import Any, Dict, List, Optional


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
    # 权利金从 trades 汇总(stats ranking 风格简化)
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
    score = 50.0  # 中性起点
    tags: List[str] = []

    # 趋势
    tname = (trend or {}).get("trend")
    if tname == "UP":
        score += 15
        factors["trend"] = 15
    elif tname == "WEAK":
        score -= 5
        factors["trend"] = -5
        tags.append("趋势转弱")
    elif tname == "DOWN":
        score -= 20
        factors["trend"] = -20
        tags.append("下跌趋势")
    else:
        factors["trend"] = 0
        tags.append("趋势数据不足")

    # 波动:HV 过低权利金差,过高接货伤 — 甜蜜区 15-40%
    if hv20 is not None:
        if 15 <= hv20 <= 40:
            score += 15
            factors["hv"] = 15
        elif hv20 < 12:
            score -= 10
            factors["hv"] = -10
            tags.append("波动过低")
        elif hv20 > 55:
            score -= 12
            factors["hv"] = -12
            tags.append("波动过高")
        else:
            score += 5
            factors["hv"] = 5
    else:
        factors["hv"] = 0

    # IV rank: 偏高有利于卖方
    ivr = vol.get("iv_rank")
    if ivr is not None:
        if ivr >= 50:
            score += 10
            factors["iv_rank"] = 10
        elif ivr < 25:
            score -= 8
            factors["iv_rank"] = -8
            tags.append("IV低位")
        else:
            factors["iv_rank"] = 0
    else:
        factors["iv_rank"] = 0

    # 历史 wheel 表现
    if closed:
        avg_pnl = realized / len(closed)
        if avg_pnl > 0:
            score += min(15, avg_pnl / 50)  # 粗略
            factors["history_pnl"] = round(min(15, avg_pnl / 50), 2)
        else:
            score -= min(15, abs(avg_pnl) / 50)
            factors["history_pnl"] = round(-min(15, abs(avg_pnl) / 50), 2)
            tags.append("历史轮子偏亏")
    else:
        factors["history_pnl"] = 0
        tags.append("无已结束轮子")

    # floor 合理性
    if target and spot and target.get("floor_price"):
        floor = target["floor_price"]
        if floor > spot:
            score -= 15
            factors["floor"] = -15
            tags.append("floor>现价")
        elif floor < spot * 0.7:
            score -= 5
            factors["floor"] = -5
            tags.append("floor过远")
        else:
            score += 5
            factors["floor"] = 5
    else:
        factors["floor"] = 0

    # K线数据充足
    if len(closes) >= 200:
        score += 5
        factors["data"] = 5
    elif len(closes) < 60:
        score -= 10
        factors["data"] = -10
        tags.append("日K不足")
    else:
        factors["data"] = 0

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
            "floor_price": (target or {}).get("floor_price"),
            "enabled": bool((target or {}).get("enabled", True)),
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
    }
