"""多标的回测引擎"""
from typing import Dict, List
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

from app.core.engine import BacktestEngine


class MultiSymbolEngine:
    """多标的回测引擎"""

    def __init__(self, ir_config: dict, symbols: List[str], data: Dict[str, pd.DataFrame]):
        self.ir_config = ir_config
        self.symbols = symbols
        self.data = data
        self.mode = ir_config.get("position_mode", "shared")

        risk = ir_config.get("risk_rules", {})
        self.total_capital = risk.get("initial_capital", 100000)
        self.max_position_pct = risk.get("max_position_pct", 0.5)

        self.results: Dict[str, Dict] = {}

    def run(self) -> Dict:
        if len(self.symbols) == 1:
            symbol = self.symbols[0]
            engine = BacktestEngine(self.ir_config, self.data[symbol])
            self.results = {symbol: engine.run()}
            return self._aggregate_results()

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for symbol in self.symbols:
                if symbol in self.data:
                    config = self._get_symbol_config(symbol)
                    engine = BacktestEngine(config, self.data[symbol])
                    futures[symbol] = executor.submit(engine.run)

            for symbol, future in futures.items():
                self.results[symbol] = future.result()

        return self._aggregate_results()

    def _get_symbol_config(self, symbol: str) -> dict:
        """共享资金池：平均分配初始资金"""
        per_capital = self.total_capital / max(len(self.symbols), 1)
        config = dict(self.ir_config)
        config["risk_rules"] = dict(config.get("risk_rules", {}))
        config["risk_rules"]["initial_capital"] = per_capital
        return config

    def _aggregate_results(self) -> Dict:
        all_trades: List[Dict] = []
        for symbol, result in self.results.items():
            for trade in result.get("trades", []):
                t = dict(trade)
                t["symbol"] = symbol
                all_trades.append(t)

        all_trades.sort(key=lambda x: x.get("entry_time", ""))

        if not all_trades:
            return {
                "total_return": 0,
                "annual_return": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_trades": 0,
                "avg_holding_days": 0,
                "buy_and_hold_return": 0,
                "symbol_results": self.results,
                "equity_curve": [],
                "trades": [],
            }

        # 汇总 PnL
        pnls = [t.get("pnl", 0) for t in all_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        total_return = total_pnl / self.total_capital

        # 年化收益：基于实际交易区间天数
        try:
            first_dt = pd.Timestamp(str(all_trades[0]["entry_time"]))
            last_dt = pd.Timestamp(str(all_trades[-1].get("exit_time") or all_trades[-1]["entry_time"]))
            span_days = max((last_dt - first_dt).days, 1)
        except Exception:
            span_days = 252
        annual_return = total_return * (365 / span_days)

        # 权益曲线（按时间合并各标的）
        equity = self.total_capital
        equity_curve = []
        for trade in all_trades:
            equity += trade.get("pnl", 0)
            equity_curve.append({"timestamp": str(trade.get("exit_time", "")), "equity": equity})

        # 最大回撤
        peak = self.total_capital
        max_dd = 0.0
        running_equity = self.total_capital
        for trade in all_trades:
            running_equity += trade.get("pnl", 0)
            if running_equity > peak:
                peak = running_equity
            dd = (peak - running_equity) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 胜率 / 盈亏比
        win_rate = len(wins) / len(pnls)
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

        # 平均持仓天数（实际计算）
        total_hold_days = 0
        valid_count = 0
        for trade in all_trades:
            try:
                entry_dt = pd.Timestamp(str(trade["entry_time"]))
                exit_dt = pd.Timestamp(str(trade["exit_time"]))
                hold = (exit_dt - entry_dt).days
                if hold >= 0:
                    total_hold_days += hold
                    valid_count += 1
            except Exception:
                pass
        avg_days = total_hold_days / valid_count if valid_count > 0 else 0

        # Buy-and-hold 基准（各标的平均）
        bh_returns = [r.get("buy_and_hold_return", 0) for r in self.results.values()]
        buy_and_hold_return = round(sum(bh_returns) / len(bh_returns), 4) if bh_returns else 0

        # 夏普比率（基于合并权益序列）
        if len(equity_curve) > 1:
            eq_vals = [e["equity"] for e in equity_curve]
            daily_rets = np.diff(eq_vals) / np.array(eq_vals[:-1])
            sharpe = (daily_rets.mean() / daily_rets.std() * np.sqrt(252)
                      if daily_rets.std() > 0 else 0)
        else:
            sharpe = 0

        return {
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "total_trades": len(all_trades),
            "avg_holding_days": round(avg_days, 1),
            "buy_and_hold_return": buy_and_hold_return,
            "symbol_results": self.results,
            "equity_curve": equity_curve,
            "trades": all_trades,
        }


def run_multi_backtest(ir_config: dict, symbols: List[str], data: Dict[str, pd.DataFrame]) -> Dict:
    engine = MultiSymbolEngine(ir_config, symbols, data)
    return engine.run()
