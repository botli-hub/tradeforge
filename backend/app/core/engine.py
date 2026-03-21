"""回测引擎核心"""
import math
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


class BacktestEngine:
    """回测引擎"""

    def __init__(self, config: dict, data: pd.DataFrame):
        self.config = config
        self.data = data
        self.position = None      # 当前持仓
        self.trades: List[Dict] = []
        self.equity_curve: List[Dict] = []

        risk = config.get("risk_rules", {})
        self.initial_capital = risk.get("initial_capital", 100000)
        self.fee_rate = risk.get("fee_rate", 0.0003)
        self.slippage = risk.get("slippage", 0.001)
        self.max_position_pct = risk.get("max_position_pct", 0.5)
        self.stop_loss = risk.get("stop_loss")
        self.take_profit = risk.get("take_profit")

        ps = config.get("position_sizing", {})
        self.position_type = ps.get("type", "fixed_amount")
        self.position_value = ps.get("value", 10000)

        self.cash = self.initial_capital

    # ------------------------------------------------------------------
    # 指标预计算（向量化，O(n)）
    # ------------------------------------------------------------------

    def _resolve_period(self, ind: Dict) -> int:
        """解析指标周期，支持直接 period 和 period_ref（引用 parameters 默认值）。"""
        if "period" in ind:
            return int(ind["period"])
        ref = ind.get("period_ref")
        if ref:
            for param in self.config.get("parameters", []):
                if param.get("name") == ref:
                    return int(param.get("default", 20))
        return 20

    def _precompute_indicators(self) -> Dict[str, pd.Series]:
        """将所有指标和变量预计算为完整序列，避免循环内重复计算。"""
        ind_series: Dict[str, pd.Series] = {}

        for ind in self.config.get("indicators", []):
            name = ind.get("name", "")
            ind_type = ind.get("type", "")
            period = self._resolve_period(ind)
            source_col = ind.get("source", "close")

            src = self.data[source_col] if source_col in self.data.columns else self.data["close"]

            if ind_type == "MA":
                ind_series[name] = src.rolling(window=period).mean()
            elif ind_type == "EMA":
                ind_series[name] = src.ewm(span=period, adjust=False).mean()

        # 计算变量（如 vol_ratio = volume / vol_ma）
        for var_name, expr in self.config.get("variables", {}).items():
            op = expr.get("op")
            left_key = expr.get("left", "")
            right_key = expr.get("right", "")

            left = ind_series.get(left_key) if left_key in ind_series else (
                self.data[left_key] if left_key in self.data.columns else None
            )
            right = ind_series.get(right_key) if right_key in ind_series else (
                self.data[right_key] if right_key in self.data.columns else None
            )

            if left is None or right is None:
                continue

            if op == "/":
                ind_series[var_name] = left / right.replace(0, float("nan"))
            elif op == "*":
                ind_series[var_name] = left * right
            elif op == "+":
                ind_series[var_name] = left + right
            elif op == "-":
                ind_series[var_name] = left - right

        return ind_series

    # ------------------------------------------------------------------
    # 条件评估
    # ------------------------------------------------------------------

    def _eval_rule(self, rule: Dict, ind_series: Dict[str, pd.Series], idx: int) -> bool:
        op = rule.get("op", "")

        if op in ("cross_above", "cross_below"):
            if idx < 1:
                return False
            left_key = rule.get("left", "")
            right_key = rule.get("right", "")
            left_s = ind_series.get(left_key)
            right_s = ind_series.get(right_key)
            if left_s is None or right_s is None:
                return False
            lc, rc = left_s.iloc[idx], right_s.iloc[idx]
            lp, rp = left_s.iloc[idx - 1], right_s.iloc[idx - 1]
            if any(math.isnan(v) for v in (lc, rc, lp, rp)):
                return False
            if op == "cross_above":
                return lc > rc and lp <= rp
            else:
                return lc < rc and lp >= rp

        if op in (">", "<", ">=", "<=", "=="):
            left_key = rule.get("left", "")
            right_val = rule.get("right")
            left_s = ind_series.get(left_key)
            if left_s is None and left_key in self.data.columns:
                lv = self.data[left_key].iloc[idx]
            elif left_s is not None:
                lv = left_s.iloc[idx]
            else:
                return False
            if math.isnan(lv):
                return False
            rv = float(right_val) if right_val is not None else 0.0
            if op == ">":   return lv > rv
            if op == "<":   return lv < rv
            if op == ">=":  return lv >= rv
            if op == "<=":  return lv <= rv
            if op == "==":  return lv == rv

        return False

    def _check_conditions(self, group: Dict, ind_series: Dict[str, pd.Series], idx: int) -> bool:
        logic = group.get("type", "AND")
        rules = group.get("rules", [])
        if not rules:
            return False
        results = [self._eval_rule(r, ind_series, idx) for r in rules]
        return all(results) if logic == "AND" else any(results)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        warmup = self._get_warmup_period()
        ind_series = self._precompute_indicators()

        entry_cond = self.config.get("conditions", {}).get("entry", {})
        exit_cond = self.config.get("conditions", {}).get("exit", {})

        for i in range(warmup, len(self.data)):
            bar = self.data.iloc[i]

            if self.position is None:
                if self._check_conditions(entry_cond, ind_series, i):
                    self._entry(bar, i)
            else:
                if self._check_conditions(exit_cond, ind_series, i):
                    self._exit(bar, i)
                elif self.position is not None:
                    self._check_risk(bar)

            self._record_equity(bar)

        # 强制平仓（最后一根K线还有持仓）
        if self.position is not None:
            self._exit(self.data.iloc[-1], len(self.data) - 1)

        return self._generate_result(warmup)

    # ------------------------------------------------------------------
    # 入场 / 出场 / 风控
    # ------------------------------------------------------------------

    def _get_warmup_period(self) -> int:
        max_period = max(
            (self._resolve_period(ind) for ind in self.config.get("indicators", [])),
            default=1,
        )
        return max(max_period, 1)

    def _entry(self, bar, index: int):
        price = bar["open"] * (1 + self.slippage)

        if self.position_type == "fixed_amount":
            quantity = int(self.position_value / price)
        else:
            quantity = int((self.cash * self.position_value / 100) / price)

        if quantity <= 0:
            return

        cost = quantity * price
        fee = cost * self.fee_rate
        if cost + fee > self.cash:
            return

        self.position = {
            "entry_time": str(bar.name),
            "entry_price": price,
            "quantity": quantity,
        }
        self.cash -= cost + fee

    def _exit(self, bar, index: int):
        if not self.position:
            return
        price = bar["open"] * (1 - self.slippage)
        qty = self.position["quantity"]
        revenue = qty * price
        fee = revenue * self.fee_rate
        pnl = revenue - fee - qty * self.position["entry_price"]

        self.trades.append({
            "entry_time": self.position["entry_time"],
            "entry_price": self.position["entry_price"],
            "exit_time": str(bar.name),
            "exit_price": price,
            "quantity": qty,
            "pnl": pnl,
        })
        self.cash += revenue - fee
        self.position = None

    def _check_risk(self, bar):
        if not self.position:
            return
        current = bar["close"]
        entry = self.position["entry_price"]
        pnl_pct = (current - entry) / entry

        if self.stop_loss and pnl_pct <= -self.stop_loss:
            self._exit(bar, 0)
        elif self.take_profit and pnl_pct >= self.take_profit:
            self._exit(bar, 0)

    def _record_equity(self, bar):
        pos_val = self.position["quantity"] * bar["close"] if self.position else 0
        self.equity_curve.append({
            "timestamp": str(bar.name),
            "equity": self.cash + pos_val,
        })

    # ------------------------------------------------------------------
    # 结果生成
    # ------------------------------------------------------------------

    def _generate_result(self, warmup: int = 0) -> Dict:
        # Buy-and-hold 基准
        buy_and_hold_return = 0.0
        if len(self.data) > warmup:
            bh_entry = self.data.iloc[warmup]["open"]
            bh_exit = self.data.iloc[-1]["close"]
            if bh_entry > 0:
                buy_and_hold_return = round((bh_exit - bh_entry) / bh_entry, 4)

        empty = {
            "total_return": 0,
            "annual_return": 0,
            "sharpe_ratio": 0,
            "max_drawdown": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_trades": 0,
            "avg_holding_days": 0,
            "buy_and_hold_return": buy_and_hold_return,
            "equity_curve": self.equity_curve,
            "trades": [],
        }

        if not self.trades:
            return empty

        pnls = [t["pnl"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        total_return = total_pnl / self.initial_capital

        # 年化收益（基于实际天数）
        days = len(self.equity_curve)
        annual_return = total_return * (365 / max(days, 1))

        # 夏普比率（每日权益收益率）
        if len(self.equity_curve) > 1:
            equities = [e["equity"] for e in self.equity_curve]
            daily_rets = np.diff(equities) / np.array(equities[:-1])
            sharpe = (daily_rets.mean() / daily_rets.std() * np.sqrt(252)
                      if daily_rets.std() > 0 else 0)
        else:
            sharpe = 0

        # 最大回撤
        peak = self.initial_capital
        max_dd = 0.0
        for e in self.equity_curve:
            eq = e["equity"]
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
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
        for trade in self.trades:
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

        return {
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "total_trades": len(self.trades),
            "avg_holding_days": round(avg_days, 1),
            "buy_and_hold_return": buy_and_hold_return,
            "equity_curve": self.equity_curve,
            "trades": self.trades,
        }
