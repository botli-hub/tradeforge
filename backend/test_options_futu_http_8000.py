import urllib.request
import urllib.error

paths = [
    'http://127.0.0.1:8000/api/options/expirations?symbol=QQQ&host=127.0.0.1&port=11111',
    'http://127.0.0.1:8000/api/options/chain?symbol=QQQ&host=127.0.0.1&port=11111',
]

for path in paths:
    try:
        with urllib.request.urlopen(path, timeout=30) as resp:
            print(path)
            print(resp.read().decode('utf-8')[:2000])
    except urllib.error.HTTPError as e:
        print(path)
        print('HTTPError', e.code, e.read().decode('utf-8'))
