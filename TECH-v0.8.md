# TradeForge v0.8 技术设计文档

---

## 一、系统架构

### 1.1 整体架构（v0.8）

```
┌─────────────────────────────────────────────────────────────────┐
│                         TradeForge v0.8                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────┐     ┌──────────────────────┐        │
│  │      前端 (Tauri)     │     │    后端 (Python)   │        │
│  │                      │     │                      │        │
│  │  React + TypeScript │◀───▶│  FastAPI + SQLite   │        │
│  │  Monaco Editor      │     │  Backtest Engine    │        │
│  │  lightweight-charts │     │  Formula Parser     │        │
│  │  react-flow         │     │  MultiSymbol Engine │        │
│  └──────────────────────┘     └──────────────────────┘        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 新增模块

| 模块 | 说明 |
|------|------|
| Formula Parser | DSL 语法解析器 |
| Formula Transpiler | Formula → IR 转译器 |
| MultiSymbol Engine | 多标的回测引擎 |

---

## 二、Formula Parser 设计

### 2.1 词法分析

```python
# app/core/formula/lexer.py
from typing import List, Tuple
import re

class Token:
    def __init__(self, type: str, value: str, line: int, col: int):
        self.type = type
        self.value = value
        self.line = line
        self.col = col

class Lexer:
    KEYWORDS = {'strategy', 'param', 'if', 'else', 'and', 'or', 'not'}
    
    TOKEN_REGEX = [
        ('STRING', r'"[^"]*"'),
        ('NUMBER', r'\d+\.?\d*'),
        ('IDENTIFIER', r'[a-zA-Z_][a-zA-Z0-9_]*'),
        ('OPERATOR', r'[+\-*/%=<>!]+'),
        ('LPAREN', r'\('),
        ('RPAREN', r'\)'),
        ('COLON', r':'),
        ('NEWLINE', r'\n'),
        ('SKIP', r'[ \t]+'),
    ]
    
    def tokenize(self, code: str) -> List[Token]:
        tokens = []
        lines = code.split('\n')
        
        for line_num, line in enumerate(lines):
            col = 0
            while col < len(line):
                matched = False
                for token_type, pattern in self.TOKEN_REGEX:
                    regex = re.compile(pattern)
                    match = regex.match(line, col)
                    if match:
                        value = match.group(0)
                        if token_type == 'SKIP':
                            col = match.end()
                            continue
                        if token_type == 'IDENTIFIER' and value in self.KEYWORDS:
                            token_type = value.upper()
                        tokens.append(Token(token_type, value, line_num + 1, col + 1))
                        col = match.end()
                        matched = True
                        break
                if not matched:
                    raise SyntaxError(f"Unknown token at line {line_num + 1}, col {col + 1}")
        
        return tokens
```

### 2.2 语法分析

```python
# app/core/formula/parser.py
from typing import List, Dict, Any, Optional
from app.core.formula.lexer import Lexer, Token

class ASTNode:
    pass

class StrategyNode(ASTNode):
    def __init__(self, name: str, params: Dict, body: List[ASTNode]):
        self.name = name
        self.params = params
        self.body = body

class ParamNode(ASTNode):
    def __init__(self, name: str, default: float, min_val: float, max_val: float):
        self.name = name
        self.default = default
        self.min = min_val
        self.max = max_val

class AssignmentNode(ASTNode):
    def __init__(self, target: str, value: ASTNode):
        self.target = target
        self.value = value

class IfNode(ASTNode):
    def __init__(self, condition: ASTNode, body: List[ASTNode]):
        self.condition = condition
        self.body = body

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
    
    def parse(self) -> StrategyNode:
        """解析策略"""
        # 解析 strategy() 调用
        self.eat('STRATEGY')
        self.eat('LPAREN')
        name = self.eat('STRING').value.strip('"')
        self.eat('RPAREN')
        
        # 解析参数
        params = self._parse_params()
        
        # 解析函数体
        body = self._parse_body()
        
        return StrategyNode(name, params, body)
    
    def _parse_params(self) -> Dict:
        params = {}
        while self.current().type in ('PARAM', 'IDENTIFIER'):
            if self.current().type == 'PARAM':
                self.eat('PARAM')
                self.eat('LPAREN')
                name = self.eat('STRING').value.strip('"')
                default = float(self.eat('NUMBER').value)
                min_val = float(self.eat('NUMBER').value)
                max_val = float(self.eat('NUMBER').value)
                self.eat('RPAREN')
                params[name] = ParamNode(name, default, min_val, max_val)
            else:
                # 解析其他赋值
                break
        return params
    
    def _parse_body(self) -> List[ASTNode]:
        body = []
        while self.current().type != 'EOF':
            if self.current().type == 'IDENTIFIER':
                # 解析赋值语句
                target = self.eat('IDENTIFIER').value
                self.eat('OPERATOR')  # =
                value = self._parse_expression()
                body.append(AssignmentNode(target, value))
            elif self.current().type == 'IF':
                body.append(self._parse_if())
            elif self.current().type == 'NEWLINE':
                self.eat('NEWLINE')
        return body
    
    def current(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token('EOF', '', 0, 0)
    
    def eat(self, expected_type: str) -> Token:
        if self.current().type != expected_type:
            raise SyntaxError(f"Expected {expected_type}, got {self.current().type}")
        token = self.current()
        self.pos += 1
        return token
```

---

## 三、Formula Transpiler 设计

### 3.1 转译流程

```
Formula 代码
    ↓
Lexer (词法分析)
    ↓
Parser (语法分析) → AST
    ↓
Transformer (语义分析)
    ↓
IR Generator (生成 IR)
    ↓
统一 IR (JSON)
```

### 3.2 转译器实现

```python
# app/core/formula/transpiler.py
from typing import Dict, Any, List
from app.core.formula.parser import Parser, StrategyNode, AssignmentNode, IfNode

class Transpiler:
    def __init__(self, code: str):
        self.code = code
        self.variables = {}
        self.indicators = []
        self.entry_conditions = []
        self.exit_conditions = []
    
    def transpile(self) -> Dict[str, Any]:
        """转译为 IR"""
        # 词法分析
        from app.core.formula.lexer import Lexer
        lexer = Lexer()
        tokens = lexer.tokenize(self.code)
        
        # 语法分析
        parser = Parser(tokens)
        ast = parser.parse()
        
        # 生成 IR
        ir = {
            "version": "1.0",
            "strategy_id": "",
            "mode": "formula",
            "name": ast.name,
            "symbols": [],
            "timeframe": "1d",
            "source_code": self.code,
            "parameters": [],
            "indicators": [],
            "conditions": {
                "entry": {"type": "AND", "rules": []},
                "exit": {"type": "OR", "rules": []}
            },
            "position_sizing": {"type": "fixed_amount", "value": 10000},
            "risk_rules": {}
        }
        
        # 遍历 AST 生成 IR
        for node in ast.body:
            if isinstance(node, AssignmentNode):
                self._process_assignment(node, ir)
            elif isinstance(node, IfNode):
                self._process_if(node, ir)
        
        # 处理参数
        for name, param in ast.params.items():
            ir["parameters"].append({
                "name": name,
                "default": param.default,
                "min": param.min,
                "max": param.max
            })
        
        return ir
    
    def _process_assignment(self, node: AssignmentNode, ir: Dict):
        """处理赋值语句"""
        target = node.target
        value = node.value
        
        # 检测指标
        if isinstance(value, str) and 'MA(' in value:
            # 提取 MA 参数
            import re
            match = re.search(r'MA\((\w+),\s*(\w+)\)', value)
            if match:
                source, period = match.groups()
                ir["indicators"].append({
                    "name": target,
                    "type": "MA",
                    "source": source,
                    "period_ref": period
                })
    
    def _process_if(self, node: IfNode, ir: Dict):
        """处理 if 语句"""
        # 检测买入/卖出信号
        # 转换 conditions
        pass
```

---

## 四、多标的回测引擎

### 4.1 架构

```python
# app/core/multi_engine.py
from typing import List, Dict
import pandas as pd
from app.core.engine import BacktestEngine

class MultiSymbolEngine:
    """多标的回测引擎"""
    
    def __init__(self, ir_config: dict, symbols: List[str], data: Dict[str, pd.DataFrame]):
        self.ir_config = ir_config
        self.symbols = symbols
        self.data = data
        self.mode = ir_config.get("position_mode", "shared")  # shared / independent
        
        # 共享资金池
        self.total_capital = ir_config.get("risk_rules", {}).get("initial_capital", 100000)
        self.max_position_pct = ir_config.get("risk_rules", {}).get("max_position_pct", 0.5)
        
        # 每个标的的引擎
        self.engines = {}
        
    def run(self) -> Dict:
        """执行多标的回测"""
        results = {}
        
        for symbol in self.symbols:
            if symbol not in self.data:
                continue
            
            # 创建单标的引擎
            engine = BacktestEngine(self.ir_config, self.data[symbol])
            result = engine.run()
            results[symbol] = result
        
        # 汇总
        return self._aggregate_results(results)
    
    def _aggregate_results(self, results: Dict) -> Dict:
        """汇总结果"""
        total_trades = sum(r.get("total_trades", 0) for r in results.values())
        total_pnl = sum(
            sum(t.get("pnl", 0) for t in r.get("trades", []))
            for r in results.values()
        )
        
        # 计算组合收益
        initial = self.ir_config.get("risk_rules", {}).get("initial_capital", 100000)
        total_return = total_pnl / initial
        
        return {
            "total_return": total_return,
            "total_trades": total_trades,
            "symbol_results": results,
            "equity_curve": self._merge_equity_curves(results)
        }
    
    def _merge_equity_curves(self, results: Dict) -> List[Dict]:
        """合并资金曲线"""
        # 按时间对齐合并
        merged = {}
        for symbol, result in results.items():
            for point in result.get("equity_curve", []):
                ts = point["timestamp"]
                if ts not in merged:
                    merged[ts] = 0
                merged[ts] += point["equity"]
        
        return [{"timestamp": k, "equity": v} for k, v in sorted(merged.items())]
```

---

## 五、前端 Formula 编辑器

### 5.1 Monaco Editor 集成

```typescript
// pages/FormulaEditor.tsx
import { useRef, useEffect } from 'react'
import * as monaco from 'monaco-editor'

export default function FormulaEditor() {
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  
  useEffect(() => {
    if (!containerRef.current) return
    
    // 创建编辑器
    editorRef.current = monaco.editor.create(containerRef.current, {
      value: getDefaultCode(),
      language: 'python',  // 用 Python 语法高亮
      theme: 'vs-dark',
      minimap: { enabled: false },
      fontSize: 14,
      lineNumbers: 'on',
      scrollBeyondLastLine: false,
    })
    
    // 自定义自动补全
    monaco.languages.registerCompletionItemProvider('python', {
      provideCompletionItems: () => {
        const suggestions = [
          { label: 'MA', kind: monaco.languages.CompletionItemKind.Function, insertText: 'MA(${1:close}, ${2:20})' },
          { label: 'EMA', kind: monaco.languages.CompletionItemKind.Function, insertText: 'EMA(${1:close}, ${2:20})' },
          { label: 'RSI', kind: monaco.languages.CompletionItemKind.Function, insertText: 'RSI(${1:close}, ${2:14})' },
          { label: 'ATR', kind: monaco.languages.CompletionItemKind.Function, insertText: 'ATR(${1:14})' },
          { label: 'cross_above', kind: monaco.languages.CompletionItemKind.Function, insertText: 'cross_above(${1:series1}, ${2:series2})' },
          { label: 'cross_below', kind: monaco.languages.CompletionItemKind.Function, insertText: 'cross_below(${1:series1}, ${2:series2})' },
          { label: 'param', kind: monaco.languages.CompletionItemKind.Snippet, insertText: 'param("${1:name}", ${2:default}, ${3:min}, ${4:max})' },
          { label: 'buy', kind: monaco.languages.CompletionItemKind.Function, insertText: 'buy(${1:size})' },
          { label: 'sell', kind: monaco.languages.CompletionItemKind.Function, insertText: 'sell(${1:size})' },
        ]
        return { suggestions }
      }
    })
    
    return () => {
      editorRef.current?.dispose()
    }
  }, [])
  
  function getDefaultCode() {
    return `strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)

entry = cross_above(ma_fast, ma_slow)
exit = cross_below(ma_fast, ma_slow)

if entry:
    buy(100)

if exit:
    sell_all()
`
  }
  
  return <div ref={containerRef} style={{ width: '100%', height: '400px' }} />
}
```

### 5.2 参数面板

```typescript
// components/ParameterPanel.tsx
interface Parameter {
  name: string
  default: number
  min: number
  max: number
}

export function ParameterPanel({ parameters, values, onChange }: {
  parameters: Parameter[]
  values: Record<string, number>
  onChange: (name: string, value: number) => void
}) {
  return (
    <div className="parameter-panel">
      {parameters.map(p => (
        <div key={p.name} className="parameter-row">
          <label>{p.name}</label>
          <input 
            type="range" 
            min={p.min} 
            max={p.max}
            value={values[p.name] ?? p.default}
            onChange={e => onChange(p.name, Number(e.target.value))}
          />
          <span>{values[p.name] ?? p.default}</span>
        </div>
      ))}
    </div>
  )
}
```

---

## 六、数据模型扩展

### 6.1 SQLite 表扩展

```sql
-- 多标的配置表
CREATE TABLE IF NOT EXISTS symbol_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbols TEXT NOT NULL,  -- JSON array
    position_mode TEXT DEFAULT 'shared',
    created_at TEXT NOT NULL
);

-- 策略版本表扩展
ALTER TABLE strategy_versions ADD COLUMN source_code TEXT;
ALTER TABLE strategy_versions ADD COLUMN parameters TEXT;  -- JSON
```

### 6.2 IR 扩展

```json
{
  "version": "1.0",
  "mode": "formula",
  "source_code": "strategy('MA Cross')...",
  "parameters": [
    {"name": "fast", "default": 5, "min": 2, "max": 50},
    {"name": "slow", "default": 20, "min": 5, "max": 200}
  ],
  "symbols": ["AAPL", "TSLA"],
  "position_mode": "shared",
  "indicators": [...],
  "conditions": {...},
  "risk_rules": {...}
}
```

---

## 七、API 扩展

### 7.1 新增 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/strategies/validate | 验证 Formula 代码 |
| POST | /api/strategies/parse | 解析 Formula 获取参数 |
| GET | /api/symbol-groups | 获取标的组合 |
| POST | /api/symbol-groups | 创建标的组合 |

---

## 八、性能优化

### 8.1 多标的并行

```python
from concurrent.futures import ThreadPoolExecutor

def run_parallel(symbols, data, config):
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            symbol: executor.submit(run_single, symbol, data[symbol], config)
            for symbol in symbols
        }
        return {s: f.result() for s, f in futures.items()}
```

---

*版本: v0.8 | 2026.03.19*
