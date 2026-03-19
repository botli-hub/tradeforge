"""实时报价推送服务 - 连接富途WebSocket与MarketState

功能：
- 订阅富途实时行情推送
- 将推送的报价更新到 MarketStateManager
- 支持港股/A股实时推送
- 美股暂时不支持（无权限）

使用方式：
1. 启动服务：QuotePushService.start()
2. 订阅标的：QuotePushService.subscribe("00700.HK")
3. 取消订阅：QuotePushService.unsubscribe("00700.HK")
4. 停止服务：QuotePushService.stop()

注意：
- 目前仅支持港股和A股（通过富途）
- 美股需要等权限开放后才能使用
"""
import threading
import queue
import time
from typing import Set, Optional, Callable, Dict, Any
from datetime import datetime

from app.data.adapter import get_adapter, FutuAdapter
from app.core.market_state import get_market_state_manager, MarketStateManager
from app.core.config import get_settings


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
            # 创建富途适配器
            self._adapter = get_adapter("futu", host=host, port=port)
            if not self._adapter:
                print("[QuotePush] 无法创建富途适配器")
                return False
                
            # 连接
            if hasattr(self._adapter, 'connect'):
                if not self._adapter.connect():
                    print(f"[QuotePush] 富途连接失败: {getattr(self._adapter, 'last_error', 'unknown')}")
                    return False
            
            # 设置推送回调
            self._adapter.on_quote(self._handle_quote)
            
            # 开启推送模式
            self._enable_push()
            
            self._running = True
            
            # 启动后台线程（用于保持连接和处理）
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            
            print(f"[QuotePush] 启动成功，连接到 {host}:{port}")
            return True
            
        except Exception as e:
            print(f"[QuotePush] 启动失败: {e}")
            self._running = False
            return False
    
    def _enable_push(self):
        """开启富途行情推送"""
        if not self._adapter:
            return
            
        try:
            # 获取所有已订阅标的并重新订阅，开启推送
            symbols = list(self._subscribed_symbols)
            if symbols:
                # 需要重新订阅并开启推送
                from futu import SubType, RET_OK
                
                # 规范化代码
                code_list = []
                for sym in symbols:
                    code = self._normalize_symbol(sym)
                    code_list.append(code)
                
                # 开启推送订阅
                ret, err = self._adapter._quote_ctx.subscribe(
                    code_list, 
                    [SubType.QUOTE],  # 实时报价
                    subscribe_push=True  # 开启推送！
                )
                
                if ret == RET_OK:
                    print(f"[QuotePush] 开启推送成功: {symbols}")
                else:
                    print(f"[QuotePush] 开启推送失败: {err}")
                    
        except Exception as e:
            print(f"[QuotePush] 开启推送异常: {e}")
    
    def _normalize_symbol(self, symbol: str) -> str:
        """规范化股票代码"""
        symbol = symbol.strip().upper()
        # 已经包含市场的直接返回
        if '.' in symbol:
            return symbol
        # 港股
        if symbol.isdigit() and len(symbol) == 5:
            return f"HK.{symbol}"
        # A股
        if symbol.startswith(('600', '601', '603', '605', '688')):
            return f"SH.{symbol}"
        if symbol.startswith(('000', '001', '002', '003', '300', '301')):
            return f"SZ.{symbol}"
        # 美股
        return f"US.{symbol}"
    
    def _handle_quote(self, symbol: str, quote_data: Dict[str, Any]):
        """处理收到的实时报价"""
        try:
            price = float(quote_data.get('last_price', 0) or 0)
            volume = int(quote_data.get('volume', 0) or 0)
            
            if price <= 0:
                return
                
            # 更新 MarketStateManager
            manager = get_market_state_manager()
            manager.update_forming_bar_with_quote(
                symbol=symbol,
                price=price,
                volume=volume,
            )
            
            # 记录时间
            self._last_quote_time[symbol] = time.time()
            
            # 触发回调（如果有）
            if self._push_callback:
                self._push_callback(symbol, price, volume)
                
        except Exception as e:
            print(f"[QuotePush] 处理报价异常: {e}")
    
    def _run_loop(self):
        """后台运行循环"""
        while self._running:
            try:
                time.sleep(1)
                
                # 检查连接状态
                if self._adapter and hasattr(self._adapter, 'is_connected'):
                    if not self._adapter.is_connected():
                        print("[QuotePush] 连接断开，尝试重连...")
                        self._reconnect()
                        
            except Exception as e:
                print(f"[QuotePush] 运行循环异常: {e}")
                time.sleep(5)
    
    def _reconnect(self):
        """重连"""
        try:
            if self._adapter and hasattr(self._adapter, 'connect'):
                self._adapter.connect()
                # 重新订阅
                if self._subscribed_symbols:
                    self.subscribe(list(self._subscribed_symbols))
        except Exception as e:
            print(f"[QuotePush] 重连失败: {e}")
    
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
            print("[QuotePush] 服务未启动")
            return False
            
        try:
            from futu import SubType, RET_OK
            
            # 规范化
            code_list = [self._normalize_symbol(s) for s in symbols]
            
            # 订阅并开启推送
            ret, err = self._adapter._quote_ctx.subscribe(
                code_list,
                [SubType.QUOTE],
                subscribe_push=True  # 开启推送！
            )
            
            if ret == RET_OK:
                self._subscribed_symbols.update(symbols)
                print(f"[QuotePush] 订阅成功: {symbols}")
                return True
            else:
                print(f"[QuotePush] 订阅失败: {err}")
                return False
                
        except Exception as e:
            print(f"[QuotePush] 订阅异常: {e}")
            return False
    
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
                print(f"[QuotePush] 取消订阅: {symbols}")
                return True
            return False
            
        except Exception as e:
            print(f"[QuotePush] 取消订阅异常: {e}")
            return False
    
    def set_callback(self, callback: Callable[[str, float, int], None]):
        """
        设置报价回调
        
        Args:
            callback: (symbol, price, volume) -> None
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
        
        # 取消所有订阅
        if self._subscribed_symbols and self._adapter:
            try:
                from futu import SubType
                code_list = [self._normalize_symbol(s) for s in self._subscribed_symbols]
                self._adapter._quote_ctx.unsubscribe(code_list, [SubType.QUOTE])
            except:
                pass
        
        # 断开连接
        if self._adapter and hasattr(self._adapter, 'disconnect'):
            try:
                self._adapter.disconnect()
            except:
                pass
                
        self._subscribed_symbols.clear()
        print("[QuotePush] 服务已停止")


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
