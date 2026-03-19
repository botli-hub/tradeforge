import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

# load backend/.env
for line in (Path(__file__).resolve().parent / '.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

key = os.environ['FINNHUB_API_KEY']
start = int((datetime.now() - timedelta(days=30)).timestamp())
end = int(datetime.now().timestamp())
params = urllib.parse.urlencode({
    'symbol': 'AAPL',
    'resolution': 'D',
    'from': start,
    'to': end,
    'token': key,
})
url = f'https://finnhub.io/api/v1/stock/candle?{params}'
print(url)
try:
    with urllib.request.urlopen(url, timeout=20) as resp:
        body = resp.read().decode('utf-8')
        print('status ok')
        print(body[:1000])
except Exception as e:
    print(type(e).__name__, e)
    if hasattr(e, 'read'):
        print(e.read().decode('utf-8'))
