from app.core.formula.parser import parse_formula

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
entry = cross_above(ma_fast, ma_slow)
exit = cross_below(ma_fast, ma_slow)
'''

ast = parse_formula(code)
print(ast.name)
print(ast.params)
