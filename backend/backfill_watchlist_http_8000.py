import json
import urllib.request

BASE = 'http://127.0.0.1:8000/api/history/backfill'
HEADERS = {'Content-Type': 'application/json'}

symbols = [
    ('300750.SZ', 'futu'),
    ('00883.HK', 'futu'),
    ('TSLA', 'yahoo'),
    ('GOOGL', 'yahoo'),
    ('NVDA', 'yahoo'),
    ('AMD', 'yahoo'),
]

for symbol, source in symbols:
    payload = {
        'symbol': symbol,
        'timeframe': '1d',
        'start_date': '2025-03-19T00:00:00',
        'end_date': '2026-03-19T00:00:00',
        'host': '127.0.0.1',
        'port': 11111,
        'source': source,
    }
    req = urllib.request.Request(BASE, data=json.dumps(payload).encode('utf-8'), headers=HEADERS, method='POST')
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(symbol, resp.read().decode('utf-8'))
