import urllib.request
import urllib.error

for path in [
    'http://127.0.0.1:8000/api/market/status?adapter=finnhub&host=127.0.0.1&port=11111',
    'http://127.0.0.1:8000/api/market/search?q=AAPL&adapter=finnhub',
    'http://127.0.0.1:8000/api/market/quote?symbol=AAPL&adapter=finnhub&host=127.0.0.1&port=11111',
    'http://127.0.0.1:8000/api/market/klines?symbol=AAPL&timeframe=1d&adapter=finnhub&host=127.0.0.1&port=11111',
]:
    try:
        with urllib.request.urlopen(path, timeout=10) as resp:
            print(path, resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(path, e.code, e.read().decode('utf-8'))
