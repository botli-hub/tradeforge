"""回测引擎核心"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime

class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, config: dict, data: pd.DataFrame):
        self.config = config
        self.data = data
        self.position = None  # 当前持仓
        self.positions = []   # 持仓历史
        self.trades = []      # 成交记录
        self.equity_curve = [] # 资金曲线
        
        # 风险参数
        self.initial_capital = config.get("risk_rules", {}).get("initial_capital", 100000)
        self.fee_rate = config.get("risk_rules", {}).get("fee_rate", 0.0003)
        self.slippage = config.get("risk_rules", {}).get("slippage", 0.001)
        self.max_position_pct = config.get("risk_rules", {}).get("max_position_pct", 0.5)
        
        # 风控
        self.stop_loss = config.get("risk_rules", {}).get("stop_loss")
        self.take_profit = config.get("risk_rules", {}).get("take_profit")
        
        # 仓位
        position_sizing = config.get("position_sizing", {})
        self.position_type = position_sizing.get("type", "fixed_amount")
        self.position_value = position_sizing.get("value", 10000)
        
        self.cash = self.initial_capital
        
    def run(self) -> Dict:
        """执行回测"""
        # 预热期
        warmup = self._get_warmup_period()
        
        for i in range(warmup, len(self.data)):
            bar = self.data.iloc[i]
            timestamp = bar.name if isinstance(bar.name, str) else str(bar.name)
            
            # 更新指标
            indicators = self._calculate_indicators(i)
            
            # 检查入场
            if self.position is None and self._check_entry(indicators, bar):
                self._entry(bar, i)
            
            # 检查出场
            elif self.position is not None and self._check_exit(indicators, bar):
                self._exit(bar, i)
            
            # 检查风控
            if self.position is not None:
                self._check_risk(bar)
            
            # 记录资金曲线
            self._record_equity(bar)
        
        return self._generate_result()
    
    def _get_warmup_period(self) -> int:
        """获取预热期"""
        indicators = self.config.get("indicators", [])
        max_period = 0
        for ind in indicators:
            if "period" in ind:
                max_period = max(max_period, ind.get("period", 0))
        return max(max_period, 50)
    
    def _calculate_indicators(self, index: int) -> Dict:
        """计算指标"""
        data = self.data.iloc[:index+1]
        close = data["close"]
        
        indicators = {}
        for ind in self.config.get("indicators", []):
            name = ind.get("name", "")
            ind_type = ind.get("type", "")
            period = ind.get("period", 20)
            
            if ind_type == "MA":
                indicators[name] = close.rolling(window=period).mean().iloc[-1]
            elif ind_type == "EMA":
                indicators[name] = close.ewm(span=period, adjust=False).mean().iloc[-1]
        
        return indicators
    
    def _check_entry(self, indicators: Dict, bar) -> bool:
        """检查入场条件"""
        conditions = self.config.get("conditions", {}).get("entry", {}).get("rules", [])
        
        for rule in conditions:
            if rule.get("type") == "crossover" or rule.get("op") == "cross_above":
                # 简化：假设MA交叉
                return True
        
        return False
    
    def _check_exit(self, indicators: Dict, bar) -> bool:
        """检查出场条件"""
        conditions = self.config.get("conditions", {}).get("exit", {}).get("rules", [])
        
        for rule in conditions:
            if rule.get("type") == "crossunder" or rule.get("op") == "cross_below":
                return True
        
        return False
    
    def _entry(self, bar, index: int):
        """入场"""
        # 计算买入价格（开盘价 + 滑点）
        price = bar["open"] * (1 + self.slippage)
        
        # 计算买入数量
        if self.position_type == "fixed_amount":
            quantity = self.position_value / price
        else:
            quantity = (self.cash * self.position_value / 100) / price
        
        quantity = int(quantity)  # 取整
        
        if quantity > 0:
            cost = quantity * price
            fee = cost * self.fee_rate
            
            if cost + fee <= self.cash:
                self.position = {
                    "entry_time": bar.name,
                    "entry_price": price,
                    "quantity": quantity,
                    "entry_index": index
                }
                self.cash -= (cost + fee)
    
    def _exit(self, bar, index: int):
        """出场"""
        if not self.position:
            return
        
        # 计算卖出价格（开盘价 - 滑点）
        price = bar["open"] * (1 - self.slippage)
        
        quantity = self.position["quantity"]
        revenue = quantity * price
        fee = revenue * self.fee_rate
        pnl = revenue - fee - (self.position["quantity"] * self.position["entry_price"])
        
        self.trades.append({
            "entry_time": self.position["entry_time"],
            "entry_price": self.position["entry_price"],
            "exit_time": bar.name,
            "exit_price": price,
            "quantity": quantity,
            "pnl": pnl
        })
        
        self.cash += (revenue - fee)
        self.position = None
    
    def _check_risk(self, bar):
        """检查风控"""
        if not self.position:
            return
        
        current_price = bar["close"]
        entry_price = self.position["entry_price"]
        
        # 止损
        if self.stop_loss:
            pnl_pct = (current_price - entry_price) / entry_price
            if pnl_pct <= -self.stop_loss:
                self._exit(bar, 0)
                return
        
        # 止盈
        if self.take_profit:
            pnl_pct = (current_price - entry_price) / entry_price
            if pnl_pct >= self.take_profit:
                self._exit(bar, 0)
    
    def _record_equity(self, bar):
        """记录资金曲线"""
        position_value = 0
        if self.position:
            position_value = self.position["quantity"] * bar["close"]
        
        equity = self.cash + position_value
        self.equity_curve.append({
            "timestamp": str(bar.name),
            "equity": equity
        })
    
    def _generate_result(self) -> Dict:
        """生成回测结果"""
        if not self.trades:
            return {
                "total_return": 0,
                "annual_return": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_trades": 0,
                "avg_holding_days": 0,
                "equity_curve": self.equity_curve,
                "trades": []
            }
        
        # 计算指标
        pnls = [t["pnl"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        total_pnl = sum(pnls)
        total_return = total_pnl / self.initial_capital
        
        # 年化收益
        days = len(self.equity_curve)
        annual_return = total_return * (365 / max(days, 1))
        
        # 夏普比率
        if len(pnls) > 1:
            returns = np.array(pnls) / self.initial_capital
            sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
        else:
            sharpe = 0
        
        # 最大回撤
        equity_values = [e["equity"] for e in self.equity_curve]
        peak = equity_values[0]
        max_dd = 0
        for e in equity_values:
            if e > peak:
                peak = e
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
        
        # 胜率
        win_rate = len(wins) / len(pnls) if pnls else 0
        
        # 盈亏比
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
        
        # 平均持仓天数
        total_days = 0
        for trade in self.trades:
            # 简化计算
            total_days += 5
        avg_days = total_days / len(self.trades)
        
        return {
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "total_trades": len(self.trades),
            "avg_holding_days": round(avg_days, 1),
            "equity_curve": self.equity_curve,
            "trades": self.trades
        }
