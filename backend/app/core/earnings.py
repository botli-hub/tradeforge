"""财报日历(Finnhub)。仅支持美股。

无 key、请求失败与「未来 90 天没有财报」分开:
unknown 只用短缓存,避免把缺失数据当成「没有财报」存 12 小时。
get_next_earnings 仍只返回日期或 None,调用方用 earnings_lookup 看状态。
"""
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: dict = {}  # symbol -> (expires_monotonic, payload)
_TTL_OK = 12 * 3600
_TTL_UNKNOWN = 120


def _payload(date_str: Optional[str], status: str, reason: str, hour: Optional[str] = None) -> Dict[str, Any]:
    return {
        "date": date_str,
        "status": status,
        "reason": reason,
        "hour": hour,
    }


def earnings_lookup(symbol: str) -> Dict[str, Any]:
    """{date, status: ok|none|unknown|unsupported, reason, hour}。"""
    symbol = (symbol or "").strip().upper()
    if not symbol or symbol.endswith(".HK") or symbol.endswith(".SH") or symbol.endswith(".SZ"):
        return _payload(None, "unsupported", "非美股")
    now = time.monotonic()
    hit = _CACHE.get(symbol)
    if hit and hit[0] > now:
        return dict(hit[1])

    payload = _payload(None, "unknown", "未配置 Finnhub")
    ttl = _TTL_UNKNOWN
    try:
        from app.core.config import get_effective_config
        effective = get_effective_config()
        api_key = (effective.get("finnhub_api_key") or "").strip()
        base_url = (effective.get("finnhub_base_url") or "https://finnhub.io/api/v1").rstrip("/")
        if not api_key:
            payload = _payload(None, "unknown", "未配置 Finnhub")
        else:
            import httpx
            resp = httpx.get(
                f"{base_url}/calendar/earnings",
                params={
                    "from": date.today().isoformat(),
                    "to": (date.today() + timedelta(days=90)).isoformat(),
                    "symbol": symbol,
                    "token": api_key,
                },
                timeout=8,
            )
            if resp.status_code == 200:
                items = (resp.json() or {}).get("earningsCalendar") or []
                dated = [i for i in items if i.get("date")]
                dated.sort(key=lambda i: i.get("date"))
                if dated:
                    hour = dated[0].get("hour")
                    payload = _payload(str(dated[0]["date"])[:10], "ok", "finnhub", hour=hour)
                    ttl = _TTL_OK
                else:
                    payload = _payload(None, "none", "未来90天无财报")
                    ttl = _TTL_OK
            else:
                logger.info("finnhub earnings(%s): HTTP %s", symbol, resp.status_code)
                payload = _payload(None, "unknown", f"HTTP {resp.status_code}")
    except Exception as e:
        logger.info("earnings(%s) 获取失败: %s", symbol, e)
        payload = _payload(None, "unknown", "请求失败")

    _CACHE[symbol] = (now + ttl, payload)
    return dict(payload)


def get_next_earnings(symbol: str) -> Optional[str]:
    """返回未来 90 天内最近的财报日 YYYY-MM-DD;没有或未知都是 None。"""
    info = earnings_lookup(symbol)
    if info.get("status") == "ok":
        return info.get("date")
    return None


def days_to_earnings(symbol: str) -> Optional[int]:
    d = get_next_earnings(symbol)
    if not d:
        return None
    try:
        return (date.fromisoformat(d) - date.today()).days
    except Exception:
        return None
