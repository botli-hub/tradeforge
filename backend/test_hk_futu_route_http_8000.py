import urllib.request
import urllib.error

paths = [
    'http://127.0.0.1:8000/api/market/search?q=00700&adapter=finnhub',
    'http://127.0.0.1:8000/api/market/quote?symbol=00700.HK&adapter=finnhub&host=127.0.0.1&port=11111',
    'http://127.0.0.1:8000/api/market/klines?symbol=00700.HK&timeframe=1d&limit=3&adapter=finnhub&host=127.0.0.1&port=11111',
]

for path in paths:
    try:
        with urllib.request.urlopen(path, timeout=20) as resp:
            print(path)
            print(resp.read().decode('utf-8')[:1200])
    except urllib.error.HTTPError as e:
        print(path)
        print('HTTPError', e.code, e.read().decode('utf-8'))
