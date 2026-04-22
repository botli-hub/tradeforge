"""富途交易API

优化说明（v1.2）：
- 统一 API 错误响应语义：
  - 业务拒绝（风控拦截）返回 HTTP 422 + 结构化错误体
  - 适配器/系统错误返回 HTTP 502
  - 未连接返回 HTTP 503
- 补全 STOP 订单类型的映射
- 引入 logging 替换 print
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app.data.trading import (
    get_trading_adapter, TradingAdapter,
    Order, Position, OrderSide, OrderType, OrderStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 全局交易适配器实例
_trading_adapter: Optional[TradingAdapter] = None


class OrderRequest(BaseModel):
    symbol: str
    side: str          # BUY/SELL
    quantity: float
    price: Optional[float] = 0
    order_type: str = "LIMIT"  # MARKET/LIMIT/STOP


class ConnectRequest(BaseModel):
    adapter: str = "futu"
    trd_env: str = "SIM"    # SIM / REAL
    host: str = "127.0.0.1"
    port: int = 11111


def _require_connected():
    """检查交易连接，未连接时抛出 503"""
    if _trading_adapter is None or not _trading_adapter.is_connected():
        raise HTTPException(status_code=503, detail="交易账户未连接，请先调用 /connect")


def _parse_order_type(order_type_str: str) -> OrderType:
    """将字符串映射为 OrderType 枚举，支持 MARKET / LIMIT / STOP"""
    mapping = {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "STOP": OrderType.STOP,
    }
    result = mapping.get(order_type_str.upper())
    if result is None:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的订单类型: {order_type_str!r}，请使用 MARKET / LIMIT / STOP",
        )
    return result


@router.post("/connect")
async def connect_trading(req: ConnectRequest):
    """连接交易账户"""
    global _trading_adapter

    try:
        adapter = get_trading_adapter(
            adapter_type=req.adapter,
            host=req.host,
            port=req.port,
        )
        success = adapter.connect(req.trd_env)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.error("连接交易账户时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="连接交易账户时发生内部错误")

    if success:
        _trading_adapter = adapter
        return {"status": "connected", "adapter": req.adapter, "trd_env": req.trd_env}
    else:
        raise HTTPException(status_code=502, detail="连接富途 OpenD 失败，请检查 OpenD 是否运行")


@router.post("/disconnect")
async def disconnect_trading():
    """断开连接"""
    global _trading_adapter

    if _trading_adapter:
        try:
            _trading_adapter.disconnect()
        except Exception:
            logger.warning("断开交易账户时发生异常", exc_info=True)
        _trading_adapter = None

    return {"status": "disconnected"}


@router.get("/status")
async def get_status():
    """获取连接状态"""
    if _trading_adapter is None:
        return {"connected": False, "adapter": None}

    return {
        "connected": _trading_adapter.is_connected(),
        "adapter": "futu",
    }


@router.post("/order")
async def place_order(req: OrderRequest):
    """
    下单（带风控检查）

    响应语义：
    - 200: 下单成功，返回 order_id
    - 422: 风控拦截（业务拒绝），返回 risk_check 详情
    - 502: 适配器下单失败
    - 503: 未连接交易账户
    """
    _require_connected()

    # 风控检查
    from app.core.risk_engine import check_order_risk
    try:
        risk_result = check_order_risk(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            price=req.price or 0,
            order_type=req.order_type,
        )
    except Exception:
        logger.error("风控检查时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="风控检查时发生内部错误")

    # 风控拦截：使用 422 Unprocessable Entity 表示业务层拒绝
    if not risk_result.allowed:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RISK_BLOCKED",
                "message": risk_result.reason,
                "risk_check": risk_result.to_dict(),
            },
        )

    # 解析订单参数
    side = OrderSide.BUY if req.side.upper() == "BUY" else OrderSide.SELL
    order_type = _parse_order_type(req.order_type)

    try:
        order_id = _trading_adapter.place_order(
            symbol=req.symbol,
            side=side,
            quantity=req.quantity,
            price=req.price or 0,
            order_type=order_type,
        )
    except Exception:
        logger.error("调用交易适配器下单时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="下单时发生内部错误")

    if not order_id:
        raise HTTPException(status_code=502, detail="下单失败，请检查富途 OpenD 日志")

    response = {
        "order_id": order_id,
        "status": "submitted",
    }
    # 有警告时附加到响应（不影响 HTTP 状态码）
    if risk_result.warnings:
        response["warnings"] = risk_result.warnings

    return response


@router.post("/order/{order_id}/cancel")
async def cancel_order(order_id: str):
    """撤单"""
    _require_connected()

    try:
        success = _trading_adapter.cancel_order(order_id)
    except Exception:
        logger.error("撤单时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="撤单时发生内部错误")

    if not success:
        raise HTTPException(status_code=502, detail=f"撤单失败，订单 {order_id} 可能已成交或不存在")

    return {"status": "cancelled", "order_id": order_id}


@router.get("/orders")
async def get_orders(status: Optional[str] = None):
    """查询订单列表"""
    if _trading_adapter is None:
        return []

    order_status = None
    if status:
        try:
            order_status = OrderStatus(status.upper())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"无效的订单状态: {status!r}",
            )

    try:
        orders = _trading_adapter.query_orders(order_status)
    except Exception:
        logger.error("查询订单列表时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="查询订单列表时发生内部错误")

    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "side": o.side.value,
            "price": o.price,
            "quantity": o.quantity,
            "filled_quantity": o.filled_quantity,
            "status": o.status.value,
            "create_time": o.create_time,
            "message": o.message,
        }
        for o in orders
    ]


@router.get("/positions")
async def get_positions():
    """查询持仓"""
    if _trading_adapter is None:
        return []

    try:
        positions = _trading_adapter.query_positions()
    except Exception:
        logger.error("查询持仓时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="查询持仓时发生内部错误")

    return [
        {
            "symbol": p.symbol,
            "direction": p.direction.value,
            "quantity": p.quantity,
            "avg_cost": p.avg_cost,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "realized_pnl": p.realized_pnl,
        }
        for p in positions
    ]


@router.get("/account")
async def get_account():
    """查询账户"""
    if _trading_adapter is None:
        return {}

    try:
        return _trading_adapter.query_account()
    except Exception:
        logger.error("查询账户时发生异常", exc_info=True)
        raise HTTPException(status_code=502, detail="查询账户时发生内部错误")
