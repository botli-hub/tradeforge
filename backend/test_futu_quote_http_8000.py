import urllib.request
import urllib.error

urls = [
    'http://127.0.0.1:8000/api/market/quote?symbol=AAPL&adapter=futu&host=127.0.0.1&port=11111',
    'http://127.0.0.1:8000/api/market/quote?symbol=00700&adapter=futu&host=127.0.0.1&port=11111',
]

for url in urls:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(url)
            print(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(url)
        print('HTTPError', e.code, e.read().decode('utf-8'))
    except Exception as e:
        print(url)
        print(type(e).__name__, str(e))
