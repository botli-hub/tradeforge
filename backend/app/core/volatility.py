"""标的波动率档案:实际波动率(HV)、期望波动率(ATM IV)、IV Rank

- HV(历史/实际波动率): 本地日K收盘价对数收益率标准差年化,窗口 20/60 日
- ATM IV(期望波动率): 期权链上最接近现价的合约隐含波动率(put/call 均值)
- IV Rank: 当前 ATM IV 在本地积累的标的 IV 历史中的百分位(0-100);
  历史不足 min_history_days 时给出 IV/HV 比值作为替代参考
"""
import logging
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.data.database import get_db, _now_iso

logger = logging.getLogger(__name__)

MIN_HISTORY_DAYS = 60  # IV Rank 有意义所需的最少历史天数


# ── 本地日K ───────────────────────────────────────────────────────────────────

def get_daily_closes(symbol: str, limit: int = 300) -> List[float]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT close FROM kline_bars WHERE symbol = ? AND timeframe = '1d' ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [float(r["close"]) for r in reversed(rows)]
    finally:
        conn.close()


def compute_hv(closes: List[float], window: int) -> Optional[float]:
    """年化历史波动率(%),基于对数收益率"""
    if len(closes) < window + 1:
        return None
    rets = []
    seg = closes[-(window + 1):]
    for i in range(1, len(seg)):
        if seg[i - 1] > 0 and seg[i] > 0:
            rets.append(math.log(seg[i] / seg[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 2)


def compute_ema(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return round(ema, 4)


# ── ATM IV(从已加载的期权链提取,不额外请求)────────────────────────────────────

def atm_iv_from_chain(contracts: List[Dict[str, Any]], spot: float) -> Optional[float]:
    """从期权链合约列表提取 ATM 隐含波动率(%)。put/call 各取最接近现价的,再平均"""
    best: Dict[str, Any] = {}
    for c in contracts:
        iv = c.get("iv") or 0
        if iv <= 0:
            continue
        ot = c.get("option_type")
        dist = abs((c.get("strike") or 0) - spot)
        if ot not in best or dist < best[ot][0]:
            best[ot] = (dist, iv)
    ivs = [v[1] for v in best.values()]
    if not ivs:
        return None
    iv = sum(ivs) / len(ivs)
    # 归一化到百分数(链上 iv 可能是 0.32 或 32)
    return round(iv * 100 if iv < 3 else iv, 2)


# ── IV 历史积累与 Rank ────────────────────────────────────────────────────────

def save_iv_snapshot(symbol: str, iv: float, spot: Optional[float] = None):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO underlying_iv_history (symbol, date, iv, spot, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET iv = excluded.iv, spot = excluded.spot""",
            (symbol, date.today().isoformat(), iv, spot, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_iv_rank(symbol: str, current_iv: float) -> Dict[str, Any]:
    """返回 {iv_rank, history_days}。历史不足时 iv_rank 为 None"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT iv FROM underlying_iv_history WHERE symbol = ? ORDER BY date DESC LIMIT 252",
            (symbol,),
        ).fetchall()
    finally:
        conn.close()
    ivs = [r["iv"] for r in rows]
    days = len(ivs)
    if days < MIN_HISTORY_DAYS:
        return {"iv_rank": None, "history_days": days}
    rank = sum(1 for v in ivs if v <= current_iv) / days * 100
    return {"iv_rank": round(rank, 1), "history_days": days}


# ── 汇总档案 ──────────────────────────────────────────────────────────────────

def build_profile(symbol: str, spot: float,
                  chain_contracts: Optional[List[Dict[str, Any]]] = None,
                  atm_iv: Optional[float] = None) -> Dict[str, Any]:
    """组装波动率档案。atm_iv/chain 二选一提供;会顺手保存 IV 快照"""
    closes = get_daily_closes(symbol)
    hv20 = compute_hv(closes, 20)
    hv60 = compute_hv(closes, 60)
    ema20 = compute_ema(closes, 20)

    if atm_iv is None and chain_contracts:
        atm_iv = atm_iv_from_chain(chain_contracts, spot)

    result: Dict[str, Any] = {
        "symbol": symbol,
        "spot": spot,
        "atm_iv": atm_iv,          # 期望波动率(隐含) %
        "hv20": hv20,              # 实际波动率(20日) %
        "hv60": hv60,              # 实际波动率(60日) %
        "ema20": ema20,
        "iv_rank": None,
        "iv_history_days": 0,
        "iv_hv_ratio": None,       # IV/HV20,历史不足时的富余度替代指标
        "kline_days": len(closes),
    }
    if atm_iv is not None:
        save_iv_snapshot(symbol, atm_iv, spot)
        rank = get_iv_rank(symbol, atm_iv)
        result["iv_rank"] = rank["iv_rank"]
        result["iv_history_days"] = rank["history_days"]
        if hv20:
            result["iv_hv_ratio"] = round(atm_iv / hv20, 3)
    return result
