import json
import urllib.request

base = 'http://127.0.0.1:8000/api/trading'
headers = {'Content-Type': 'application/json'}

calls = [
    ('/connect', {'adapter': 'mock', 'trd_env': 'SIM', 'host': '127.0.0.1', 'port': 11111}),
    ('/status', None),
    ('/order', {'symbol': 'AAPL', 'side': 'BUY', 'quantity': 100, 'price': 185, 'order_type': 'LIMIT'}),
    ('/orders', None),
    ('/positions', None),
    ('/account', None),
]

for path, payload in calls:
    method = 'POST' if payload is not None else 'GET'
    req = urllib.request.Request(
        base + path,
        data=None if payload is None else json.dumps(payload).encode('utf-8'),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(path, resp.read().decode('utf-8'))
