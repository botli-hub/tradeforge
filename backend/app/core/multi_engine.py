"""多标的回测引擎"""
from typing import List, Dict
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from app.core.engine import BacktestEngine

class MultiSymbolEngine:
    """多标的回测引擎"""
    
    def __init__(self, ir_config: dict, symbols: List[str], data: Dict[str, pd.DataFrame]):
        self.ir_config = ir_config
        self.symbols = symbols
        self.data = data
        self.mode = ir_config.get("position_mode", "shared")  # shared / independent
        
        # 共享资金池参数
        self.total_capital = ir_config.get("risk_rules", {}).get("initial_capital", 100000)
        self.max_position_pct = ir_config.get("risk_rules", {}).get("max_position_pct", 0.5)
        
        self.results = {}
    
    def run(self) -> Dict:
        """执行多标的回测"""
        if len(self.symbols) == 1:
            # 单标的，直接回测
            symbol = self.symbols[0]
            engine = BacktestEngine(self.ir_config, self.data[symbol])
            result = engine.run()
            self.results = {symbol: result}
            return self._aggregate_results()
        
        # 多标的并行回测
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for symbol in self.symbols:
                if symbol in self.data:
                    # 为每个标的创建独立配置
                    config = self._get_symbol_config(symbol)
                    engine = BacktestEngine(config, self.data[symbol])
                    futures[symbol] = executor.submit(engine.run)
            
            # 收集结果
            for symbol, future in futures.items():
                self.results[symbol] = future.result()
        
        return self._aggregate_results()
    
    def _get_symbol_config(self, symbol: str) -> dict:
        """获取单个标的的配置（共享资金池模式）"""
        # 简化：平均分配资金
        per_symbol_capital = self.total_capital / len(self.symbols)
        
        config = self.ir_config.copy()
        config["risk_rules"] = config.get("risk_rules", {}).copy()
        config["risk_rules"]["initial_capital"] = per_symbol_capital
        
        return config
    
    def _aggregate_results(self) -> Dict:
        """汇总所有标的的结果"""
        # 收集所有交易
        all_trades = []
        for symbol, result in self.results.items():
            trades = result.get("trades", [])
            for trade in trades:
                trade["symbol"] = symbol
            all_trades.extend(trades)
        
        # 按时间排序
        all_trades.sort(key=lambda x: x.get("entry_time", ""))
        
        # 计算汇总指标
        total_trades = len(all_trades)
        if total_trades == 0:
            return {
                "total_return": 0,
                "annual_return": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_trades": 0,
                "avg_holding_days": 0,
                "symbol_results": {},
                "trades": []
            }
        
        # 计算总盈亏
        total_pnl = sum(t.get("pnl", 0) for t in all_trades)
        total_return = total_pnl / self.total_capital
        
        # 胜率
        wins = [t for t in all_trades if t.get("pnl", 0) > 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        
        # 盈亏比
        wins_pnl = sum(t.get("pnl", 0) for t in wins)
        losses = [t for t in all_trades if t.get("pnl", 0) <= 0]
        losses_pnl = abs(sum(t.get("pnl", 0) for t in losses))
        profit_factor = wins_pnl / losses_pnl if losses_pnl > 0 else 0
        
        # 计算最大回撤（简化）
        equity = self.total_capital
        peak = equity
        max_dd = 0
        for trade in all_trades:
            equity += trade.get("pnl", 0)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
        
        return {
            "total_return": round(total_return, 4),
            "annual_return": round(total_return * 0.5, 4),  # 简化年化
            "sharpe_ratio": 0,  # 简化
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "total_trades": total_trades,
            "avg_holding_days": 5.0,  # 简化
            "symbol_results": self.results,
            "trades": all_trades
        }


def run_multi_backtest(ir_config: dict, symbols: List[str], data: Dict[str, pd.DataFrame]) -> Dict:
    """执行多标的回测"""
    engine = MultiSymbolEngine(ir_config, symbols, data)
    return engine.run()
