"""Runtime API - 市场状态、策略执行、风控

提供统一入口：
- GET /api/runtime/market-state - 获取市场状态
- GET /api/runtime/strategies/{id}/signal - 获取策略信号（支持实时/收盘两种模式）
- POST /api/runtime/risk/check - 风控检查
- GET /api/runtime/risk/events - 风险事件记录

核心设计 - 两种信号触发模式：
- ON_QUOTE（默认）：实时触发，每次quote进来立即计算信号
  - 使用 historical_bars + forming_bar（实时更新OHLC）
  - 适合盘中实时盯盘
- ON_BAR_CLOSE：收盘触发，等bar收盘才计算
  - 仅使用已收盘的history_bars
  - 适合回测和传统定时信号
"""
from typing import Optional, Literal, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.market_state import (
    get_market_state,
    SUPPORTED_TIMEFRAMES,
    MarketState,
    TriggerMode,
    get_market_state_manager,
)
from app.core.strategy_runtime import (
    evaluate_strategy,
    evaluate_all_strategies_on_symbol,
    get_available_strategies,
    summarize_signals,
    SignalResult,
)
from app.core.risk_engine import (
    check_order_risk,
    get_risk_events,
    update_risk_policy,
    get_risk_policy,
    RiskPolicy,
)


router = APIRouter(prefix="", tags=["runtime"])


# ========== Market State API ==========

@router.get("/market-state")
async def get_market_state_endpoint(
    symbol: str = Query(..., description="股票代码"),
    timeframe: str = Query("1d", description=f"时间周期: {', '.join(SUPPORTED_TIMEFRAMES)}"),
    trigger_mode: str = Query("on_quote", description="触发模式: on_quote(实时) / on_bar_close(收盘)"),
    adapter: str = Query("auto", description="数据源: futu/finnhub/yahoo (auto=按标的自动路由)"),
    host: str = Query("127.0.0.1", description="行情地址"),
    port: int = Query(11111, description="行情端口"),
):
    """
    获取市场状态
    
    两种模式：
    - on_quote: 返回历史bars + forming_bar（实时更新）
    - on_bar_close: 仅返回已收盘的历史bars
    
    返回:
    - history_bars: 已收盘的K线
    - forming_bar: 当前正在形成的K线（仅on_quote模式）
    - latest_bar: 用于信号评估的最新K线
    - is_live_triggered: 是否处于实时触发模式
    """
    try:
        state = get_market_state(
            symbol=symbol,
            timeframe=timeframe,
            trigger_mode=trigger_mode,
            history_days=420,
            adapter_type=adapter,
            adapter_host=host,
            adapter_port=port,
        )
        return state.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取市场状态失败: {e}")


@router.get("/timeframes")
async def get_supported_timeframes():
    """获取支持的时间周期"""
    return {"timeframes": SUPPORTED_TIMEFRAMES}


@router.get("/trigger-modes")
async def get_trigger_modes():
    """获取支持的触发模式"""
    return {
        "modes": [
            {
                "value": "on_quote",
                "name": "实时触发",
                "description": "每次quote进来立即计算信号，使用历史bars+实时forming bar",
            },
            {
                "value": "on_bar_close",
                "name": "收盘触发",
                "description": "等bar收盘才计算信号，仅使用已收盘的历史bars",
            },
        ]
    }


# ========== Strategy Runtime API ==========

@router.get("/strategies")
async def list_strategies():
    """获取可用策略列表"""
    return {"strategies": get_available_strategies()}


@router.get("/strategies/{strategy_id}/signal")
async def get_strategy_signal(
    strategy_id: str,
    symbol: Optional[str] = Query(None, description="标的代码，不传则取策略首个symbol"),
    timeframe: Optional[str] = Query(None, description="时间周期"),
    trigger_mode: str = Query("on_quote", description="触发模式: on_quote(实时) / on_bar_close(收盘)"),
    adapter: str = Query("auto", description="数据源: futu/finnhub/yahoo (auto=按标的自动路由)"),
    host: str = Query("127.0.0.1", description="行情地址"),
    port: int = Query(11111, description="行情端口"),
):
    """
    评估策略信号
    
    核心字段：
    - signal: BUY / SELL / NONE
    - trigger_mode: 使用的触发模式
    - is_live_triggered: 是否基于实时forming bar触发
    - latest_bar: 最新K线（可能是forming bar）
    - indicators: 指标值
    """
    try:
        result = evaluate_strategy(
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            trigger_mode=trigger_mode,
            adapter_type=adapter,
            adapter_host=host,
            adapter_port=port,
        )
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"策略信号评估失败: {e}")


@router.get("/strategies/{strategy_id}/signal/on-quote")
async def get_strategy_signal_realtime(
    strategy_id: str,
    symbol: Optional[str] = Query(None, description="标的代码"),
    timeframe: Optional[str] = Query(None, description="时间周期"),
    adapter: str = Query("auto", description="数据源: futu/finnhub/yahoo (auto=按标的自动路由)"),
    host: str = Query("127.0.0.1", description="行情地址"),
    port: int = Query(11111, description="行情端口"),
):
    """
    实时触发模式：每次quote进来立即计算信号
    
    快捷方式，等同于 GET .../signal?trigger_mode=on_quote
    """
    return await get_strategy_signal(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        trigger_mode="on_quote",
        adapter=adapter,
        host=host,
        port=port,
    )


@router.get("/strategies/{strategy_id}/signal/on-bar-close")
async def get_strategy_signal_bar_close(
    strategy_id: str,
    symbol: Optional[str] = Query(None, description="标的代码"),
    timeframe: Optional[str] = Query(None, description="时间周期"),
    adapter: str = Query("auto", description="数据源: futu/finnhub/yahoo (auto=按标的自动路由)"),
    host: str = Query("127.0.0.1", description="行情地址"),
    port: int = Query(11111, description="行情端口"),
):
    """
    收盘触发模式：等bar收盘才计算信号
    
    快捷方式，等同于 GET .../signal?trigger_mode=on_bar_close
    """
    return await get_strategy_signal(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        trigger_mode="on_bar_close",
        adapter=adapter,
        host=host,
        port=port,
    )


@router.get("/signals/symbol/{symbol}")
async def get_signals_for_symbol(
    symbol: str,
    timeframe: str = Query("1d", description="时间周期"),
    trigger_mode: str = Query("on_quote", description="触发模式"),
    adapter: str = Query("auto", description="数据源: futu/finnhub/yahoo (auto=按标的自动路由)"),
):
    """
    获取所有策略在指定标的上的信号
    
    用于批量监控
    """
    try:
        results = evaluate_all_strategies_on_symbol(
            symbol=symbol,
            timeframe=timeframe,
            trigger_mode=trigger_mode,
            adapter_type=adapter,
        )
        return summarize_signals(results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量评估失败: {e}")


# ========== Real-time Quote Push ==========

class QuoteUpdateRequest(BaseModel):
    symbol: str
    price: float
    volume: float = 0


@router.post("/quote")
async def push_quote(req: QuoteUpdateRequest):
    """
    推送实时报价（用于内部测试或WebSocket转发）
    
    更新所有订阅了该标的的MarketState的forming bar
    """
    manager = get_market_state_manager()
    manager.update_forming_bar_with_quote(
        symbol=req.symbol,
        price=req.price,
        volume=req.volume,
    )
    return {"status": "ok", "symbol": req.symbol, "price": req.price}


# ========== Risk Engine API ==========

class RiskCheckRequest(BaseModel):
    symbol: str
    side: str  # BUY / SELL
    quantity: float
    price: float = 0
    order_type: str = "MARKET"


@router.post("/risk/check")
async def check_risk(req: RiskCheckRequest):
    """
    下单前风险检查
    
    风控规则：
    - max_position_pct: 最大仓位占比
    - max_order_value: 单笔最大金额
    - signal_cooldown_seconds: 同方向冷却时间
    - price_deviation_pct: 限价偏离保护
    - allow_same_side_pyramid: 是否允许同方向加仓
    """
    try:
        result = check_order_risk(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            price=req.price,
            order_type=req.order_type,
        )
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风控检查失败: {e}")


@router.get("/risk/events")
async def list_risk_events(
    limit: int = Query(50, description="返回数量"),
    symbol: Optional[str] = Query(None, description="标的筛选"),
    blocked_only: bool = Query(False, description="仅显示被拦截的事件"),
):
    """获取风险事件记录"""
    try:
        events = get_risk_events(
            limit=limit,
            symbol=symbol,
            blocked_only=blocked_only,
        )
        return {"events": events, "count": len(events)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取风险事件失败: {e}")


class RiskPolicyRequest(BaseModel):
    max_position_pct: Optional[float] = None
    max_order_value: Optional[float] = None
    signal_cooldown_seconds: Optional[int] = None
    price_deviation_pct: Optional[float] = None
    allow_same_side_pyramid: Optional[bool] = None


@router.get("/risk/policy")
async def get_current_policy():
    """获取当前风控策略"""
    policy = get_risk_policy()
    return {"policy": policy.to_dict()}


@router.post("/risk/policy")
async def update_risk_policy_endpoint(req: RiskPolicyRequest):
    """更新风控策略"""
    current = get_risk_policy()
    
    new_policy = RiskPolicy(
        max_position_pct=req.max_position_pct if req.max_position_pct is not None else current.max_position_pct,
        max_order_value=req.max_order_value if req.max_order_value is not None else current.max_order_value,
        signal_cooldown_seconds=req.signal_cooldown_seconds if req.signal_cooldown_seconds is not None else current.signal_cooldown_seconds,
        price_deviation_pct=req.price_deviation_pct if req.price_deviation_pct is not None else current.price_deviation_pct,
        allow_same_side_pyramid=req.allow_same_side_pyramid if req.allow_same_side_pyramid is not None else current.allow_same_side_pyramid,
    )
    
    return update_risk_policy(new_policy)


# ========== Health Check ==========

# ========== Quote Push Service API ==========

class SubscribeRequest(BaseModel):
    symbols: List[str]
    host: str = "127.0.0.1"
    port: int = 11111


@router.post("/push/start")
async def start_push_service(req: SubscribeRequest = None):
    """
    启动实时行情推送服务
    
    仅支持港股和A股（通过富途）
    美股暂不支持
    
    启动后可调用 /push/subscribe 订阅具体标的
    """
    try:
        from app.core.quote_push import get_quote_push_service, start_quote_push
        
        host = req.host if req else "127.0.0.1"
        port = req.port if req else 11111
        
        success = start_quote_push(host=host, port=port)
        
        if success:
            return {"status": "ok", "message": "推送服务已启动", "supported_markets": ["HK", "SH", "SZ"]}
        else:
            return {"status": "error", "message": "推送服务启动失败"}
            
    except Exception as e:
        return {"status": "error", "message": f"启动失败: {e}"}


@router.post("/push/stop")
async def stop_push_service():
    """停止实时行情推送服务"""
    try:
        from app.core.quote_push import stop_quote_push
        stop_quote_push()
        return {"status": "ok", "message": "推送服务已停止"}
    except Exception as e:
        return {"status": "error", "message": f"停止失败: {e}"}


@router.post("/push/subscribe")
async def subscribe_push(request: SubscribeRequest):
    """
    订阅实时行情推送
    
    Args:
        symbols: 股票代码列表，如 ["00700.HK", "600519.SH"]
        
    注意：
    - 港股代码格式：00700.HK 或 00700
    - A股代码格式：600519.SH, 300750.SZ
    - 美股暂不支持（返回错误）
    """
    try:
        from app.core.quote_push import subscribe_quotes
        
        # 检查是否包含美股
        us_symbols = [s for s in request.symbols if s.startswith('US.') or (
            not '.' in s and s.isalpha() and len(s) <= 5
        )]
        
        if us_symbols:
            # 美股暂不支持
            cn_hk_symbols = [s for s in request.symbols if s not in us_symbols]
            if cn_hk_symbols:
                success = subscribe_quotes(cn_hk_symbols)
                return {
                    "status": "partial",
                    "message": f"美股暂不支持，已订阅港股/A股: {cn_hk_symbols}",
                    "subscribed": cn_hk_symbols,
                    "unsupported": us_symbols,
                }
            else:
                return {"status": "error", "message": "美股暂不支持实时推送"}
        
        success = subscribe_quotes(request.symbols)
        
        if success:
            return {"status": "ok", "subscribed": request.symbols}
        else:
            return {"status": "error", "message": "订阅失败"}
            
    except Exception as e:
        return {"status": "error", "message": f"订阅异常: {e}"}


@router.post("/push/unsubscribe")
async def unsubscribe_push(request: SubscribeRequest):
    """取消订阅实时行情"""
    try:
        from app.core.quote_push import unsubscribe_quotes
        
        success = unsubscribe_quotes(request.symbols)
        
        if success:
            return {"status": "ok", "unsubscribed": request.symbols}
        return {"status": "error", "message": "取消订阅失败"}
        
    except Exception as e:
        return {"status": "error", "message": f"取消订阅异常: {e}"}


@router.get("/push/status")
async def get_push_status():
    """获取推送服务状态"""
    try:
        from app.core.quote_push import get_quote_push_service
        
        service = get_quote_push_service()
        
        return {
            "running": service.is_running(),
            "subscribed_symbols": list(service.get_subscribed_symbols()),
            "supported_markets": ["HK", "SH", "SZ"],
            "unsupported_markets": ["US"],
        }
    except Exception as e:
        return {"status": "error", "message": f"获取状态失败: {e}"}


@router.get("/health")
async def runtime_health():
    """Runtime服务健康检查"""
    return {
        "status": "ok",
        "trigger_modes": ["on_quote", "on_bar_close"],
        "default_mode": "on_quote",
        "components": {
            "market_state": "ok",
            "strategy_runtime": "ok",
            "risk_engine": "ok",
            "quote_push": "ok",
        },
        "push_markets": {
            "supported": ["HK", "SH", "SZ"],
            "unsupported": ["US"],
        }
    }
