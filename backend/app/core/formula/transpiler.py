"""Formula DSL 转译器 - 将 Formula 代码转为 IR"""
import uuid
from typing import Dict, Any, List
from app.core.formula.parser import (
    Parser, ParamNode, AssignmentNode, CallNode, BinaryOpNode
)
from app.core.formula.lexer import Lexer


class Transpiler:
    """Formula → IR 转译器"""

    def __init__(self, code: str):
        self.code = code
        self.variables: Dict[str, Any] = {}
        self.params: Dict[str, ParamNode] = {}

    def transpile(self) -> Dict[str, Any]:
        """转译为 IR"""
        lexer = Lexer()
        tokens = lexer.tokenize(self.code)
        parser = Parser(tokens)
        ast = parser.parse()

        self.params = dict(ast.params)
        self._extract_variables(ast.body)

        indicators = self._extract_indicators()
        variables = self._extract_variable_expressions(indicators)

        return {
            "version": "1.0",
            "strategy_id": str(uuid.uuid4()),
            "mode": "formula",
            "name": ast.name,
            "symbols": ["AAPL"],
            "timeframe": "1d",
            "source_code": self.code,
            "parameters": [
                {
                    "name": name,
                    "label": param.name,
                    "default": param.default,
                    "min": param.min_val,
                    "max": param.max_val,
                }
                for name, param in self.params.items()
            ],
            "indicators": indicators,
            "variables": variables,
            "conditions": self._extract_conditions(),
            "position_sizing": {
                "type": "fixed_amount",
                "value": 10000
            },
            "risk_rules": {
                "initial_capital": ast.capital,
                "fee_rate": ast.fee,
                "slippage": 0.001,
                "max_position_pct": 0.5
            }
        }

    def _extract_variables(self, body: List[Any]):
        """提取变量"""
        for node in body:
            if isinstance(node, AssignmentNode):
                if isinstance(node.value, CallNode) and node.value.func == 'param':
                    continue
                self.variables[node.target] = node.value

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, CallNode):
            return {
                "func": value.func,
                "args": [self._normalize_value(arg) for arg in value.args]
            }
        if isinstance(value, BinaryOpNode):
            return {
                "op": value.op,
                "left": self._normalize_value(value.left),
                "right": self._normalize_value(value.right)
            }
        return value

    def _extract_indicators(self) -> List[Dict[str, Any]]:
        """提取指标定义"""
        indicators: List[Dict[str, Any]] = []

        for var_name, value in self.variables.items():
            if not isinstance(value, CallNode):
                continue

            func = value.func.upper()
            if func not in ('MA', 'EMA', 'WMA', 'RSI', 'MACD', 'ATR', 'BBANDS'):
                continue

            indicator: Dict[str, Any] = {
                "name": var_name,
                "type": func,
            }

            if len(value.args) >= 1:
                indicator["source"] = self._normalize_value(value.args[0])

            if len(value.args) >= 2:
                arg = value.args[1]
                if isinstance(arg, str) and arg in self.params:
                    indicator["period_ref"] = arg
                elif isinstance(arg, (int, float)):
                    indicator["period"] = int(arg)
                else:
                    indicator["period_value"] = self._normalize_value(arg)

            if len(value.args) > 2:
                indicator["args"] = [self._normalize_value(arg) for arg in value.args[2:]]

            indicators.append(indicator)

        return indicators

    def _extract_variable_expressions(self, indicators: List[Dict[str, Any]]) -> Dict[str, Any]:
        """提取普通变量表达式（供实时信号引擎使用）"""
        indicator_names = {item.get('name') for item in indicators}
        variables: Dict[str, Any] = {}

        for var_name, value in self.variables.items():
            if var_name in indicator_names:
                continue
            if var_name in ('entry', 'exit'):
                continue
            variables[var_name] = self._normalize_value(value)

        return variables

    def _rule_from_node(self, node: Any, rule_id: str) -> Dict[str, Any]:
        if isinstance(node, CallNode):
            if node.func in ('cross_above', 'cross_below'):
                return {
                    "id": rule_id,
                    "type": "crossover",
                    "op": node.func,
                    "left": self._normalize_value(node.args[0]) if len(node.args) > 0 else None,
                    "right": self._normalize_value(node.args[1]) if len(node.args) > 1 else None,
                }
            return {
                "id": rule_id,
                "type": "call",
                "func": node.func,
                "args": [self._normalize_value(arg) for arg in node.args]
            }

        if isinstance(node, BinaryOpNode):
            return {
                "id": rule_id,
                "type": "binary",
                "op": node.op,
                "left": self._normalize_value(node.left),
                "right": self._normalize_value(node.right),
            }

        return {
            "id": rule_id,
            "type": "value",
            "value": self._normalize_value(node)
        }

    def _extract_conditions(self) -> Dict[str, Any]:
        """提取条件"""
        conditions = {
            "entry": {"type": "AND", "rules": []},
            "exit": {"type": "OR", "rules": []}
        }

        for condition_name in ('entry', 'exit'):
            node = self.variables.get(condition_name)
            if node is None:
                continue

            group = conditions[condition_name]
            if isinstance(node, BinaryOpNode) and str(node.op).lower() in ('and', 'or'):
                group["type"] = str(node.op).upper()
                group["rules"] = [
                    self._rule_from_node(node.left, f"{condition_name}_1"),
                    self._rule_from_node(node.right, f"{condition_name}_2"),
                ]
            else:
                group["rules"] = [self._rule_from_node(node, f"{condition_name}_1")]

        return conditions


def transpile_formula(code: str) -> Dict[str, Any]:
    """转译 Formula 代码为 IR"""
    transpiler = Transpiler(code)
    return transpiler.transpile()
