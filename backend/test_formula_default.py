from app.core.formula.parser import parse_formula
from app.core.formula.transpiler import transpile_formula
import json

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
vol_ratio = volume / MA(volume, 20)

entry = cross_above(ma_fast, ma_slow) and vol_ratio > 1.5
exit = cross_below(ma_fast, ma_slow)

if entry:
    buy(100)

if exit:
    sell_all()
'''

ast = parse_formula(code)
print('AST OK:', ast.name)
print('PARAMS:', list(ast.params.keys()))
print(json.dumps(transpile_formula(code), ensure_ascii=False, indent=2))
