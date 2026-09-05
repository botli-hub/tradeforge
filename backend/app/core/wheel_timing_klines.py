"""Wheel 触线 K 线：CALL=1h+1d / PUT=1d，按 timeframe 缓存与 EMA。

期权合约 K 线不走 FutuAdapter；由 leaps_monitor 价格缓存拉取。
本模块只含纯函数，便于无 OpenD 单测。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

TIMEFRAME_DAY = "1d"
TIMEFRAME_HOUR = "1h"
# Call 触线双周期:1h(盘中) + 1d(日线);档案按 timeframe 分桶不碰撞
CALL_SCAN_TIMEFRAMES = (TIMEFRAME_HOUR, TIMEFRAME_DAY)

# Futu KLType / SubType 名。adapter._ktype_map 已有 1h→K_60M，但期权 bar 不走 adapter。
_KL_DAY = ("K_DAY", "K_DAY")
_KL_HOUR = ("K_60M", "K_60M")


def normalize_timeframe(timeframe: Optional[str]) -> str:
    s = str(timeframe or TIMEFRAME_DAY).strip().lower()
    if s in ("1h", "60m", "k_60m", "hour", "hourly", "h"):
        return TIMEFRAME_HOUR
    if s in ("1d", "day", "daily", "k_day", "d"):
        return TIMEFRAME_DAY
    return TIMEFRAME_DAY


def default_timeframe(option_type: Optional[str]) -> str:
    """CALL 默认 1h(另可显式扫 1d)；PUT / LEAPS 默认日 K。"""
    if str(option_type or "").strip().upper() == "CALL":
        return TIMEFRAME_HOUR
    return TIMEFRAME_DAY


def resolve_scan_timeframe(
    option_type: Optional[str], timeframe: Optional[str] = None
) -> str:
    if timeframe:
        return normalize_timeframe(timeframe)
    return default_timeframe(option_type)


def futu_kl_names(timeframe: Optional[str]) -> Tuple[str, str]:
    """返回 (KLType 属性名, SubType 属性名)。PUT=K_DAY，CALL=K_60M。"""
    if normalize_timeframe(timeframe) == TIMEFRAME_HOUR:
        return _KL_HOUR
    return _KL_DAY


def history_key(contract_code: str, timeframe: Optional[str] = None) -> Tuple[str, str]:
    """档案/价格缓存主键：(contract_code, timeframe)。Put 日K 与 Call 1h 不碰撞。"""
    return (str(contract_code or ""), normalize_timeframe(timeframe))


def call_holding_cycles(cycles: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """持股轮(HOLDING)列表。CC 挂机/成本基础仍用此池。
    Call 触线扫描本身不再要求 HOLDING(见 WheelTimingMonitor.scan_all)。"""
    out: List[Dict[str, Any]] = []
    for c in cycles or []:
        if str((c or {}).get("status") or "").upper() == "HOLDING":
            out.append(c)  # type: ignore[arg-type]
    return out


def call_cost_basis_for_scan(cycles: Optional[Sequence[Dict[str, Any]]]) -> Optional[float]:
    """有 HOLDING 时取最大成本基础作 Call strike 下限锚;无持股返回 None。"""
    holding = call_holding_cycles(cycles)
    vals: List[float] = []
    for c in holding:
        try:
            v = float((c or {}).get("cost_basis") or 0)
            if v > 0:
                vals.append(v)
        except (TypeError, ValueError):
            pass
    return max(vals) if vals else None


def call_strike_min(
    cost_basis: Optional[float], sell_above: Optional[float] = None
) -> Optional[float]:
    """CALL strike 下限 = max(成本基础, 愿卖价)。0/None 忽略。"""
    vals: List[float] = []
    for v in (cost_basis, sell_above):
        try:
            if v is not None and float(v) > 0:
                vals.append(float(v))
        except (TypeError, ValueError):
            pass
    return max(vals) if vals else None


def _positive_float(v: Any) -> Optional[float]:
    """正数 float; 0/None/非法 → None。"""
    try:
        if v is None:
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def is_otm_put(strike: Any, spot: Any) -> bool:
    """Put 严格 OTM: strike < spot。ATM/ITM/无效 → False。无 epsilon。"""
    k = _positive_float(strike)
    s = _positive_float(spot)
    if k is None or s is None:
        return False
    return k < s


def is_otm_call(strike: Any, spot_or_anchor: Any) -> bool:
    """Call 严格 OTM: strike > spot_or_anchor。

    spot_or_anchor 一般为标的现价。CC 另有 strike≥max(cost_basis, sell_above)
    下限(见 call_strike_min),与本函数独立叠加。ATM/ITM/无效 → False。无 epsilon。
    """
    k = _positive_float(strike)
    a = _positive_float(spot_or_anchor)
    if k is None or a is None:
        return False
    return k > a


def bar_timestamp(time_key: Any, timeframe: Optional[str] = None) -> str:
    """日K 存 YYYY-MM-DD；1h 存到分钟，避免同日多根互相覆盖。"""
    raw = str(time_key or "").replace("T", " ").strip()
    if normalize_timeframe(timeframe) == TIMEFRAME_HOUR:
        return raw[:19] if len(raw) >= 16 else raw
    return raw[:10]


def snapshot_bar(
    contract: Dict[str, Any],
    today: str,
    timeframe: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """用快照拼一根 bar。1h 用当前小时整点作 date。"""
    tf = normalize_timeframe(timeframe)
    if tf == TIMEFRAME_HOUR:
        ts_src = now or datetime.now()
        date_s = ts_src.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00:00")
    else:
        date_s = today
    return {
        "date": date_s,
        "open": contract.get("open"),
        "high": contract.get("high"),
        "low": contract.get("low"),
        "close": contract.get("last_price") or contract.get("close"),
        "volume": contract.get("volume"),
        "iv": contract.get("iv"),
    }


def bars_on_day(price_history: Sequence[Dict[str, Any]], today: str) -> List[Dict[str, Any]]:
    """日K date==today；1h 按日期前缀匹配。"""
    day = str(today or "")[:10]
    return [b for b in price_history if str((b or {}).get("date") or "")[:10] == day]


def ema_touch(
    closes,
    trigger_price: float,
    *,
    ema50_min: int = 50,
    ema200_min: int = 200,
    allow_partial_ema: bool = True,
    level_map: Optional[Dict[str, str]] = None,
    compute_ema=None,
) -> Optional[Dict[str, Any]]:
    """last/high ≥ EMA200 强 / EMA50 一级。未触及返回 None。不访问 Futu。"""
    level_map = level_map or {"EMA50": "PRIMARY", "EMA200": "SECONDARY"}
    if compute_ema is None:
        def compute_ema(series, period):  # type: ignore[misc]
            return series.ewm(span=period, adjust=False).mean()
    try:
        px = float(trigger_price)
    except (TypeError, ValueError):
        return None
    n_bars = len(closes)

    def _try(period: int, min_bars: int, key: str) -> Optional[Dict[str, Any]]:
        if n_bars < min_bars:
            return None
        partial = n_bars < period
        if partial and not allow_partial_ema:
            return None
        val = float(compute_ema(closes, period).iloc[-1])
        if px >= val:
            return {
                "signal_level": level_map[key],
                "ema_type": key,
                "ema_value": val,
                "ema_partial": partial,
            }
        return None

    hit = _try(200, int(ema200_min), "EMA200")
    if hit is None:
        hit = _try(50, int(ema50_min), "EMA50")
    return hit
