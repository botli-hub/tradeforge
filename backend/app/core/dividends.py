"""除息日历(Finnhub)。美股;无 key 或失败返回 None。

用于 CC 提前行权风险提示。
"""
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CACHE: dict = {}  # symbol -> (expires, list|None)
_TTL = 12 * 3600


def dividend_lookup(symbol: str) -> Dict[str, Any]:
    """与财报相同:unknown 不是「没有除息」。"""
    symbol = (symbol or "").strip().upper()
    if not symbol or symbol.endswith(".HK") or symbol.endswith(".SH") or symbol.endswith(".SZ"):
        return {"date": None, "amount": None, "status": "unsupported", "reason": "非美股"}
    try:
        from app.core.config import get_effective_config
        api_key = (get_effective_config().get("finnhub_api_key") or "").strip()
    except Exception:
        api_key = ""
    if not api_key:
        return {"date": None, "amount": None, "status": "unknown", "reason": "未配置 Finnhub"}
    items = get_dividends(symbol)
    # get_dividends 在请求失败时也返回空列表;用缓存是否写入区分 none / unknown
    hit = _CACHE.get(symbol)
    if hit and hit[1] is None:
        return {"date": None, "amount": None, "status": "unknown", "reason": "请求失败"}
    if not items:
        return {"date": None, "amount": None, "status": "none", "reason": "未来窗口无除息"}
    first = items[0]
    return {
        "date": first.get("date"),
        "amount": first.get("amount"),
        "status": "ok",
        "reason": "finnhub",
    }


def get_next_dividend(symbol: str) -> Optional[Dict[str, Any]]:
    """返回最近未来除息信息 {date, amount} 或 None。"""
    info = dividend_lookup(symbol)
    if info.get("status") != "ok":
        return None
    return {"date": info.get("date"), "amount": info.get("amount")}


def get_dividends(symbol: str, days: int = 90) -> List[Dict[str, Any]]:
    symbol = symbol.strip().upper()
    if symbol.endswith(".HK") or symbol.endswith(".SH") or symbol.endswith(".SZ"):
        return []
    now = time.monotonic()
    hit = _CACHE.get(symbol)
    if hit and hit[0] > now:
        return hit[1] or []

    result: List[Dict[str, Any]] = []
    failed = False
    try:
        from app.core.config import get_effective_config
        import httpx

        effective = get_effective_config()
        api_key = (effective.get("finnhub_api_key") or "").strip()
        base_url = (effective.get("finnhub_base_url") or "https://finnhub.io/api/v1").rstrip("/")
        if not api_key:
            failed = True
        else:
            resp = httpx.get(
                f"{base_url}/stock/dividend",
                params={
                    "symbol": symbol,
                    "from": date.today().isoformat(),
                    "to": (date.today() + timedelta(days=days)).isoformat(),
                    "token": api_key,
                },
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json() or []
                if isinstance(data, list):
                    for d in data:
                        dt = d.get("date") or d.get("exDate") or d.get("payDate")
                        if not dt:
                            continue
                        result.append({
                            "date": str(dt)[:10],
                            "amount": d.get("amount"),
                            "adjustedAmount": d.get("adjustedAmount"),
                        })
                    result.sort(key=lambda x: x["date"])
            else:
                logger.info("finnhub dividend(%s): HTTP %s", symbol, resp.status_code)
                failed = True
    except Exception as e:
        logger.info("dividend(%s) 失败: %s", symbol, e)
        failed = True

    ttl = 120 if failed else _TTL
    _CACHE[symbol] = (now + ttl, None if failed else result)
    return [] if failed else result


def dividend_warn(symbol: str, warn_days: int = 14) -> Optional[Dict[str, Any]]:
    d = get_next_dividend(symbol)
    if not d:
        return None
    try:
        days = (date.fromisoformat(d["date"]) - date.today()).days
    except Exception:
        return None
    if days < 0 or days > warn_days:
        return None
    return {**d, "days_to_ex": days, "warn": True}
