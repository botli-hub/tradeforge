import json
import urllib.request

base = 'http://127.0.0.1:8000/api/options'

for path in [
    '/expirations?symbol=AAPL',
    '/chain?symbol=AAPL&expiry=2026-03-20&adapter=mock&host=127.0.0.1&port=11111',
]:
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        print(path, resp.read().decode('utf-8')[:500])

payload = {
    'strategy': 'bull_call_spread',
    'underlying_price': 105,
    'legs': [
        {'option_type': 'CALL', 'side': 'LONG', 'strike': 100, 'premium': 5, 'quantity': 1},
        {'option_type': 'CALL', 'side': 'SHORT', 'strike': 110, 'premium': 2, 'quantity': 1},
    ],
}
req = urllib.request.Request(base + '/payoff', data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=10) as resp:
    print('/payoff', resp.read().decode('utf-8')[:500])
