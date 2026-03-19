import json
import urllib.request
from app.core.formula.transpiler import transpile_formula

base = 'http://127.0.0.1:8000'
headers = {'Content-Type': 'application/json'}

code = '''strategy("Formula Signal Test", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
vol_ratio = volume / MA(volume, 20)

entry = cross_above(ma_fast, ma_slow) and vol_ratio > 1.0
exit = cross_below(ma_fast, ma_slow)
'''

ir = transpile_formula(code)
ir['symbols'] = ['AAPL']

payload = {
    'name': 'Formula Signal Test',
    'config': ir,
}

req = urllib.request.Request(base + '/api/strategies', data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
with urllib.request.urlopen(req, timeout=10) as resp:
    created = json.loads(resp.read().decode('utf-8'))
    print('created', created)

strategy_id = created['id']
with urllib.request.urlopen(base + f'/api/strategies/{strategy_id}/signal?symbol=AAPL&adapter=mock&host=127.0.0.1&port=11111', timeout=20) as resp:
    print(resp.read().decode('utf-8'))
