import urllib.request
import urllib.error

urls = [
    'http://127.0.0.1:8000/api/history/subscriptions',
    'http://127.0.0.1:8000/api/history/scheduler/status',
]
for url in urls:
    with urllib.request.urlopen(url, timeout=20) as resp:
        print(url)
        print(resp.read().decode('utf-8')[:2000])

req = urllib.request.Request('http://127.0.0.1:8000/api/history/scheduler/run?host=127.0.0.1&port=11111', method='POST')
with urllib.request.urlopen(req, timeout=300) as resp:
    print('RUN')
    print(resp.read().decode('utf-8')[:4000])

with urllib.request.urlopen('http://127.0.0.1:8000/api/history/scheduler/status', timeout=20) as resp:
    print('STATUS_AFTER')
    print(resp.read().decode('utf-8')[:4000])
