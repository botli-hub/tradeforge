import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from app.core.config import get_settings

settings = get_settings()
key = settings["finnhub_api_key"]
if not key:
    raise RuntimeError("未配置 FINNHUB_API_KEY，请先在 backend/.env 或系统环境变量中设置")

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
print('request url: https://finnhub.io/api/v1/stock/candle?[token masked]')
try:
    with urllib.request.urlopen(url, timeout=20) as resp:
        body = resp.read().decode('utf-8')
        print('status ok')
        print(body[:1000])
except Exception as e:
    print(type(e).__name__, e)
    if hasattr(e, 'read'):
        print(e.read().decode('utf-8'))
