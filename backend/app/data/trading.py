"""富途交易适配器 - 委托/持仓"""
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

class OrderStatus(Enum):
    """订单状态"""
    SUBMITTED = "SUBMITTED"      # 已提交
    FILLED = "FILLED"          # 全部成交
    PARTIAL = "PARTIAL"        # 部分成交
    CANCELLED = "CANCELLED"    # 已撤单
    FAILED = "FAILED"          # 失败

class OrderSide(Enum):
    """交易方向"""
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"      # 市价
    LIMIT = "LIMIT"       # 限价
    STOP = "STOP"          # 止损

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
            from futu import OpenDCluster, TrdEnv, TrdSide, OrderType as FutuOrderType
            
            self._trd_env = trd_env
            self._opend = OpenDCluster(self.host, self.port)
            self._opend.start()
            
            # 登录交易
            self._trade = self._opend.trade
            self._connected = True
            return True
        except ImportError:
            print("futu-api 未安装")
            return False
        except Exception as e:
            print(f"连接失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        if hasattr(self, '_opend'):
            self._opend.stop()
        self._connected = False
    
    def is_connected(self) -> bool:
        return self._connected
    
    def place_order(self, symbol: str, side: OrderSide,
                   quantity: float, price: float = 0,
                   order_type: OrderType = OrderType.LIMIT) -> str:
        """下单"""
        if not self._connected:
            return ""
        
        try:
            from futu import TrdSide as FutuTrdSide, OrderType as FutuOrderType
            
            # 转换方向
            futu_side = FutuTrdSide.BUY if side == OrderSide.BUY else FutuTrdSide.SELL
            
            # 转换订单类型
            if order_type == OrderType.MARKET:
                futu_otype = FutuOrderType.MARKET
            else:
                futu_otype = FutuOrderType.LIMIT
            
            # 下单
            ret, data = self._trade.place_order(
                code=symbol,
                price=price,
                qty=quantity,
                side=futu_side,
                order_type=futu_otype
            )
            
            if ret == 0:
                return str(data.iloc[0]['order_id'])
            else:
                print(f"下单失败: {data}")
                return ""
                
        except Exception as e:
            print(f"下单异常: {e}")
            return ""
    
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if not self._connected:
            return False
        
        try:
            ret, data = self._trade.cancel_order(order_id)
            return ret == 0
        except Exception as e:
            print(f"撤单失败: {e}")
            return False
    
    def query_order(self, order_id: str) -> Optional[Order]:
        """查询订单"""
        if not self._connected:
            return None
        
        try:
            ret, data = self._trade.order_list_query(order_id=order_id)
            if ret == 0 and len(data) > 0:
                row = data.iloc[0]
                return Order(
                    order_id=str(row['order_id']),
                    symbol=row['code'],
                    side=OrderSide.BUY if row['side'] == TrdSide.BUY else OrderSide.SELL,
                    price=float(row['price']),
                    quantity=float(row['qty']),
                    filled_quantity=float(row['dealt_qty']),
                    status=self._parse_status(row['status']),
                    order_type=OrderType.LIMIT,
                    create_time=row['create_time'],
                    update_time=row['update_time'],
                    message=row.get('msg', '')
                )
        except Exception as e:
            print(f"查询订单失败: {e}")
        
        return None
    
    def query_orders(self, status: OrderStatus = None) -> List[Order]:
        """查询订单列表"""
        if not self._connected:
            return []
        
        try:
            ret, data = self._trade.order_list_query()
            if ret == 0:
                orders = []
                for _, row in data.iterrows():
                    orders.append(Order(
                        order_id=str(row['order_id']),
                        symbol=row['code'],
                        side=OrderSide.BUY if row['side'] == 'Buy' else OrderSide.SELL,
                        price=float(row['price']),
                        quantity=float(row['qty']),
                        filled_quantity=float(row['dealt_qty']),
                        status=self._parse_status(row['status']),
                        order_type=OrderType.LIMIT,
                        create_time=row['create_time'],
                        update_time=row['update_time'],
                        message=row.get('msg', '')
                    ))
                return orders
        except Exception as e:
            print(f"查询订单列表失败: {e}")
        
        return []
    
    def query_positions(self) -> List[Position]:
        """查询持仓"""
        if not self._connected:
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
                        realized_pnl=0
                    ))
                return positions
        except Exception as e:
            print(f"查询持仓失败: {e}")
        
        return []
    
    def query_account(self) -> dict:
        """查询账户"""
        if not self._connected:
            return {}
        
        try:
            ret, data = self._trade.accinfo_query()
            if ret == 0 and len(data) > 0:
                row = data.iloc[0]
                return {
                    "cash": float(row['cash']),
                    "buying_power": float(row['buying_power']),
                    "market_value": float(row['market_value']),
                    "total_assets": float(row['total_assets']),
                }
        except Exception as e:
            print(f"查询账户失败: {e}")
        
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


class MockTradingAdapter:
    """模拟交易适配器"""
    
    def __init__(self):
        self._connected = True
        self._orders = {}
        self._positions = {}
        self._order_counter = 1000
    
    def connect(self, trd_env: str = "SIM") -> bool:
        self._connected = True
        return True
    
    def disconnect(self):
        self._connected = False
    
    def is_connected(self) -> bool:
        return self._connected
    
    def place_order(self, symbol: str, side: OrderSide,
                   quantity: float, price: float = 0,
                   order_type: OrderType = OrderType.LIMIT) -> str:
        order_id = f"MOCK{self._order_counter}"
        self._order_counter += 1
        
        self._orders[order_id] = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            filled_quantity=quantity,  # 模拟直接成交
            status=OrderStatus.FILLED,
            order_type=order_type,
            create_time=datetime.now().isoformat(),
            update_time=datetime.now().isoformat(),
            message="模拟成交"
        )
        
        # 更新持仓
        if symbol not in self._positions:
            self._positions[symbol] = Position(
                symbol=symbol,
                direction=side,
                quantity=quantity,
                avg_cost=price,
                current_price=price,
                unrealized_pnl=0,
                realized_pnl=0
            )
        else:
            pos = self._positions[symbol]
            if pos.direction == side:
                # 同向加仓
                pos.quantity += quantity
                pos.avg_cost = (pos.avg_cost * pos.quantity + price * quantity) / (pos.quantity + quantity)
            else:
                # 反向减仓
                if pos.quantity > quantity:
                    pos.quantity -= quantity
                else:
                    del self._positions[symbol]
        
        return order_id
    
    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False
    
    def query_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)
    
    def query_orders(self, status: OrderStatus = None) -> List[Order]:
        if status:
            return [o for o in self._orders.values() if o.status == status]
        return list(self._orders.values())
    
    def query_positions(self) -> List[Position]:
        return list(self._positions.values())
    
    def query_account(self) -> dict:
        total = sum(p.current_price * p.quantity for p in self._positions.values())
        return {
            "cash": 100000,
            "buying_power": 100000,
            "market_value": total,
            "total_assets": 100000 + total,
        }


# 工厂函数
def get_trading_adapter(adapter_type: str = "mock", **kwargs) -> TradingAdapter:
    """获取交易适配器"""
    if adapter_type == "futu":
        return FutuTradingAdapter(**kwargs)
    else:
        return MockTradingAdapter()
