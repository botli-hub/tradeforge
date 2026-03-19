from app.core.formula.lexer import Lexer

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
entry = cross_above(ma_fast, ma_slow)
exit = cross_below(ma_fast, ma_slow)
'''

lexer = Lexer()
tokens = lexer.tokenize(code)
print(len(tokens))
for t in tokens:
    print(t)
