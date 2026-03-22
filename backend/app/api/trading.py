"""富途交易API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from app.data.trading import (
    get_trading_adapter, TradingAdapter,
    Order, Position, OrderSide, OrderType
)

router = APIRouter()

# 全局交易适配器实例
_trading_adapter: Optional[TradingAdapter] = None

class OrderRequest(BaseModel):
    symbol: str
    side: str  # BUY/SELL
    quantity: float
    price: Optional[float] = 0
    order_type: str = "LIMIT"  # MARKET/LIMIT/STOP

class ConnectRequest(BaseModel):
    adapter: str = "futu"
    trd_env: str = "SIM"    # SIM / REAL
    host: str = "127.0.0.1"
    port: int = 11111

@router.post("/connect")
async def connect_trading(req: ConnectRequest):
    """连接交易账户"""
    global _trading_adapter
    
    adapter = get_trading_adapter(
        adapter_type=req.adapter,
        host=req.host,
        port=req.port
    )
    
    success = adapter.connect(req.trd_env)
    
    if success:
        _trading_adapter = adapter
        return {"status": "connected", "adapter": req.adapter}
    else:
        raise HTTPException(status_code=400, detail="连接失败")

@router.post("/disconnect")
async def disconnect_trading():
    """断开连接"""
    global _trading_adapter
    
    if _trading_adapter:
        _trading_adapter.disconnect()
        _trading_adapter = None
    
    return {"status": "disconnected"}

@router.get("/status")
async def get_status():
    """获取连接状态"""
    global _trading_adapter
    
    if _trading_adapter is None:
        return {"connected": False, "adapter": None}
    
    return {
        "connected": _trading_adapter.is_connected(),
        "adapter": "futu"
    }

@router.post("/order")
async def place_order(req: OrderRequest):
    """下单（带风控检查）"""
    global _trading_adapter
    
    if _trading_adapter is None or not _trading_adapter.is_connected():
        raise HTTPException(status_code=400, detail="未连接交易账户")
    
    # 风控检查
    from app.core.risk_engine import check_order_risk
    risk_result = check_order_risk(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        price=req.price,
        order_type=req.order_type,
    )
    
    # 如果被完全阻止，返回错误
    if not risk_result.allowed and risk_result.result == "BLOCK":
        return {
            "order_id": None,
            "status": "rejected",
            "risk_check": risk_result.to_dict(),
            "message": f"风控拦截: {risk_result.reason}",
        }
    
    # 如果有警告，记录但继续执行
    warning_msg = ""
    if risk_result.warnings:
        warning_msg = f" (警告: {', '.join(risk_result.warnings)})"
    
    side = OrderSide.BUY if req.side == "BUY" else OrderSide.SELL
    order_type = OrderType.MARKET if req.order_type == "MARKET" else OrderType.LIMIT
    
    order_id = _trading_adapter.place_order(
        symbol=req.symbol,
        side=side,
        quantity=req.quantity,
        price=req.price,
        order_type=order_type
    )
    
    if order_id:
        return {"order_id": order_id, "status": "submitted"}
    else:
        raise HTTPException(status_code=400, detail="下单失败")

@router.post("/order/{order_id}/cancel")
async def cancel_order(order_id: str):
    """撤单"""
    global _trading_adapter
    
    if _trading_adapter is None or not _trading_adapter.is_connected():
        raise HTTPException(status_code=400, detail="未连接")
    
    success = _trading_adapter.cancel_order(order_id)
    
    if success:
        return {"status": "cancelled"}
    else:
        raise HTTPException(status_code=400, detail="撤单失败")

@router.get("/orders")
async def get_orders(status: Optional[str] = None):
    """查询订单列表"""
    global _trading_adapter
    
    if _trading_adapter is None:
        return []
    
    order_status = None
    if status:
        from app.data.trading import OrderStatus
        order_status = OrderStatus(status)
    
    orders = _trading_adapter.query_orders(order_status)
    
    return [{
        "order_id": o.order_id,
        "symbol": o.symbol,
        "side": o.side.value,
        "price": o.price,
        "quantity": o.quantity,
        "filled_quantity": o.filled_quantity,
        "status": o.status.value,
        "create_time": o.create_time,
        "message": o.message
    } for o in orders]

@router.get("/positions")
async def get_positions():
    """查询持仓"""
    global _trading_adapter
    
    if _trading_adapter is None:
        return []
    
    positions = _trading_adapter.query_positions()
    
    return [{
        "symbol": p.symbol,
        "direction": p.direction.value,
        "quantity": p.quantity,
        "avg_cost": p.avg_cost,
        "current_price": p.current_price,
        "unrealized_pnl": p.unrealized_pnl,
        "realized_pnl": p.realized_pnl
    } for p in positions]

@router.get("/account")
async def get_account():
    """查询账户"""
    global _trading_adapter
    
    if _trading_adapter is None:
        return {}
    
    return _trading_adapter.query_account()
