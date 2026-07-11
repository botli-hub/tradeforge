"""在场合约行动判定 + Roll 配对识别 单元测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.wheel import _position_hints  # noqa: E402


def _item(**kw):
    base = dict(side="PUT", strike=100.0, spot=110.0, dte=35, current_price=1.0,
                open_price=3.0, profit_pct=None, itm=False, delta=0.2, expiring=False)
    base.update(kw)
    return base


def test_profit_hit_first():
    r = _position_hints(_item(profit_pct=60.0), 15, 50)
    assert r["action_hint"] == "止盈平仓"


def test_deep_itm_by_delta():
    r = _position_hints(_item(itm=True, delta=0.62, spot=95, profit_pct=-30.0), 15, 50)
    assert r["deep_itm"] and r["action_hint"] == "尽快 Roll(深度价内)"


def test_deep_itm_by_moneyness():
    # PUT strike 100, spot 95 → 价内 5.3% > 3%,即使 delta 缺失
    r = _position_hints(_item(itm=True, delta=0, spot=95, profit_pct=-30.0), 15, 50)
    assert r["deep_itm"]


def test_shallow_itm_expiring():
    r = _position_hints(_item(itm=True, delta=0.45, spot=99, dte=5, expiring=True, profit_pct=-10.0), 15, 50)
    assert not r["deep_itm"]
    assert r["action_hint"] == "临期 ITM:Roll 或准备接货/交货"


def test_21dte_rule():
    r = _position_hints(_item(dte=18, profit_pct=30.0), 15, 50)
    assert r["roll_21dte"] and r["action_hint"] == "考虑 Roll(≤21DTE)"
    # 已止盈则不触发 21DTE
    r2 = _position_hints(_item(dte=18, profit_pct=55.0), 15, 50)
    assert not r2["roll_21dte"] and r2["action_hint"] == "止盈平仓"


def test_low_yield():
    # 剩余年化 = 0.3/100×365/35×100 ≈ 3.13% < 15%
    r = _position_hints(_item(current_price=0.3, profit_pct=40.0), 15, 50)
    assert r["low_yield"] and r["action_hint"] == "平仓换仓(剩余年化低)"
    assert abs(r["remaining_annualized"] - 3.13) < 0.01
    # ITM 时剩余价值高是风险不是收益,不触发 low_yield
    r2 = _position_hints(_item(current_price=0.3, itm=True, spot=99.5, delta=0.4, profit_pct=-5.0), 15, 50)
    assert not r2["low_yield"]


def test_early_assign_cc():
    r = _position_hints(_item(side="CALL", strike=100, spot=112, itm=True, delta=0.85, profit_pct=-50.0), 15, 50)
    assert r["early_assign_risk"]
    assert any("提前被行权" in x for x in r["reasons"])


def test_healthy_no_hint():
    r = _position_hints(_item(dte=35, current_price=1.6, profit_pct=30.0), 15, 50)
    # 剩余年化 16.7% > 15%,OTM,未临期未止盈 → 无建议
    assert r["action_hint"] is None and not r["reasons"]


def test_roll_pairing():
    """get_trades 的同日 买回+再卖出 配对"""
    import sqlite3, uuid
    import app.data.database as db
    db.DB_PATH = Path('/tmp/wtest3/test.db')
    Path('/tmp/wtest3').mkdir(exist_ok=True)
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()
    db.init_db()
    from app.data import wheel_repository as repo
    c = repo.record_trade(symbol="AAPL", trade_type="SELL_PUT", strike=200, expiry="2026-08-21",
                          qty=1, price=3.0, traded_at="2026-07-01T10:00:00")
    repo.record_trade(symbol="AAPL", trade_type="BUY_PUT_CLOSE", qty=1, price=1.2,
                      cycle_id=c["id"], traded_at="2026-07-20T10:00:00")
    repo.record_trade(symbol="AAPL", trade_type="SELL_PUT", strike=195, expiry="2026-09-18",
                      qty=1, price=3.4, cycle_id=c["id"], traded_at="2026-07-20T10:05:00")
    trades = repo.get_trades(cycle_id=c["id"])
    rolls = [t for t in trades if t.get("is_roll")]
    assert len(rolls) == 2, trades
    assert {t["trade_type"] for t in rolls} == {"BUY_PUT_CLOSE", "SELL_PUT"}
    # 首笔开仓(不同日)不应被标记
    first = next(t for t in trades if t["traded_at"].startswith("2026-07-01"))
    assert not first.get("is_roll")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
