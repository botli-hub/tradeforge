import json
import urllib.request

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
vol_ratio = volume / MA(volume, 20)

entry = cross_above(ma_fast, ma_slow) and vol_ratio > 1.5
exit = cross_below(ma_fast, ma_slow)
'''

headers = {"Content-Type": "application/json"}
base = "http://127.0.0.1:8001/api/formula"

for path in ["/validate", "/parse", "/transpile"]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps({"code": code}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(path, json.dumps(data, ensure_ascii=False))
