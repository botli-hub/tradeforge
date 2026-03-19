from app.core.formula.parser import parse_formula
from app.core.formula.transpiler import transpile_formula
import json

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
entry = cross_above(ma_fast, ma_slow)
exit = cross_below(ma_fast, ma_slow)
'''

ast = parse_formula(code)
print('AST_NAME:', ast.name)
print('AST_PARAMS:', {k: {'name': v.name, 'default': v.default, 'min': v.min_val, 'max': v.max_val} for k, v in ast.params.items()})

ir = transpile_formula(code)
print('IR:')
print(json.dumps(ir, ensure_ascii=False, indent=2))
