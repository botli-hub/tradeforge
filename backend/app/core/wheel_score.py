"""Wheel 合约综合打分

score = annualized(现金担保口径,bid 计)
        × liquidity_factor(bid-ask spread 惩罚)
        × trend_factor(EMA50/EMA200 趋势,仅卖 Put 惩罚逆势)
        × earnings_factor(到期前覆盖财报则惩罚)
        × iv_bonus(IV rank 加成)
        × delta_factor(IV 高位时同等年化偏好低 delta)

所有权重可在设置页 wheel_scan 段覆盖,分项明细随建议返回,前端可展示归因。
"""
from typing import Any, Dict, List, Optional

# 代码兜底默认;数据库(设置页)可覆盖,见 _scan_cfg()
DEFAULT_SCAN_CFG: Dict[str, Any] = {
    "max_spread_pct": 10.0,      # spread% 超过此值直接过滤
    "spread_soft_pct": 4.0,      # 低于此值不惩罚
    "earnings_penalty": 0.85,    # 到期前有财报的乘数
    "iv_rank_bonus": 0.20,       # 满 rank(100)时加成上限:score ×(1+0.20)
    "trend_penalty_below_ema50": 0.90,   # 现价 < EMA50(仍 > EMA200)
    "trend_penalty_below_ema200": 0.70,  # 现价 < EMA200
    "top_per_symbol": 3,         # 扫描时每标的取前 N
    "top_overall": 20,           # 扫描汇总取前 N
    "chain_cache_ttl_sec": 900,  # 期权链缓存
    "auto_push_minutes": 0,      # 定时扫描推送间隔(分钟),0=关闭
}


def get_scan_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """合并默认值与设置页 wheel_scan 段"""
    merged = dict(DEFAULT_SCAN_CFG)
    merged.update(cfg.get("wheel_scan", {}) or {})
    return merged


def spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """(ask-bid)/mid × 100;无有效双边报价返回 None"""
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100, 2)


def liquidity_factor(sp: Optional[float], scan_cfg: Dict[str, Any]) -> Optional[float]:
    """spread ≤ soft → 1.0;soft~max 线性降至 0.7;> max → None(应过滤)。
    单边报价(sp is None)按最差可接受处理,给 0.7 但不过滤——由调用方决定。"""
    max_sp = scan_cfg["max_spread_pct"]
    soft = scan_cfg["spread_soft_pct"]
    if sp is None:
        return 0.7
    if sp > max_sp:
        return None
    if sp <= soft:
        return 1.0
    return round(1.0 - 0.3 * (sp - soft) / max(max_sp - soft, 1e-9), 4)


def trend_profile(symbol: str, spot: Optional[float]) -> Optional[Dict[str, Any]]:
    """基于本地日K的 EMA50/EMA200 趋势档案;数据不足时返回 None(不惩罚)"""
    if not spot or spot <= 0:
        return None
    try:
        from app.core.volatility import get_daily_closes, compute_ema
        closes = get_daily_closes(symbol, limit=320)
        ema50 = compute_ema(closes, 50)
        ema200 = compute_ema(closes, 200)
    except Exception:
        return None
    if ema50 is None and ema200 is None:
        return None
    above50 = spot >= ema50 if ema50 else None
    above200 = spot >= ema200 if ema200 else None
    if above200 is False:
        trend = "DOWN"
    elif above50 is False:
        trend = "WEAK"
    else:
        trend = "UP"
    return {
        "ema50": ema50, "ema200": ema200,
        "above_ema50": above50, "above_ema200": above200,
        "trend": trend,
        "pct_vs_ema50": round((spot - ema50) / ema50 * 100, 2) if ema50 else None,
        "pct_vs_ema200": round((spot - ema200) / ema200 * 100, 2) if ema200 else None,
    }


def trend_factor(side: str, trend: Optional[Dict[str, Any]], scan_cfg: Dict[str, Any]) -> float:
    """卖 Put 在弱势/下跌趋势中惩罚;卖 Call 是持股对冲,不惩罚"""
    if side != "PUT" or not trend:
        return 1.0
    t = trend.get("trend")
    if t == "DOWN":
        return scan_cfg["trend_penalty_below_ema200"]
    if t == "WEAK":
        return scan_cfg["trend_penalty_below_ema50"]
    return 1.0


def is_iv_high(volatility: Optional[Dict[str, Any]]) -> bool:
    if not volatility:
        return False
    return (volatility.get("iv_rank") or 0) >= 70 or (volatility.get("iv_hv_ratio") or 0) >= 1.3


def score_contract(
    annualized: float,
    side: str,
    delta: float,
    sp: Optional[float],
    covers_earnings: bool,
    volatility: Optional[Dict[str, Any]],
    trend: Optional[Dict[str, Any]],
    scan_cfg: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """返回 {"score": float, "factors": {...}};spread 超限返回 None(过滤)"""
    liq = liquidity_factor(sp, scan_cfg)
    if liq is None:
        return None
    earn = scan_cfg["earnings_penalty"] if covers_earnings else 1.0
    tr = trend_factor(side, trend, scan_cfg)
    iv_rank = (volatility or {}).get("iv_rank") or 0
    iv_bonus = 1.0 + scan_cfg["iv_rank_bonus"] * iv_rank / 100.0
    # IV 高位:同等年化偏好更低 delta(更远离行权价)
    delta_f = (1.0 - delta) if is_iv_high(volatility) else 1.0
    score = annualized * liq * earn * tr * iv_bonus * delta_f
    return {
        "score": round(score, 2),
        "factors": {
            "annualized": annualized,
            "liquidity": liq,
            "spread_pct": sp,
            "earnings": earn,
            "trend": tr,
            "iv_bonus": round(iv_bonus, 3),
            "delta_pref": round(delta_f, 3) if delta_f != 1.0 else 1.0,
        },
    }
