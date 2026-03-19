"""Formula DSL 语法分析器"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from app.core.formula.lexer import Lexer, Token

@dataclass
class ASTNode:
    """AST节点基类"""
    pass

@dataclass
class StrategyNode(ASTNode):
    """策略定义节点"""
    name: str
    capital: float = 100000
    fee: float = 0.0003
    params: Dict[str, 'ParamNode'] = field(default_factory=dict)
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class ParamNode(ASTNode):
    """参数节点"""
    name: str
    default: float
    min_val: float
    max_val: float

@dataclass
class AssignmentNode(ASTNode):
    """赋值语句节点"""
    target: str
    value: 'ASTNode'

@dataclass
class IndicatorNode(ASTNode):
    """指标节点"""
    name: str
    func: str
    args: List[Any]

@dataclass
class IfNode(ASTNode):
    """条件节点"""
    condition: 'ASTNode'
    body: List[ASTNode]
    else_body: List[ASTNode] = field(default_factory=list)

@dataclass
class BinaryOpNode(ASTNode):
    """二元运算节点"""
    op: str
    left: 'ASTNode'
    right: 'ASTNode'

@dataclass
class CallNode(ASTNode):
    """函数调用节点"""
    func: str
    args: List[Any]

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
    
    def current(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token('EOF', '', 0, 0)
    
    def peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else Token('EOF', '', 0, 0)
    
    def eat(self, expected_type: str) -> Token:
        if self.current().type != expected_type:
            raise SyntaxError(f"Expected {expected_type}, got {self.current().type} at line {self.current().line}")
        token = self.current()
        self.pos += 1
        return token
    
    def parse(self) -> StrategyNode:
        """解析策略"""
        # strategy(...)
        self.eat('STRATEGY')
        self.eat('LPAREN')
        
        # 解析策略名称
        name = self.eat('STRING').value.strip('"')
        
        # 解析参数
        capital = 100000
        fee = 0.0003
        while self.current().type != 'RPAREN':
            if self.current().type == 'IDENTIFIER':
                key = self.eat('IDENTIFIER').value
                self.eat('OPERATOR')  # =
                if key == 'capital':
                    capital = float(self.eat('NUMBER').value)
                elif key == 'fee':
                    fee = float(self.eat('NUMBER').value)
            if self.current().type == 'COMMA':
                self.eat('COMMA')
        
        self.eat('RPAREN')
        
        # 创建策略节点
        strategy = StrategyNode(name=name, capital=capital, fee=fee)
        
        # 解析函数体
        strategy.body = self._parse_body()

        # 提取参数定义
        for node in strategy.body:
            if isinstance(node, AssignmentNode) and isinstance(node.value, CallNode) and node.value.func == 'param':
                args = node.value.args
                if len(args) >= 4:
                    strategy.params[node.target] = ParamNode(
                        name=str(args[0]),
                        default=float(args[1]),
                        min_val=float(args[2]),
                        max_val=float(args[3])
                    )
        
        return strategy
    
    def _parse_body(self) -> List[ASTNode]:
        """解析函数体"""
        body = []
        
        while self.current().type not in ('EOF', 'RBRACE'):
            # 跳过空行
            if self.current().type == 'NEWLINE':
                self.eat('NEWLINE')
                continue
            
            if self.current().type == 'IDENTIFIER':
                # 解析赋值或函数调用
                node = self._parse_statement()
                body.append(node)
            elif self.current().type == 'IF':
                body.append(self._parse_if())
            elif self.current().type == 'NEWLINE':
                self.eat('NEWLINE')
            else:
                self.eat(self.current().type)  # 跳过未知token
        
        return body
    
    def _parse_statement(self) -> ASTNode:
        """解析语句"""
        # param(...) 或 变量 = ...
        if self.current().type == 'PARAM':
            return self._parse_param()
        elif self.peek().type == 'OPERATOR':
            return self._parse_assignment()
        elif self.peek().type == 'LPAREN':
            return self._parse_call()
        else:
            # 跳过
            token = self.eat(self.current().type)
            return AssignmentNode(token.value, CallNode(token.value, []))
    
    def _parse_param(self) -> ParamNode:
        """解析参数定义"""
        self.eat('PARAM')
        self.eat('LPAREN')
        
        name = self.eat('STRING').value.strip('"')
        
        # 处理逗号分隔的参数
        self.eat('COMMA')
        default = float(self.eat('NUMBER').value)
        
        self.eat('COMMA')
        min_val = float(self.eat('NUMBER').value)
        
        self.eat('COMMA')
        max_val = float(self.eat('NUMBER').value)
        
        self.eat('RPAREN')
        
        return ParamNode(name, default, min_val, max_val)
    
    def _parse_assignment(self) -> AssignmentNode:
        """解析赋值语句"""
        target = self.eat('IDENTIFIER').value
        self.eat('OPERATOR')  # =
        value = self._parse_expression()
        return AssignmentNode(target, value)
    
    def _parse_if(self) -> IfNode:
        """解析if语句"""
        self.eat('IF')
        condition = self._parse_expression()
        self.eat('COLON')
        
        body = self._parse_body()
        
        else_body = []
        if self.current().type == 'ELSE':
            self.eat('ELSE')
            self.eat('COLON')
            else_body = self._parse_body()
        
        return IfNode(condition, body, else_body)
    
    def _parse_expression(self) -> ASTNode:
        """解析表达式"""
        return self._parse_or()
    
    def _parse_or(self) -> ASTNode:
        """解析 or 表达式"""
        left = self._parse_and()
        
        while self.current().type == 'OR':
            op = self.eat('OR').value
            right = self._parse_and()
            left = BinaryOpNode(op, left, right)
        
        return left
    
    def _parse_and(self) -> ASTNode:
        """解析 and 表达式"""
        left = self._parse_comparison()
        
        while self.current().type == 'AND':
            op = self.eat('AND').value
            right = self._parse_comparison()
            left = BinaryOpNode(op, left, right)
        
        return left
    
    def _parse_comparison(self) -> ASTNode:
        """解析比较表达式"""
        left = self._parse_primary()
        
        while self.current().type in ('OPERATOR', '>', '<', '>=', '<=', '==', '!='):
            op = self.eat(self.current().type).value
            right = self._parse_primary()
            left = BinaryOpNode(op, left, right)
        
        return left
    
    def _parse_primary(self) -> ASTNode:
        """解析基本表达式"""
        token = self.current()
        
        if token.type == 'NUMBER':
            self.eat('NUMBER')
            return float(token.value)

        elif token.type == 'STRING':
            self.eat('STRING')
            return token.value.strip('"')
        
        elif token.type in ('IDENTIFIER', 'PARAM', 'BUY', 'SELL', 'SELL_ALL'):
            self.eat(token.type)
            # 检查是否是函数调用
            if self.current().type == 'LPAREN':
                return self._parse_call_with_name(token.value.lower())
            return token.value
        
        elif token.type == 'LPAREN':
            self.eat('LPAREN')
            expr = self._parse_expression()
            self.eat('RPAREN')
            return expr
        
        else:
            self.eat(self.current().type)
            return CallNode('unknown', [])
    
    def _parse_call(self) -> CallNode:
        """解析函数调用"""
        func = self.eat('IDENTIFIER').value
        return self._parse_call_with_name(func)
    
    def _parse_call_with_name(self, func: str) -> CallNode:
        """解析函数调用（已知函数名）"""
        self.eat('LPAREN')
        args = []
        
        while self.current().type != 'RPAREN':
            args.append(self._parse_expression())
            if self.current().type == 'COMMA':
                self.eat('COMMA')
        
        self.eat('RPAREN')
        return CallNode(func, args)


def parse_formula(code: str) -> StrategyNode:
    """解析 Formula 代码"""
    lexer = Lexer()
    tokens = lexer.tokenize(code)
    parser = Parser(tokens)
    return parser.parse()
