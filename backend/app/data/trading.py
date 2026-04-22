"""富途交易适配器 - 委托/持仓

优化说明（v1.2）：
- 修复 query_order 中 TrdSide 未导入导致的 NameError
- 统一 query_orders 中 side 比较逻辑，改为枚举而非字符串
- 所有方法改用 logging 替换 print，并记录完整异常堆栈
- 补全 STOP 订单类型的映射（映射为 Futu STOP 类型）
"""
import logging
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """订单状态"""
    SUBMITTED = "SUBMITTED"   # 已提交
    FILLED = "FILLED"         # 全部成交
    PARTIAL = "PARTIAL"       # 部分成交
    CANCELLED = "CANCELLED"   # 已撤单
    FAILED = "FAILED"         # 失败


class OrderSide(Enum):
    """交易方向"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"  # 市价
    LIMIT = "LIMIT"    # 限价
    STOP = "STOP"      # 止损


@dataclass
class Order:
    """订单"""
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    filled_quantity: float
    status: OrderStatus
    order_type: OrderType
    create_time: str
    update_time: str
    message: str


@dataclass
class Position:
    """持仓"""
    symbol: str
    direction: OrderSide
    quantity: float
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float


class TradingAdapter:
    """交易适配器接口"""

    def place_order(self, symbol: str, side: OrderSide,
                    quantity: float, price: float = 0,
                    order_type: OrderType = OrderType.LIMIT) -> str:
        """下单，返回订单ID"""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        ...

    def query_order(self, order_id: str) -> Optional[Order]:
        """查询订单"""
        ...

    def query_orders(self, status: OrderStatus = None) -> List[Order]:
        """查询订单列表"""
        ...

    def query_positions(self) -> List[Position]:
        """查询持仓"""
        ...

    def query_account(self) -> dict:
        """查询账户信息"""
        ...


class FutuTradingAdapter:
    """富途交易适配器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        self.host = host
        self.port = port
        self._connected = False
        self._trd_env = "SIM"  # SIM/REAL

    def connect(self, trd_env: str = "SIM") -> bool:
        """连接富途交易"""
        try:
            from futu import OpenDCluster
            self._trd_env = trd_env
            self._opend = OpenDCluster(self.host, self.port)
            self._opend.start()
            self._trade = self._opend.trade
            self._connected = True
            return True
        except ImportError:
            logger.error("futu-api 未安装，无法连接交易账户")
            return False
        except Exception:
            logger.error("连接富途交易账户失败", exc_info=True)
            return False

    def disconnect(self):
        """断开连接"""
        if hasattr(self, '_opend'):
            try:
                self._opend.stop()
            except Exception:
                logger.warning("断开富途连接时发生异常", exc_info=True)
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _map_order_type(self, order_type: OrderType):
        """将内部 OrderType 映射到 Futu OrderType"""
        from futu import OrderType as FutuOrderType
        mapping = {
            OrderType.MARKET: FutuOrderType.MARKET,
            OrderType.LIMIT: FutuOrderType.LIMIT,
            OrderType.STOP: FutuOrderType.STOP,
        }
        return mapping.get(order_type, FutuOrderType.LIMIT)

    def place_order(self, symbol: str, side: OrderSide,
                    quantity: float, price: float = 0,
                    order_type: OrderType = OrderType.LIMIT) -> str:
        """下单"""
        if not self._connected:
            logger.warning("place_order 调用失败：未连接交易账户")
            return ""

        try:
            from futu import TrdSide as FutuTrdSide

            futu_side = FutuTrdSide.BUY if side == OrderSide.BUY else FutuTrdSide.SELL
            futu_otype = self._map_order_type(order_type)

            ret, data = self._trade.place_order(
                code=symbol,
                price=price,
                qty=quantity,
                side=futu_side,
                order_type=futu_otype,
            )

            if ret == 0:
                return str(data.iloc[0]['order_id'])
            else:
                logger.error("下单失败: %s", data)
                return ""

        except Exception:
            logger.error("下单异常", exc_info=True)
            return ""

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if not self._connected:
            logger.warning("cancel_order 调用失败：未连接交易账户")
            return False

        try:
            ret, data = self._trade.cancel_order(order_id)
            if ret != 0:
                logger.error("撤单失败: %s", data)
            return ret == 0
        except Exception:
            logger.error("撤单异常", exc_info=True)
            return False

    def query_order(self, order_id: str) -> Optional[Order]:
        """查询单个订单"""
        if not self._connected:
            logger.warning("query_order 调用失败：未连接交易账户")
            return None

        try:
            # 在方法内部导入所有需要的 Futu 枚举，避免 NameError
            from futu import TrdSide as FutuTrdSide

            ret, data = self._trade.order_list_query(order_id=order_id)
            if ret == 0 and len(data) > 0:
                row = data.iloc[0]
                # 使用枚举比较，与 query_orders 保持一致
                side = (
                    OrderSide.BUY
                    if row['side'] == FutuTrdSide.BUY
                    else OrderSide.SELL
                )
                return Order(
                    order_id=str(row['order_id']),
                    symbol=row['code'],
                    side=side,
                    price=float(row['price']),
                    quantity=float(row['qty']),
                    filled_quantity=float(row['dealt_qty']),
                    status=self._parse_status(row['status']),
                    order_type=OrderType.LIMIT,
                    create_time=row['create_time'],
                    update_time=row['update_time'],
                    message=row.get('msg', ''),
                )
            logger.warning("query_order 未找到订单 %s", order_id)
        except Exception:
            logger.error("查询订单失败", exc_info=True)

        return None

    def query_orders(self, status: OrderStatus = None) -> List[Order]:
        """查询订单列表"""
        if not self._connected:
            logger.warning("query_orders 调用失败：未连接交易账户")
            return []

        try:
            from futu import TrdSide as FutuTrdSide

            ret, data = self._trade.order_list_query()
            if ret == 0:
                orders = []
                for _, row in data.iterrows():
                    # 统一使用枚举比较，消除字符串 'Buy' 的不一致用法
                    side = (
                        OrderSide.BUY
                        if row['side'] == FutuTrdSide.BUY
                        else OrderSide.SELL
                    )
                    orders.append(Order(
                        order_id=str(row['order_id']),
                        symbol=row['code'],
                        side=side,
                        price=float(row['price']),
                        quantity=float(row['qty']),
                        filled_quantity=float(row['dealt_qty']),
                        status=self._parse_status(row['status']),
                        order_type=OrderType.LIMIT,
                        create_time=row['create_time'],
                        update_time=row['update_time'],
                        message=row.get('msg', ''),
                    ))
                return orders
            else:
                logger.error("查询订单列表失败: %s", data)
        except Exception:
            logger.error("查询订单列表异常", exc_info=True)

        return []

    def query_positions(self) -> List[Position]:
        """查询持仓"""
        if not self._connected:
            logger.warning("query_positions 调用失败：未连接交易账户")
            return []

        try:
            ret, data = self._trade.position_list_query()
            if ret == 0:
                positions = []
                for _, row in data.iterrows():
                    current_cost = float(row['cost'])
                    pl_ratio = float(row['pl_ratio']) if row['pl_ratio'] is not None else 0.0
                    positions.append(Position(
                        symbol=row['code'],
                        direction=OrderSide.BUY if float(row['qty']) > 0 else OrderSide.SELL,
                        quantity=abs(float(row['qty'])),
                        avg_cost=current_cost,
                        current_price=current_cost * (1 + pl_ratio),
                        unrealized_pnl=float(row['pl']),
                        realized_pnl=0,
                    ))
                return positions
            else:
                logger.error("查询持仓失败: %s", data)
        except Exception:
            logger.error("查询持仓异常", exc_info=True)

        return []

    def query_account(self) -> dict:
        """查询账户"""
        if not self._connected:
            logger.warning("query_account 调用失败：未连接交易账户")
            return {}

        try:
            ret, data = self._trade.accinfo_query()
            if ret == 0 and len(data) > 0:
                row = data.iloc[0] if hasattr(data, 'iloc') else data[0]

                # futu 返回字段名是 market_val，不是 market_value。
                # 这里做兼容兜底，避免因为字段名不一致导致持仓市值缺失。
                market_value = row.get('market_val', row.get('market_value', 0.0))
                cash = row.get('cash', 0.0)
                buying_power = row.get('buying_power', row.get('power', 0.0))
                total_assets = row.get('total_assets')

                # total_assets 优先使用券商原始返回；若缺失则退化为 cash + market_value。
                if total_assets is None:
                    total_assets = float(cash) + float(market_value)

                return {
                    "cash": float(cash),
                    "buying_power": float(buying_power),
                    "market_value": float(market_value),
                    "total_assets": float(total_assets),
                }
            else:
                logger.error("查询账户失败: %s", data)
        except Exception:
            logger.error("查询账户异常", exc_info=True)

        return {}

    def _parse_status(self, status: str) -> OrderStatus:
        """解析订单状态"""
        status_map = {
            "Submitted": OrderStatus.SUBMITTED,
            "Filled": OrderStatus.FILLED,
            "Partial": OrderStatus.PARTIAL,
            "Cancelled": OrderStatus.CANCELLED,
            "Failed": OrderStatus.FAILED,
        }
        return status_map.get(status, OrderStatus.SUBMITTED)


# 工厂函数
def get_trading_adapter(adapter_type: str = "futu", **kwargs) -> TradingAdapter:
    """获取交易适配器。目前仅支持 futu。"""
    if adapter_type == "futu":
        return FutuTradingAdapter(**kwargs)
    raise ValueError(
        f"未知交易适配器: {adapter_type!r}。目前仅支持 'futu'。"
    )
