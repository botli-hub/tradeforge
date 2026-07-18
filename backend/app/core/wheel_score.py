"""Wheel 合约综合打分

score = annualized(现金担保口径, 默认 mid 计)
        × liquidity_factor(bid-ask spread 惩罚)
        × trend_factor(EMA50/EMA200 趋势,仅卖 Put 惩罚逆势)
        × earnings_factor(到期前覆盖财报则惩罚/硬过滤)
        × iv_bonus(IV rank 加成)
        × delta_factor(IV 高位时同等年化偏好低 delta)
        × pop_factor(虚值概率加成)
        × buffer_factor(相对 ATR 缓冲)
        × headroom_factor(组合资金余量)

所有权重可在设置页 wheel_scan 段覆盖,分项明细随建议返回,前端可展示归因。
"""
from typing import Any, Dict, List, Optional

# 代码兜底默认;数据库(设置页)可覆盖,见 get_scan_cfg()
DEFAULT_SCAN_CFG: Dict[str, Any] = {
    "max_spread_pct": 10.0,      # spread% 超过此值直接过滤
    "spread_soft_pct": 4.0,      # 低于此值不惩罚
    "earnings_penalty": 0.85,    # 到期前有财报的乘数
    "earnings_hard_filter": True,  # True=覆盖财报直接不推荐卖 Put
    "iv_rank_bonus": 0.20,       # 满 rank(100)时加成上限:score ×(1+0.20)
    "trend_penalty_below_ema50": 0.90,   # 现价 < EMA50(仍 > EMA200)
    "trend_penalty_below_ema200": 0.70,  # 现价 < EMA200
    "top_per_symbol": 3,         # 扫描时每标的取前 N
    "top_overall": 20,           # 扫描汇总取前 N
    "chain_cache_ttl_sec": 900,  # 期权链缓存
    "auto_push_minutes": 0,      # 定时扫描推送间隔(分钟),0=关闭
    "telegram_top_n": 5,         # TG 推送条数
    "premium_pricing": "mid",    # bid | mid — 权利金估价
    "pop_weight": 0.35,          # POP 对分数的加成权重
    "buffer_atr_min": 0.8,       # put 缓冲至少 0.8×ATR 才不惩罚
    "buffer_weight": 0.25,       # 缓冲不足时惩罚力度
    "headroom_boost": 0.15,      # 有资金余量时最高加成
    "min_iv_history_for_bonus": 30,  # IV 历史不足时不给满额 IV 加成
    "sort_mode": "score",        # score | robust(稳健:高POP+缓冲)
}


def get_scan_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """合并默认值与设置页 wheel_scan 段"""
    merged = dict(DEFAULT_SCAN_CFG)
    merged.update(cfg.get("wheel_scan", {}) or {})
    return merged


def premium_from_quote(
    bid: Optional[float],
    ask: Optional[float],
    pricing: str = "mid",
) -> float:
    """权利金估价:mid 更贴近可成交价;bid 偏乐观。"""
    b = float(bid or 0)
    a = float(ask or 0)
    if pricing == "bid":
        return b if b > 0 else 0.0
    if b > 0 and a > 0 and a >= b:
        return (b + a) / 2.0
    return b if b > 0 else (a if a > 0 else 0.0)


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


def estimate_pop(side: str, delta: float) -> float:
    """用 |delta| 近似到期 OTM 概率(卖方视角)。
    卖 Put: POP ≈ 1 - |delta|; 卖 Call: 同理。
    夹到 [0.05, 0.98] 避免极端。"""
    d = abs(float(delta or 0))
    pop = 1.0 - d
    return max(0.05, min(0.98, pop))


def estimate_ev(
    premium: float,
    collateral: float,
    pop: float,
    downside_pct: float = 0.08,
) -> float:
    """粗期望收益(占担保金 %): premium×POP − downside×(1−POP)。
    downside_pct 为被行权时相对担保金的粗估损失比例。"""
    if collateral <= 0:
        return 0.0
    prem_pct = premium / collateral * 100
    down = downside_pct * 100
    return round(prem_pct * pop - down * (1.0 - pop), 3)


def compute_atr(closes: List[float], window: int = 20) -> Optional[float]:
    """用收盘价序列近似 ATR(无高低价时用 |Δclose| 均值)。"""
    if len(closes) < window + 1:
        return None
    diffs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    seg = diffs[-window:]
    if not seg:
        return None
    return sum(seg) / len(seg)


def buffer_atr_multiple(
    side: str,
    spot: Optional[float],
    strike: float,
    atr: Optional[float],
) -> Optional[float]:
    """strike 距现价相对 ATR 的倍数(卖 Put 为现价−strike)。"""
    if not spot or spot <= 0 or not atr or atr <= 0:
        return None
    if side == "PUT":
        buf = spot - strike
    else:
        buf = strike - spot
    return round(buf / atr, 3)


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
    atr = compute_atr(closes, 20)
    return {
        "ema50": ema50, "ema200": ema200,
        "above_ema50": above50, "above_ema200": above200,
        "trend": trend,
        "pct_vs_ema50": round((spot - ema50) / ema50 * 100, 2) if ema50 else None,
        "pct_vs_ema200": round((spot - ema200) / ema200 * 100, 2) if ema200 else None,
        "atr20": atr,
        "closes_n": len(closes),
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


def pop_factor(pop: float, scan_cfg: Dict[str, Any]) -> float:
    """POP 越高加成越大;权重由 pop_weight 控制。pop=0.7 → 约 1+0.35*0.2=1.07"""
    w = float(scan_cfg.get("pop_weight", 0.35) or 0)
    # 以 0.7 为中性: (pop-0.5) 映射
    return round(1.0 + w * (pop - 0.5), 4)


def buffer_factor(buf_atr: Optional[float], scan_cfg: Dict[str, Any], side: str) -> float:
    """缓冲不足惩罚;卖 Call 缓冲为上方空间。"""
    if buf_atr is None or side != "PUT":
        return 1.0
    mn = float(scan_cfg.get("buffer_atr_min", 0.8) or 0.8)
    w = float(scan_cfg.get("buffer_weight", 0.25) or 0.25)
    if buf_atr >= mn:
        return 1.0
    if buf_atr <= 0:
        return round(1.0 - w, 4)
    return round(1.0 - w * (1.0 - buf_atr / mn), 4)


def headroom_factor(headroom_ratio: Optional[float], scan_cfg: Dict[str, Any]) -> float:
    """headroom_ratio = 可用资金/max_capital,越高加成越大。"""
    if headroom_ratio is None:
        return 1.0
    boost = float(scan_cfg.get("headroom_boost", 0.15) or 0)
    r = max(0.0, min(1.0, float(headroom_ratio)))
    return round(1.0 + boost * r, 4)


def score_contract(
    annualized: float,
    side: str,
    delta: float,
    sp: Optional[float],
    covers_earnings: bool,
    volatility: Optional[Dict[str, Any]],
    trend: Optional[Dict[str, Any]],
    scan_cfg: Dict[str, Any],
    *,
    pop: Optional[float] = None,
    buffer_atr: Optional[float] = None,
    headroom_ratio: Optional[float] = None,
    premium: Optional[float] = None,
    collateral: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """返回 {"score", "robust_score", "pop", "ev_pct", "factors"};spread 超限返回 None。"""
    # 财报硬过滤(仅 Put)
    if (
        side == "PUT"
        and covers_earnings
        and scan_cfg.get("earnings_hard_filter", True)
    ):
        return None

    liq = liquidity_factor(sp, scan_cfg)
    if liq is None:
        return None
    earn = scan_cfg["earnings_penalty"] if covers_earnings else 1.0
    tr = trend_factor(side, trend, scan_cfg)

    iv_rank = (volatility or {}).get("iv_rank") or 0
    hist_days = (volatility or {}).get("iv_history_days") or 0
    min_hist = int(scan_cfg.get("min_iv_history_for_bonus", 30) or 0)
    # 冷启动:IV 历史不足时减半加成,避免误导
    bonus_scale = 1.0 if hist_days >= min_hist or (volatility or {}).get("iv_rank_source") == "iv_history" else 0.5
    if (volatility or {}).get("iv_rank_source") == "hv_proxy":
        bonus_scale = min(bonus_scale, 0.5)
    iv_bonus = 1.0 + scan_cfg["iv_rank_bonus"] * iv_rank / 100.0 * bonus_scale

    # IV 高位:同等年化偏好更低 delta(更远离行权价)
    delta_f = (1.0 - abs(delta)) if is_iv_high(volatility) else 1.0

    pop_v = pop if pop is not None else estimate_pop(side, delta)
    pop_f = pop_factor(pop_v, scan_cfg)
    buf_f = buffer_factor(buffer_atr, scan_cfg, side)
    head_f = headroom_factor(headroom_ratio, scan_cfg)

    score = annualized * liq * earn * tr * iv_bonus * delta_f * pop_f * buf_f * head_f
    # 稳健分:更重视 POP 与缓冲,年化权重降低
    robust = (annualized ** 0.5) * (pop_v * 100) * liq * earn * tr * buf_f * head_f * iv_bonus

    ev = None
    if premium is not None and collateral and collateral > 0:
        # 缓冲越差 downside 越大
        down = 0.12 if (buffer_atr is not None and buffer_atr < 0.5) else 0.08
        if trend and trend.get("trend") == "DOWN":
            down += 0.04
        ev = estimate_ev(premium, collateral, pop_v, down)

    return {
        "score": round(score, 2),
        "robust_score": round(robust, 2),
        "pop": round(pop_v, 4),
        "ev_pct": ev,
        "factors": {
            "annualized": annualized,
            "liquidity": liq,
            "spread_pct": sp,
            "earnings": earn,
            "trend": tr,
            "iv_bonus": round(iv_bonus, 3),
            "delta_pref": round(delta_f, 3) if delta_f != 1.0 else 1.0,
            "pop": round(pop_f, 3),
            "buffer": round(buf_f, 3),
            "headroom": round(head_f, 3),
        },
    }


def sort_key_for_mode(item: Dict[str, Any], mode: str = "score"):
    if mode == "robust":
        return item.get("robust_score") or item.get("score") or 0
    return item.get("score") or 0
