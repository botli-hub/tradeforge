import json
import urllib.request

BASE = 'http://127.0.0.1:8000'
HEADERS = {'Content-Type': 'application/json'}

payloads = [
    {
        'symbol': 'AAPL',
        'timeframe': '1d',
        'start_date': '2026-02-15T00:00:00',
        'end_date': '2026-03-19T00:00:00',
        'source': 'yahoo',
    },
    {
        'symbol': '600519.SH',
        'timeframe': '1d',
        'start_date': '2026-02-15T00:00:00',
        'end_date': '2026-03-19T00:00:00',
        'source': 'futu',
    },
]

for payload in payloads:
    req = urllib.request.Request(
        BASE + '/api/history/backfill',
        data=json.dumps(payload).encode('utf-8'),
        headers=HEADERS,
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print('BACKFILL', payload['symbol'], resp.read().decode('utf-8'))

for url in [
    BASE + '/api/history/coverage?symbol=AAPL&timeframe=1d',
    BASE + '/api/history/coverage?symbol=600519.SH&timeframe=1d',
    BASE + '/api/market/klines?symbol=AAPL&timeframe=1d&limit=3&adapter=finnhub',
    BASE + '/api/market/klines?symbol=600519.SH&timeframe=1d&limit=3&adapter=finnhub&host=127.0.0.1&port=11111',
]:
    with urllib.request.urlopen(url, timeout=30) as resp:
        print('GET', url)
        print(resp.read().decode('utf-8')[:1200])
