import json
import urllib.request

base = 'http://127.0.0.1:8000'
headers = {'Content-Type': 'application/json'}

payload = {
    'name': 'Signal Engine Test',
    'config': {
        'version': '1.0',
        'mode': 'visual',
        'name': 'Signal Engine Test',
        'symbols': ['AAPL'],
        'timeframe': '1d',
        'indicators': [
            {'name': 'ma_fast', 'type': 'MA', 'period': 20, 'source': 'close'},
            {'name': 'ma_slow', 'type': 'MA', 'period': 50, 'source': 'close'}
        ],
        'conditions': {
            'entry': {
                'type': 'AND',
                'rules': [
                    {'id': 'entry_1', 'type': 'crossover', 'op': 'cross_above', 'left': 'ma_fast', 'right': 'ma_slow'}
                ]
            },
            'exit': {
                'type': 'OR',
                'rules': [
                    {'id': 'exit_1', 'type': 'crossover', 'op': 'cross_below', 'left': 'ma_fast', 'right': 'ma_slow'}
                ]
            }
        },
        'position_sizing': {'type': 'fixed_amount', 'value': 10000},
        'risk_rules': {'initial_capital': 100000, 'fee_rate': 0.0003, 'slippage': 0.001, 'max_position_pct': 0.5}
    }
}

req = urllib.request.Request(base + '/api/strategies', data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
with urllib.request.urlopen(req, timeout=10) as resp:
    created = json.loads(resp.read().decode('utf-8'))
    print('created', created)

strategy_id = created['id']
with urllib.request.urlopen(base + f'/api/strategies/{strategy_id}/signal?symbol=AAPL&adapter=mock&host=127.0.0.1&port=11111', timeout=15) as resp:
    print(resp.read().decode('utf-8'))
