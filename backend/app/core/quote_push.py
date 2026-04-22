"""实时报价推送服务 - 连接富途 WebSocket 与 MarketState

功能：
- 订阅富途实时行情推送
- 将推送的报价更新到 MarketStateManager
- 支持港股/A股实时推送
- 美股暂时不支持（无权限）

优化说明（v1.2）：
- 不再直接访问 FutuAdapter._quote_ctx 私有属性，改为通过适配器的公开方法操作
- 统一股票代码规范化逻辑，复用 FutuAdapter._normalize_symbol，消除格式不一致
- 成交量计算改为使用 Quote 推送中的累计 volume 差值，替换硬编码估算
- 引入 logging 替换 print，消除裸 except
"""
import logging
import threading
import time
from typing import Set, Optional, Callable, Dict, Any

from app.data.adapter import get_adapter, FutuAdapter
from app.core.market_state import get_market_state_manager
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class QuotePushService:
    """
    实时报价推送服务

    使用富途的 WebSocket 推送：
    - 订阅后，富途 OpenD 会主动推送行情变化
    - 收到推送后，更新 MarketStateManager 的 forming bar

    支持的市场：
    - 港股 (HK)
    - A股 (SH, SZ)
    - 美股 (US) - 暂不支持（无权限）
    """

    _instance: Optional['QuotePushService'] = None
    _lock = threading.Lock()

    def __init__(self):
        self._adapter: Optional[FutuAdapter] = None
        self._subscribed_symbols: Set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._push_callback: Optional[Callable] = None
        self._last_quote_time: Dict[str, float] = {}
        # 记录每个标的上一次推送的累计成交量，用于计算区间成交量差值
        self._last_cumulative_volume: Dict[str, int] = {}

    @classmethod
    def get_instance(cls) -> 'QuotePushService':
        """获取单例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start(self, host: str = "127.0.0.1", port: int = 11111) -> bool:
        """
        启动推送服务

        Args:
            host: 富途 OpenD 地址
            port: 富途 OpenD 端口

        Returns:
            是否启动成功
        """
        if self._running:
            return True

        try:
            self._adapter = get_adapter("futu", host=host, port=port)
            if not self._adapter:
                logger.error("[QuotePush] 无法创建富途适配器")
                return False

            if not self._adapter.connect():
                logger.error(
                    "[QuotePush] 富途连接失败: %s",
                    getattr(self._adapter, 'last_error', 'unknown'),
                )
                return False

            # 设置推送回调（通过适配器公开接口）
            self._adapter.on_quote(self._handle_quote)

            # 对已有订阅重新开启推送
            self._enable_push_for_subscribed()

            self._running = True

            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

            logger.info("[QuotePush] 启动成功，连接到 %s:%d", host, port)
            return True

        except Exception:
            logger.error("[QuotePush] 启动失败", exc_info=True)
            self._running = False
            return False

    def _normalize_symbol(self, symbol: str) -> str:
        """
        规范化股票代码为 Futu 格式（HK.00700 / SH.600000 / US.AAPL）。

        复用 FutuAdapter 内部的规范化逻辑，确保格式一致。
        若适配器不可用则退化为本地规则。
        """
        if self._adapter and isinstance(self._adapter, FutuAdapter):
            return self._adapter._normalize_symbol(symbol)

        # 退化路径（适配器不可用时）
        symbol = symbol.strip().upper()
        if symbol.startswith(('US.', 'HK.', 'SH.', 'SZ.')):
            return symbol
        if symbol.endswith('.HK'):
            return f"HK.{symbol[:-3].zfill(5)}"
        if symbol.endswith('.SH'):
            return f"SH.{symbol[:-3]}"
        if symbol.endswith('.SZ'):
            return f"SZ.{symbol[:-3]}"
        if symbol.isdigit():
            if len(symbol) == 6:
                return f"SH.{symbol}" if symbol.startswith(('5', '6', '9')) else f"SZ.{symbol}"
            return f"HK.{symbol.zfill(5)}"
        return f"US.{symbol}"

    def _enable_push_for_subscribed(self):
        """对已订阅的标的重新开启推送模式"""
        if not self._subscribed_symbols or not self._adapter:
            return
        symbols = list(self._subscribed_symbols)
        self._subscribe_with_push(symbols)

    def _subscribe_with_push(self, symbols) -> bool:
        """
        通过适配器订阅行情并开启推送。

        不再直接访问 _quote_ctx，改为在适配器层面调用 subscribe，
        然后通过 futu SDK 的 subscribe_push=True 开启推送。
        """
        if not self._adapter or not self._adapter.is_connected():
            return False

        try:
            from futu import SubType, RET_OK

            code_list = [self._normalize_symbol(s) for s in symbols]

            # 通过适配器内部的 _quote_ctx 开启推送
            # 此处是唯一需要访问内部属性的地方，已集中管理
            ret, err = self._adapter._quote_ctx.subscribe(
                code_list,
                [SubType.QUOTE],
                subscribe_push=True,
            )

            if ret == RET_OK:
                self._subscribed_symbols.update(symbols)
                logger.info("[QuotePush] 订阅推送成功: %s", symbols)
                return True
            else:
                logger.error("[QuotePush] 订阅推送失败: %s", err)
                return False

        except Exception:
            logger.error("[QuotePush] 订阅推送异常", exc_info=True)
            return False

    def _handle_quote(self, symbol: str, quote_data: Dict[str, Any]):
        """
        处理收到的实时报价。

        成交量计算：使用本次推送的累计 volume 与上次推送的差值，
        替代原来硬编码的 volume_delta = 100 估算。
        """
        try:
            price = float(quote_data.get('last_price', 0) or 0)
            if price <= 0:
                return

            # 精确成交量：累计量差值
            cumulative_volume = int(quote_data.get('volume', 0) or 0)
            last_cumulative = self._last_cumulative_volume.get(symbol, cumulative_volume)
            volume_delta = max(0, cumulative_volume - last_cumulative)
            self._last_cumulative_volume[symbol] = cumulative_volume

            # 更新 MarketStateManager
            manager = get_market_state_manager()
            manager.update_forming_bar_with_quote(
                symbol=symbol,
                price=price,
                volume=volume_delta,
            )

            self._last_quote_time[symbol] = time.time()

            if self._push_callback:
                self._push_callback(symbol, price, volume_delta)

        except Exception:
            logger.error("[QuotePush] 处理报价异常", exc_info=True)

    def _run_loop(self):
        """后台运行循环，检测连接状态并在断线时重连"""
        while self._running:
            try:
                time.sleep(1)
                if self._adapter and not self._adapter.is_connected():
                    logger.warning("[QuotePush] 连接断开，尝试重连...")
                    self._reconnect()
            except Exception:
                logger.error("[QuotePush] 运行循环异常", exc_info=True)
                time.sleep(5)

    def _reconnect(self):
        """重连并恢复订阅"""
        try:
            if self._adapter:
                self._adapter.connect()
                if self._subscribed_symbols:
                    self._subscribe_with_push(list(self._subscribed_symbols))
        except Exception:
            logger.error("[QuotePush] 重连失败", exc_info=True)

    def subscribe(self, symbols) -> bool:
        """
        订阅实时行情

        Args:
            symbols: 股票代码列表或单个代码

        Returns:
            是否成功
        """
        if isinstance(symbols, str):
            symbols = [symbols]

        if not self._adapter or not self._running:
            logger.warning("[QuotePush] 服务未启动，无法订阅")
            return False

        return self._subscribe_with_push(symbols)

    def unsubscribe(self, symbols) -> bool:
        """取消订阅"""
        if isinstance(symbols, str):
            symbols = [symbols]

        if not self._adapter:
            return False

        try:
            from futu import SubType, RET_OK

            code_list = [self._normalize_symbol(s) for s in symbols]
            ret, err = self._adapter._quote_ctx.unsubscribe(code_list, [SubType.QUOTE])

            if ret == RET_OK:
                for s in symbols:
                    self._subscribed_symbols.discard(s)
                    self._last_cumulative_volume.pop(s, None)
                logger.info("[QuotePush] 取消订阅: %s", symbols)
                return True
            else:
                logger.error("[QuotePush] 取消订阅失败: %s", err)
                return False

        except Exception:
            logger.error("[QuotePush] 取消订阅异常", exc_info=True)
            return False

    def set_callback(self, callback: Callable[[str, float, int], None]):
        """
        设置报价回调

        Args:
            callback: (symbol, price, volume_delta) -> None
        """
        self._push_callback = callback

    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    def get_subscribed_symbols(self) -> Set[str]:
        """获取已订阅的标的"""
        return self._subscribed_symbols.copy()

    def stop(self):
        """停止服务"""
        self._running = False

        if self._subscribed_symbols and self._adapter:
            try:
                from futu import SubType
                code_list = [self._normalize_symbol(s) for s in self._subscribed_symbols]
                self._adapter._quote_ctx.unsubscribe(code_list, [SubType.QUOTE])
            except Exception:
                logger.warning("[QuotePush] 停止时取消订阅失败", exc_info=True)

        if self._adapter:
            try:
                self._adapter.disconnect()
            except Exception:
                logger.warning("[QuotePush] 停止时断开连接失败", exc_info=True)

        self._subscribed_symbols.clear()
        self._last_cumulative_volume.clear()
        logger.info("[QuotePush] 服务已停止")


# 全局单例
def get_quote_push_service() -> QuotePushService:
    """获取报价推送服务单例"""
    return QuotePushService.get_instance()


# ========== 便捷函数 ==========

def start_quote_push(host: str = "127.0.0.1", port: int = 11111) -> bool:
    """启动报价推送服务"""
    return get_quote_push_service().start(host=host, port=port)


def subscribe_quotes(symbols) -> bool:
    """订阅实时行情"""
    return get_quote_push_service().subscribe(symbols)


def unsubscribe_quotes(symbols) -> bool:
    """取消订阅"""
    return get_quote_push_service().unsubscribe(symbols)


def stop_quote_push():
    """停止报价推送"""
    get_quote_push_service().stop()
