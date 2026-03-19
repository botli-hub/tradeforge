import urllib.request
import json

base = 'http://127.0.0.1:8000/api/market'
paths = [
    '/status?adapter=mock&host=127.0.0.1&port=11111',
    '/search?q=AAPL&adapter=mock',
    '/quote?symbol=AAPL&adapter=mock&host=127.0.0.1&port=11111',
    '/klines?symbol=AAPL&timeframe=1d&limit=5&adapter=mock&host=127.0.0.1&port=11111',
]

for path in paths:
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        body = resp.read().decode('utf-8')
        print(path, body[:400])
