import json
import urllib.request

code = '''strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
entry = cross_above(ma_fast, ma_slow)
exit = cross_below(ma_fast, ma_slow)
'''

headers = {"Content-Type": "application/json"}
base = "http://127.0.0.1:8000/api/formula"


def post(path: str):
    req = urllib.request.Request(
        base + path,
        data=json.dumps({"code": code}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

print(json.dumps({
    "validate": post("/validate"),
    "parse": post("/parse"),
    "transpile": post("/transpile"),
}, ensure_ascii=False, indent=2))
