"""财报日历(Finnhub,内存缓存 12 小时)。仅支持美股;无 API key 或港股返回 None"""
import logging
import time
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE: dict = {}  # symbol -> (expires_monotonic, date_str | None)
_TTL = 12 * 3600


def get_next_earnings(symbol: str) -> Optional[str]:
    """返回未来 90 天内最近的财报日 YYYY-MM-DD,无则 None"""
    symbol = symbol.strip().upper()
    if symbol.endswith(".HK"):
        return None
    now = time.monotonic()
    hit = _CACHE.get(symbol)
    if hit and hit[0] > now:
        return hit[1]

    result: Optional[str] = None
    try:
        # 配置统一来自设置页保存的本地数据库(app.core.config.get_effective_config)
        from app.core.config import get_effective_config
        effective = get_effective_config()
        api_key = (effective.get("finnhub_api_key") or "").strip()
        base_url = (effective.get("finnhub_base_url") or "https://finnhub.io/api/v1").rstrip("/")
        if api_key:
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
                dates = sorted(i.get("date") for i in items if i.get("date"))
                result = dates[0] if dates else None
            else:
                logger.info("finnhub earnings(%s): HTTP %s", symbol, resp.status_code)
    except Exception as e:
        logger.info("earnings(%s) 获取失败: %s", symbol, e)

    _CACHE[symbol] = (now + _TTL, result)
    return result


def days_to_earnings(symbol: str) -> Optional[int]:
    d = get_next_earnings(symbol)
    if not d:
        return None
    try:
        return (date.fromisoformat(d) - date.today()).days
    except Exception:
        return None
