import asyncio
from app.api.formula import (
    validate_formula,
    parse_formula_request,
    transpile_formula_request,
    FormulaValidateRequest,
    FormulaParseRequest,
    FormulaTranspileRequest,
)

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
vol_ratio = volume / MA(volume, 20)

entry = cross_above(ma_fast, ma_slow) and vol_ratio > 1.5
exit = cross_below(ma_fast, ma_slow)
'''

async def main():
    print(await validate_formula(FormulaValidateRequest(code=code)))
    print(await parse_formula_request(FormulaParseRequest(code=code)))
    print(await transpile_formula_request(FormulaTranspileRequest(code=code)))

asyncio.run(main())
