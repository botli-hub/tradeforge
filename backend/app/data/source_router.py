"""行情源自动路由

统一规则：
- 美股 quote -> finnhub
- 美股 history/kline -> yahoo
- A股 / 港股 quote -> futu
- A股 / 港股 history/kline -> futu
- options -> futu

说明：
- 这里的 preferred_adapter 只作为兜底/兼容入参，真正的市场路由优先按 symbol + purpose 自动决定。
- 这样可以避免前端 localStorage 里的默认值（如 mock）误导真实行情链路。
"""
from __future__ import annotations

from typing import Optional

from app.data.history_repository import infer_market


def normalize_cn_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith('.SH') or symbol.endswith('.SZ'):
        return symbol
    if symbol.isdigit() and len(symbol) == 6:
        return f"{symbol}.SH" if symbol.startswith(('5', '6', '9')) else f"{symbol}.SZ"
    return symbol


def normalize_hk_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith('.HK'):
        return f"{symbol[:-3].zfill(5)}.HK"
    if symbol.isdigit() and len(symbol) <= 5:
        return f"{symbol.zfill(5)}.HK"
    return symbol


def is_cn_symbol(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    return symbol.endswith('.SH') or symbol.endswith('.SZ') or (symbol.isdigit() and len(symbol) == 6)


def is_hk_symbol(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    return symbol.endswith('.HK') or (symbol.isdigit() and len(symbol) <= 5)


def is_us_symbol(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    return symbol.startswith('US.') or ('.' not in symbol and not symbol.isdigit())


def normalize_symbol(symbol: str) -> str:
    if is_cn_symbol(symbol):
        return normalize_cn_symbol(symbol)
    if is_hk_symbol(symbol):
        return normalize_hk_symbol(symbol)
    return symbol.strip().upper()


_INVALID_ADAPTERS = {'mock', 'auto', None, ''}


def resolve_quote_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """解析实时报价来源。"""
    normalized = normalize_symbol(symbol)
    market = infer_market(normalized)

    if market in ('SH', 'SZ', 'HK'):
        return 'futu'
    if market == 'US':
        return 'finnhub'

    # 只接受真实适配器作为偏好
    if preferred_adapter not in _INVALID_ADAPTERS:
        return preferred_adapter
    return 'finnhub'


def resolve_kline_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """解析历史 K 线来源。"""
    normalized = normalize_symbol(symbol)
    market = infer_market(normalized)

    if market in ('SH', 'SZ', 'HK'):
        return 'futu'
    if market == 'US':
        return 'yahoo'

    if preferred_adapter == 'futu':
        return 'futu'
    if preferred_adapter == 'yahoo':
        return 'yahoo'
    return 'yahoo'


def resolve_runtime_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """策略实时信号默认跟随 quote 路由。"""
    return resolve_quote_source(symbol, preferred_adapter)


def resolve_option_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """期权固定走 Futu。"""
    return 'futu'


def resolve_display_market(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return infer_market(normalized)
