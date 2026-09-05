"""行情源自动路由

统一规则：
- 美股 quote -> finnhub
- 美股 history/kline -> futu（OpenD request_history_kline）；适配器不可用时 Yahoo
- A股 / 港股 quote -> futu
- A股 / 港股 history/kline -> futu
- options -> futu（港/A）; 美股 auto: OpenD 可达→futu，否则 cboe

说明：
- 这里的 preferred_adapter 只作为兜底/兼容入参，真正的市场路由优先按 symbol + purpose 自动决定。
- 这样可以避免前端 localStorage 里的默认值（如 mock）误导真实行情链路。
- 美股期权：默认 auto = OpenD 可达走富途，否则 CBOE 延时链（不依赖 OpenD）。
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


def is_futu_adapter_available() -> bool:
    """futu-api 可导入即视为 K 线适配器可用；OpenD 连通性由 FutuAdapter.connect 负责。"""
    try:
        import futu  # noqa: F401
        return True
    except Exception:
        return False


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
    """解析历史 K 线来源。美股优先 OpenD，Yahoo 仅作适配器不可用时的回退。"""
    normalized = normalize_symbol(symbol)
    market = infer_market(normalized)

    if market in ('SH', 'SZ', 'HK'):
        return 'futu'
    if market == 'US':
        if is_futu_adapter_available():
            return 'futu'
        return 'yahoo'

    if preferred_adapter == 'futu':
        return 'futu'
    if preferred_adapter == 'yahoo':
        return 'yahoo'
    return 'yahoo'


def resolve_runtime_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """策略实时信号默认跟随 quote 路由。"""
    return resolve_quote_source(symbol, preferred_adapter)


def is_opend_reachable(host: Optional[str] = None, port: Optional[int] = None) -> bool:
    """TCP 探测 OpenD；不通则立刻 False，不 import 阻塞。"""
    try:
        from app.core.opend import ensure_opend_reachable
        from app.core.config import get_effective_config
        futu = (get_effective_config().get("futu") or {}) if host is None or port is None else {}
        h = host or futu.get("host") or "127.0.0.1"
        p = int(port if port is not None else (futu.get("port") or 11111))
        ensure_opend_reachable(h, p, timeout=0.6)
        return True
    except Exception:
        return False


def _options_mode(preferred_adapter: Optional[str] = None, options_source: Optional[str] = None) -> str:
    raw = (options_source or preferred_adapter or "").strip().lower()
    if raw in ("cboe", "futu", "auto"):
        return raw
    try:
        from app.core.config import get_effective_config
        cfg = (get_effective_config().get("options") or {})
        mode = str(cfg.get("source") or "auto").strip().lower()
        if mode in ("cboe", "futu", "auto"):
            return mode
    except Exception:
        pass
    return "auto"


def resolve_option_source(
    symbol: str,
    preferred_adapter: Optional[str] = None,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    options_source: Optional[str] = None,
) -> str:
    """美股期权：auto=OpenD 通走富途否则 CBOE；港/A 仍走富途。"""
    mode = _options_mode(preferred_adapter, options_source)
    if not is_us_symbol(symbol):
        return "futu"
    if mode == "cboe":
        return "cboe"
    if mode == "futu":
        return "futu"
    if is_opend_reachable(host, port):
        return "futu"
    return "cboe"


def option_candidate_sources(symbol: str, source: Optional[str] = None) -> list:
    """美股富途失败时回落 CBOE；港/A 没有公开备援。"""
    src = (source or "futu").strip().lower()
    if is_us_symbol(symbol):
        if src == "futu":
            return ["futu", "cboe"]
        if src == "cboe":
            return ["cboe"]
    return [src or "futu"]


def resolve_display_market(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return infer_market(normalized)
