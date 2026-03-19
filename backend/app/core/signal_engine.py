"""策略实时信号引擎"""
import math
from typing import Any, Dict, Optional
import pandas as pd


class StrategySignalEngine:
    def __init__(self, config: Dict[str, Any], data: pd.DataFrame):
        self.config = config or {}
        self.data = data.copy()
        self.parameters = {
            item.get('name'): item.get('default')
            for item in self.config.get('parameters', [])
            if isinstance(item, dict) and item.get('name')
        }
        self.indicators: Dict[str, pd.Series] = {}
        self.variables: Dict[str, pd.Series] = {}

    def evaluate(self) -> Dict[str, Any]:
        if self.data.empty or len(self.data) < 2:
            return {
                'signal': 'NONE',
                'entry_triggered': False,
                'exit_triggered': False,
                'reason': '数据不足，至少需要2根K线',
                'entry': {'type': 'AND', 'triggered': False, 'rules': []},
                'exit': {'type': 'OR', 'triggered': False, 'rules': []},
                'latest_bar': None,
                'indicators': {},
            }

        self._prepare_base_columns()
        self._compute_indicators()
        self._compute_variables()

        entry_group = self._evaluate_group(self.config.get('conditions', {}).get('entry'))
        exit_group = self._evaluate_group(self.config.get('conditions', {}).get('exit'))

        signal = 'NONE'
        reason = '无信号'
        if entry_group['triggered']:
            signal = 'BUY'
            reason = '入场条件满足'
        elif exit_group['triggered']:
            signal = 'SELL'
            reason = '出场条件满足'

        latest = self.data.iloc[-1]
        latest_bar = {
            'timestamp': str(latest.name),
            'open': float(latest['open']),
            'high': float(latest['high']),
            'low': float(latest['low']),
            'close': float(latest['close']),
            'volume': float(latest['volume']),
        }

        indicator_snapshot = {}
        for name, series in self.indicators.items():
            try:
                value = series.iloc[-1]
                indicator_snapshot[name] = None if pd.isna(value) else float(value)
            except Exception:
                indicator_snapshot[name] = None

        return {
            'signal': signal,
            'entry_triggered': bool(entry_group['triggered']),
            'exit_triggered': bool(exit_group['triggered']),
            'reason': reason,
            'entry': entry_group,
            'exit': exit_group,
            'latest_bar': latest_bar,
            'indicators': indicator_snapshot,
        }

    def _prepare_base_columns(self):
        for column in ('open', 'high', 'low', 'close', 'volume'):
            if column in self.data.columns:
                self.data[column] = pd.to_numeric(self.data[column], errors='coerce')

    def _normalize_period(self, indicator: Dict[str, Any], default: int = 20) -> int:
        if indicator.get('period') is not None:
            return int(indicator.get('period'))
        period_ref = indicator.get('period_ref')
        if period_ref and period_ref in self.parameters:
            return int(self.parameters[period_ref])
        return default

    def _compute_indicators(self):
        for indicator in self.config.get('indicators', []):
            if not isinstance(indicator, dict):
                continue
            name = indicator.get('name')
            ind_type = str(indicator.get('type', '')).upper()
            if not name or not ind_type:
                continue

            source_name = indicator.get('source', 'close')
            source = self._resolve_series(source_name)
            if source is None:
                continue

            period = self._normalize_period(indicator)

            if ind_type == 'MA':
                self.indicators[name] = source.rolling(window=period).mean()
            elif ind_type == 'EMA':
                self.indicators[name] = source.ewm(span=period, adjust=False).mean()
            elif ind_type == 'WMA':
                weights = pd.Series(range(1, period + 1), dtype=float)
                self.indicators[name] = source.rolling(period).apply(
                    lambda values: (values * weights).sum() / weights.sum(), raw=False
                )
            elif ind_type == 'RSI':
                delta = source.diff()
                up = delta.clip(lower=0)
                down = -delta.clip(upper=0)
                avg_gain = up.rolling(period).mean()
                avg_loss = down.rolling(period).mean()
                rs = avg_gain / avg_loss.replace(0, pd.NA)
                self.indicators[name] = 100 - (100 / (1 + rs))

    def _compute_variables(self):
        for name, expr in (self.config.get('variables') or {}).items():
            series = self._expr_to_series(expr)
            if series is not None:
                self.variables[name] = series

    def _expr_to_series(self, expr: Any) -> Optional[pd.Series]:
        if isinstance(expr, pd.Series):
            return expr
        if expr is None:
            return None
        if isinstance(expr, (int, float)):
            return pd.Series([float(expr)] * len(self.data), index=self.data.index, dtype=float)
        if isinstance(expr, str):
            try:
                if expr.strip().replace('.', '', 1).isdigit():
                    return pd.Series([float(expr)] * len(self.data), index=self.data.index, dtype=float)
            except Exception:
                pass
            return self._resolve_series(expr)
        if isinstance(expr, dict):
            if 'func' in expr:
                return self._call_to_series(expr)
            if 'op' in expr:
                return self._binary_to_series(expr)
        return None

    def _call_to_series(self, expr: Dict[str, Any]) -> Optional[pd.Series]:
        func = str(expr.get('func', '')).lower()
        args = expr.get('args', []) or []

        if func == 'number' and args:
            return self._expr_to_series(args[0])

        if func in ('ma', 'ema', 'wma', 'rsi') and len(args) >= 2:
            source = self._expr_to_series(args[0])
            period = self._expr_to_scalar(args[1])
            if source is None or period is None:
                return None
            period = max(1, int(period))
            if func == 'ma':
                return source.rolling(window=period).mean()
            if func == 'ema':
                return source.ewm(span=period, adjust=False).mean()
            if func == 'wma':
                weights = pd.Series(range(1, period + 1), dtype=float)
                return source.rolling(period).apply(lambda values: (values * weights).sum() / weights.sum(), raw=False)
            if func == 'rsi':
                delta = source.diff()
                up = delta.clip(lower=0)
                down = -delta.clip(upper=0)
                avg_gain = up.rolling(period).mean()
                avg_loss = down.rolling(period).mean()
                rs = avg_gain / avg_loss.replace(0, pd.NA)
                return 100 - (100 / (1 + rs))
        return None

    def _binary_to_series(self, expr: Dict[str, Any]) -> Optional[pd.Series]:
        op = expr.get('op')
        left = self._expr_to_series(expr.get('left'))
        right = self._expr_to_series(expr.get('right'))
        if left is None or right is None:
            return None

        if op == '+':
            return left + right
        if op == '-':
            return left - right
        if op == '*':
            return left * right
        if op == '/':
            safe_right = right.replace(0, pd.NA)
            return left / safe_right
        if op == '>':
            return (left > right).astype(float)
        if op == '<':
            return (left < right).astype(float)
        if op == '>=':
            return (left >= right).astype(float)
        if op == '<=':
            return (left <= right).astype(float)
        if op == '==':
            return (left == right).astype(float)
        if op == '!=':
            return (left != right).astype(float)
        if str(op).lower() == 'and':
            return ((left > 0) & (right > 0)).astype(float)
        if str(op).lower() == 'or':
            return ((left > 0) | (right > 0)).astype(float)
        return None

    def _expr_to_scalar(self, expr: Any) -> Optional[float]:
        if isinstance(expr, (int, float)):
            return float(expr)
        if isinstance(expr, str):
            if expr in self.parameters:
                return float(self.parameters[expr])
            if expr.strip().replace('.', '', 1).isdigit():
                return float(expr)
            series = self._resolve_series(expr)
            if series is not None:
                value = series.iloc[-1]
                return None if pd.isna(value) else float(value)
            return None
        series = self._expr_to_series(expr)
        if series is None:
            return None
        value = series.iloc[-1]
        return None if pd.isna(value) else float(value)

    def _resolve_series(self, value: Any) -> Optional[pd.Series]:
        if isinstance(value, pd.Series):
            return value
        if isinstance(value, (int, float)):
            return pd.Series([float(value)] * len(self.data), index=self.data.index, dtype=float)
        if isinstance(value, str):
            if value in self.data.columns:
                return self.data[value].astype(float)
            if value in self.indicators:
                return self.indicators[value]
            if value in self.variables:
                return self.variables[value]
            if value in self.parameters:
                return pd.Series([float(self.parameters[value])] * len(self.data), index=self.data.index, dtype=float)
        if isinstance(value, dict):
            return self._expr_to_series(value)
        return None

    def _evaluate_group(self, group: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        group = group or {}
        rules = group.get('rules') if isinstance(group, dict) else []
        rules = rules or []
        group_type = str(group.get('type', 'AND')).upper() if isinstance(group, dict) else 'AND'

        results = [self._evaluate_rule(rule) for rule in rules]
        flags = [item['triggered'] for item in results]
        if not flags:
            triggered = False
        elif group_type == 'OR':
            triggered = any(flags)
        else:
            triggered = all(flags)

        return {
            'type': group_type,
            'triggered': bool(triggered),
            'rules': results,
        }

    def _evaluate_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        rule = rule or {}
        op = str(rule.get('op', '')).lower()
        rule_type = str(rule.get('type', '')).lower()
        rule_id = rule.get('id', 'rule')

        left_ref = rule.get('left', rule.get('indicator'))
        right_ref = rule.get('right', rule.get('ref', rule.get('value')))

        if rule_type == 'crossover' or op in ('cross_above', 'cross_below'):
            left = self._resolve_series(left_ref)
            right = self._resolve_series(right_ref)
            if left is None or right is None or len(left) < 2 or len(right) < 2:
                return {
                    'id': rule_id,
                    'triggered': False,
                    'type': 'crossover',
                    'op': op or 'cross',
                    'reason': '指标数据不足'
                }

            prev_left, curr_left = left.iloc[-2], left.iloc[-1]
            prev_right, curr_right = right.iloc[-2], right.iloc[-1]
            if any(pd.isna(v) for v in [prev_left, curr_left, prev_right, curr_right]):
                triggered = False
            elif op == 'cross_below':
                triggered = prev_left >= prev_right and curr_left < curr_right
            else:
                triggered = prev_left <= prev_right and curr_left > curr_right

            return {
                'id': rule_id,
                'triggered': bool(triggered),
                'type': 'crossover',
                'op': op or 'cross_above',
                'left': left_ref,
                'right': right_ref,
                'current_left': None if pd.isna(curr_left) else float(curr_left),
                'current_right': None if pd.isna(curr_right) else float(curr_right),
            }

        if op in ('>', '<', '>=', '<=', '==', '!=') or rule_type == 'binary':
            left_value = self._expr_to_scalar(left_ref)
            right_value = self._expr_to_scalar(right_ref)
            if left_value is None or right_value is None:
                return {
                    'id': rule_id,
                    'triggered': False,
                    'type': 'binary',
                    'op': op,
                    'reason': '无法解析比较值'
                }

            triggered = False
            if op == '>':
                triggered = left_value > right_value
            elif op == '<':
                triggered = left_value < right_value
            elif op == '>=':
                triggered = left_value >= right_value
            elif op == '<=':
                triggered = left_value <= right_value
            elif op == '==':
                triggered = math.isclose(left_value, right_value)
            elif op == '!=':
                triggered = not math.isclose(left_value, right_value)

            return {
                'id': rule_id,
                'triggered': bool(triggered),
                'type': 'binary',
                'op': op,
                'left': left_ref,
                'right': right_ref,
                'current_left': left_value,
                'current_right': right_value,
            }

        value = self._expr_to_scalar(left_ref)
        triggered = bool(value)
        return {
            'id': rule_id,
            'triggered': bool(triggered),
            'type': rule_type or 'value',
            'op': op,
            'value': value,
        }
