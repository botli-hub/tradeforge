"""交易风控层 - Risk Engine

功能：
- 下单前风险检查
- 基础风控规则：
  - max_position_pct（最大仓位占总资产比例）
  - max_order_value（单笔最大下单金额）
  - signal_cooldown_seconds（同标的同方向冷却）
  - price_deviation_pct（限价偏离最新价保护）
  - allow_same_side_pyramid（是否允许同方向继续加仓）
- 记录风险事件到数据库
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum


# 风险检查结果
class RiskCheckResult(Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    WARN = "WARN"


@dataclass
class RiskCheckOutput:
    """风险检查输出"""
    allowed: bool
    result: str  # ALLOW / BLOCK / WARN
    reason: str
    risk_score: float  # 0-100
    warnings: List[str]
    details: Dict[str, Any]
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "result": self.result,
            "reason": self.reason,
            "risk_score": self.risk_score,
            "warnings": self.warnings,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class RiskPolicy:
    """风控策略配置"""
    max_position_pct: float = 0.3        # 最大仓位占比30%
    max_order_value: float = 100000.0   # 单笔最大10万
    signal_cooldown_seconds: int = 60   # 同方向冷却60秒
    price_deviation_pct: float = 5.0     # 限价偏离保护5%
    allow_same_side_pyramid: bool = False # 不允许同方向加仓
    
    @classmethod
    def default(cls) -> "RiskPolicy":
        return cls()
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskPolicy":
        return cls(
            max_position_pct=data.get("max_position_pct", 0.3),
            max_order_value=data.get("max_order_value", 100000.0),
            signal_cooldown_seconds=data.get("signal_cooldown_seconds", 60),
            price_deviation_pct=data.get("price_deviation_pct", 5.0),
            allow_same_side_pyramid=data.get("allow_same_side_pyramid", False),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_position_pct": self.max_position_pct,
            "max_order_value": self.max_order_value,
            "signal_cooldown_seconds": self.signal_cooldown_seconds,
            "price_deviation_pct": self.price_deviation_pct,
            "allow_same_side_pyramid": self.allow_same_side_pyramid,
        }


# 全局风控策略缓存
_global_risk_policy: Optional[RiskPolicy] = None


def get_default_risk_policy() -> RiskPolicy:
    """获取默认风控策略"""
    global _global_risk_policy
    if _global_risk_policy is None:
        _global_risk_policy = RiskPolicy.default()
    return _global_risk_policy


def set_risk_policy(policy: RiskPolicy):
    """设置风控策略"""
    global _global_risk_policy
    _global_risk_policy = policy


def get_risk_policy() -> RiskPolicy:
    """获取当前风控策略"""
    return get_default_risk_policy()


def _save_risk_event(
    event_type: str,
    symbol: str,
    side: str,
    order_value: float,
    risk_score: float,
    details: Dict[str, Any],
    blocked: bool = False,
):
    """保存风险事件到数据库"""
    from app.data.database import get_db
    
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO risk_events 
        (id, event_type, symbol, side, order_value, risk_score, details, blocked, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            symbol,
            side,
            order_value,
            risk_score,
            json.dumps(details, ensure_ascii=False),
            1 if blocked else 0,
            now,
        )
    )
    conn.commit()
    conn.close()
    
    return event_id


def _get_account_info() -> Dict[str, float]:
    """获取账户信息"""
    from app.data.trading import get_trading_adapter
    
    adapter = get_trading_adapter("mock")
    if adapter.connect("SIM"):
        account = adapter.query_account()
        return {
            "cash": account.get("cash", 0),
            "buying_power": account.get("buying_power", 0),
            "market_value": account.get("market_value", 0),
            "total_assets": account.get("total_assets", 0),
        }
    # 默认返回模拟账户
    return {
        "cash": 100000,
        "buying_power": 100000,
        "market_value": 0,
        "total_assets": 100000,
    }


def _get_positions() -> List[Dict[str, Any]]:
    """获取当前持仓"""
    from app.data.trading import get_trading_adapter
    
    adapter = get_trading_adapter("mock")
    if adapter.connect("SIM"):
        positions = adapter.query_positions()
        return [
            {
                "symbol": p.symbol,
                "direction": p.direction.value,
                "quantity": p.quantity,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "value": p.quantity * p.current_price,
            }
            for p in positions
        ]
    return []


def _get_last_signal_time(symbol: str, direction: str) -> Optional[datetime]:
    """获取同标的同方向的最后信号时间"""
    # 从最近的订单/成交记录中查找
    from app.data.database import get_db
    
    conn = get_db()
    cursor = conn.cursor()
    
    # 简化实现：查询最近的trades
    cursor.execute(
        """
        SELECT created_at FROM trades 
        WHERE symbol = ? AND direction = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (symbol, direction.upper()),
    )
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return datetime.fromisoformat(row["created_at"])
    return None


def check_order_risk(
    symbol: str,
    side: str,  # BUY / SELL
    quantity: float,
    price: float = 0,  # 0表示市价单
    order_type: str = "MARKET",  # MARKET / LIMIT
    policy: Optional[RiskPolicy] = None,
) -> RiskCheckOutput:
    """
    检查订单风险
    
    Args:
        symbol: 标的代码
        side: 买入/卖出
        quantity: 数量
        price: 价格（限价单）
        order_type: 订单类型
        policy: 风控策略（可选，默认使用全局策略）
    
    Returns:
        RiskCheckOutput: 风险检查结果
    """
    now = datetime.now(timezone.utc)
    policy = policy or get_risk_policy()
    
    warnings = []
    risk_score = 0.0
    details = {}
    
    # 1. 获取账户信息
    account = _get_account_info()
    total_assets = account.get("total_assets", 100000)
    if total_assets <= 0:
        return RiskCheckOutput(
            allowed=False,
            result=RiskCheckResult.BLOCK.value,
            reason="账户总资产为0，无法交易",
            risk_score=100,
            warnings=[],
            details={"account": account},
            timestamp=now.isoformat(),
        )
    
    # 2. 计算订单价值
    # 获取当前价格
    from app.data.adapter import get_adapter
    adapter = get_adapter("mock")
    current_price = 0
    if adapter.connect():
        quote = adapter.get_quote(symbol)
        if quote:
            current_price = quote.price
    
    if current_price <= 0:
        # 使用限价或默认价格
        current_price = price if price > 0 else 100
    
    order_value = quantity * current_price
    details["order_value"] = order_value
    details["current_price"] = current_price
    details["quantity"] = quantity
    
    # 3. 检查：单笔最大下单金额
    if order_value > policy.max_order_value:
        risk_score += 30
        warnings.append(f"单笔订单金额 {order_value:.2f} 超过限制 {policy.max_order_value:.2f}")
        _save_risk_event(
            event_type="MAX_ORDER_VALUE",
            symbol=symbol,
            side=side,
            order_value=order_value,
            risk_score=30,
            details={"max_order_value": policy.max_order_value},
            blocked=False,
        )
    
    # 4. 检查：最大仓位占比
    positions = _get_positions()
    current_position_value = sum(p.get("value", 0) for p in positions if p.get("symbol") == symbol)
    current_position_pct = current_position_value / total_assets
    
    if side.upper() == "BUY":
        new_position_value = current_position_value + order_value
        new_position_pct = new_position_value / total_assets
        
        if new_position_pct > policy.max_position_pct:
            risk_score += 25
            warnings.append(
                f"建仓后仓位占比 {new_position_pct*100:.1f}% 超过限制 {policy.max_position_pct*100:.1f}%"
            )
            _save_risk_event(
                event_type="MAX_POSITION_PCT",
                symbol=symbol,
                side=side,
                order_value=order_value,
                risk_score=25,
                details={
                    "current_pct": current_position_pct,
                    "new_pct": new_position_pct,
                    "max_pct": policy.max_position_pct,
                },
                blocked=False,
            )
    
    details["current_position_value"] = current_position_value
    details["current_position_pct"] = current_position_pct
    
    # 5. 检查：同方向冷却
    last_signal_time = _get_last_signal_time(symbol, side)
    if last_signal_time:
        cooldown_seconds = (now - last_signal_time).total_seconds()
        if cooldown_seconds < policy.signal_cooldown_seconds:
            risk_score += 15
            warnings.append(
                f"距上次同方向信号仅 {cooldown_seconds:.0f}秒，冷却期 {policy.signal_cooldown_seconds}秒"
            )
            _save_risk_event(
                event_type="SIGNAL_COOLDOWN",
                symbol=symbol,
                side=side,
                order_value=order_value,
                risk_score=15,
                details={"cooldown_seconds": cooldown_seconds, "required": policy.signal_cooldown_seconds},
                blocked=False,
            )
    
    # 6. 检查：限价偏离保护
    if order_type.upper() == "LIMIT" and price > 0 and current_price > 0:
        deviation_pct = abs(price - current_price) / current_price * 100
        if deviation_pct > policy.price_deviation_pct:
            risk_score += 20
            warnings.append(
                f"限价偏离最新价 {deviation_pct:.2f}% 超过限制 {policy.price_deviation_pct}%"
            )
            _save_risk_event(
                event_type="PRICE_DEVIATION",
                symbol=symbol,
                side=side,
                order_value=order_value,
                risk_score=20,
                details={
                    "limit_price": price,
                    "current_price": current_price,
                    "deviation_pct": deviation_pct,
                    "max_deviation_pct": policy.price_deviation_pct,
                },
                blocked=False,
            )
    
    # 7. 检查：同方向加仓
    if side.upper() == "BUY" and not policy.allow_same_side_pyramid:
        has_same_direction = any(
            p.get("symbol") == symbol and p.get("direction") == "BUY"
            for p in positions
        )
        if has_same_direction:
            risk_score += 10
            warnings.append("策略禁止同方向加仓（pyramiding）")
            _save_risk_event(
                event_type="SAME_SIDE_PYRAMID",
                symbol=symbol,
                side=side,
                order_value=order_value,
                risk_score=10,
                details={"allow_pyramid": policy.allow_same_side_pyramid},
                blocked=False,
            )
    
    # 8. 确定最终结果
    risk_score = min(100, risk_score)
    
    if risk_score >= 50:
        allowed = False
        result = RiskCheckResult.BLOCK.value
        reason = f"风险评分过高 ({risk_score:.0f})，禁止下单"
    elif risk_score > 0:
        allowed = True
        result = RiskCheckResult.WARN.value
        reason = f"风险警告 ({risk_score:.0f})，但允许下单"
    else:
        allowed = True
        result = RiskCheckResult.ALLOW.value
        reason = "风控检查通过"
    
    return RiskCheckOutput(
        allowed=allowed,
        result=result,
        reason=reason,
        risk_score=risk_score,
        warnings=warnings,
        details=details,
        timestamp=now.isoformat(),
    )


def get_risk_events(
    limit: int = 50,
    symbol: Optional[str] = None,
    blocked_only: bool = False,
) -> List[Dict[str, Any]]:
    """获取风险事件记录"""
    from app.data.database import get_db
    
    conn = get_db()
    cursor = conn.cursor()
    
    sql = "SELECT * FROM risk_events WHERE 1=1"
    params = []
    
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol)
    
    if blocked_only:
        sql += " AND blocked = 1"
    
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "event_type": row["event_type"],
            "symbol": row["symbol"],
            "side": row["side"],
            "order_value": row["order_value"],
            "risk_score": row["risk_score"],
            "details": json.loads(row["details"]) if row["details"] else {},
            "blocked": bool(row["blocked"]),
            "created_at": row["created_at"],
        })
    
    return events


def update_risk_policy(policy: RiskPolicy) -> Dict[str, Any]:
    """更新风控策略"""
    global _global_risk_policy
    _global_risk_policy = policy
    
    return {
        "status": "updated",
        "policy": policy.to_dict(),
    }
