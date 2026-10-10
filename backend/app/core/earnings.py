"""财报日历(Finnhub,内存缓存 12 小时)。仅支持美股;无 API key 或港股返回 None"""
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: dict = {}  # symbol -> (expires_monotonic, {"date","status","reason"})
_TTL = 12 * 3600
_TTL_UNKNOWN = 120  # 未知只短缓存,避免把缺数据当「无财报」存 12 小时


def _payload(d: Optional[str], status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    return {"date": d, "status": status, "reason": reason}


def get_earnings_status(symbol: str) -> Dict[str, Any]:
    """{date, status, reason}。status:
    ok=有日期; none=已成功查询且 90 天内无财报; unknown=无 key/请求失败(不能当作没有财报);
    unsupported=港股等不支持市场。
    """
    symbol = symbol.strip().upper()
    if symbol.endswith(".HK"):
        return _payload(None, "unsupported", "港股不支持")
    now = time.monotonic()
    hit = _CACHE.get(symbol)
    if hit and hit[0] > now:
        return dict(hit[1])

    payload = _payload(None, "unknown", "未配置 Finnhub")
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
                payload = _payload(dates[0], "ok") if dates else _payload(None, "none")
            else:
                logger.info("finnhub earnings(%s): HTTP %s", symbol, resp.status_code)
                payload = _payload(None, "unknown", f"HTTP {resp.status_code}")
    except Exception as e:
        logger.info("earnings(%s) 获取失败: %s", symbol, e)
        payload = _payload(None, "unknown", "请求失败")

    ttl = _TTL_UNKNOWN if payload["status"] == "unknown" else _TTL
    _CACHE[symbol] = (now + ttl, payload)
    return dict(payload)


def get_next_earnings(symbol: str) -> Optional[str]:
    """返回未来 90 天内最近的财报日 YYYY-MM-DD,无/未知则 None。
    要区分「没有」与「未知」请用 get_earnings_status。"""
    return get_earnings_status(symbol).get("date")


def days_to_earnings(symbol: str) -> Optional[int]:
    d = get_next_earnings(symbol)
    if not d:
        return None
    try:
        return (date.fromisoformat(d) - date.today()).days
    except Exception:
        return None
