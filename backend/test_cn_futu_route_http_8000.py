import urllib.request
import urllib.error

paths = [
    'http://127.0.0.1:8000/api/market/search?q=600519&adapter=finnhub',
    'http://127.0.0.1:8000/api/market/quote?symbol=600519.SH&adapter=finnhub&host=127.0.0.1&port=11111',
    'http://127.0.0.1:8000/api/market/klines?symbol=600519.SH&timeframe=1d&limit=5&adapter=finnhub&host=127.0.0.1&port=11111',
]

for path in paths:
    try:
        with urllib.request.urlopen(path, timeout=15) as resp:
            print(path)
            print(resp.read().decode('utf-8')[:1200])
    except urllib.error.HTTPError as e:
        print(path)
        print('HTTPError', e.code, e.read().decode('utf-8'))
    except Exception as e:
        print(path)
        print(type(e).__name__, str(e))
