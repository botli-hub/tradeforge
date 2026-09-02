"""Futu OpenD 历史 K 线：request_history_kline 分页，不依赖 get_cur_kline。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 应用使用的周期 → OpenD KLType 名
KLTYPE_NAMES = {
    "1m": "K_1M",
    "5m": "K_5M",
    "15m": "K_15M",
    "30m": "K_30M",
    "60m": "K_60M",
    "1h": "K_60M",
    "4h": "K_240M",
    "1d": "K_DAY",
    "1w": "K_WEEK",
    "1M": "K_MON",
}

MAX_COUNT = 1000
MAX_PAGES = 20


def to_futu_time(value: str) -> str:
    """ISO / 空格时间戳 → OpenD start/end（yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss）。"""
    text = str(value or "").strip()
    if not text:
        return text
    if text.endswith("Z"):
        text = text[:-1]
    text = text.replace("T", " ")
    if "+" in text[10:]:
        text = text.split("+", 1)[0]
    if "." in text:
        text = text.split(".", 1)[0]
    return text.strip()[:19]


def resolve_kl_type(timeframe: str):
    name = KLTYPE_NAMES.get(timeframe, "K_DAY")
    try:
        from futu import KLType
        return getattr(KLType, name)
    except Exception:
        return name


def resolve_autype():
    try:
        from futu import AuType
        return AuType.QFQ
    except Exception:
        return "QFQ"


def _ret_ok() -> int:
    try:
        from futu import RET_OK
        return RET_OK
    except Exception:
        return 0


def _rows_from_frame(data) -> List[Dict[str, Any]]:
    if data is None:
        return []
    try:
        if len(data) == 0:
            return []
    except TypeError:
        return []
    rows: List[Dict[str, Any]] = []
    iterator = data.iterrows() if hasattr(data, "iterrows") else enumerate(data)
    for _, row in iterator:
        getter = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
        time_key = getter("time_key")
        if time_key is None:
            continue
        try:
            rows.append({
                "timestamp": str(time_key),
                "open": float(getter("open") or 0),
                "high": float(getter("high") or 0),
                "low": float(getter("low") or 0),
                "close": float(getter("close") or 0),
                "volume": int(getter("volume") or 0),
            })
        except (TypeError, ValueError):
            continue
    return rows


def paginate_request_history_kline(
    request_fn,
    *,
    code: str,
    start: str,
    end: str,
    ktype,
    autype,
    max_count: int = MAX_COUNT,
    max_pages: int = MAX_PAGES,
    ret_ok: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """循环 page_req_key，直到没有下一页或达到 max_pages。

    request_fn 签名与 QuoteContext.request_history_kline 一致，返回
    (ret, data, page_req_key)。
    """
    ok = _ret_ok() if ret_ok is None else ret_ok
    page_req_key = None
    collected: List[Dict[str, Any]] = []

    for page in range(max(1, max_pages)):
        ret, data, page_req_key = request_fn(
            code,
            start=start,
            end=end,
            ktype=ktype,
            autype=autype,
            max_count=max_count,
            page_req_key=page_req_key,
        )
        if ret != ok:
            err = data if isinstance(data, str) else str(data)
            return collected, f"request_history_kline 失败: {err}"
        collected.extend(_rows_from_frame(data))
        if page_req_key is None:
            break
        if page == max_pages - 1:
            logger.warning("request_history_kline 达到最大分页 %s，可能仍有剩余", max_pages)

    return collected, None


def fetch_history_bars(
    quote_ctx,
    *,
    code: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    max_count: int = MAX_COUNT,
    max_pages: int = MAX_PAGES,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """对 OpenQuoteContext 调用 request_history_kline 并分页拼接。"""
    if quote_ctx is None or not hasattr(quote_ctx, "request_history_kline"):
        return [], "OpenD quote context 无 request_history_kline"
    start = to_futu_time(start_date)
    end = to_futu_time(end_date)
    ktype = resolve_kl_type(timeframe)
    autype = resolve_autype()
    return paginate_request_history_kline(
        quote_ctx.request_history_kline,
        code=code,
        start=start,
        end=end,
        ktype=ktype,
        autype=autype,
        max_count=max_count,
        max_pages=max_pages,
    )
