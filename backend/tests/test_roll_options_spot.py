"""Regression: roll_options PUT 愿接价不能因 spot 未赋值而静默回退缓存 floor。"""
import pytest

from app.data import database as db
from app.data import wheel_repository as repo


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "wheel.db")
    db.init_db()
    from app.core import dividends, earnings, opend
    monkeypatch.setattr(opend, "is_opend_alive", lambda *a, **k: False)
    monkeypatch.setattr(earnings, "get_next_earnings", lambda symbol: None)
    monkeypatch.setattr(dividends, "get_next_dividend", lambda symbol: None)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.wheel import router
    app = FastAPI()
    app.include_router(router, prefix="/api/wheel")
    return TestClient(app)


def _csp():
    c = repo.record_trade(
        "XYZ", "SELL_PUT", contract_code="P1", strike=90, expiry="2027-01-15",
        price=2, qty=1, traded_at="2026-09-01T10:00:00",
    )
    return c["id"]


def test_put_roll_uses_resolve_willing_price(client, monkeypatch):
    from app.core import wheel_floor
    calls = []

    def fake(symbol, spot, iv_rank, current_floor):
        calls.append((symbol, spot))
        return 88.0

    monkeypatch.setattr(wheel_floor, "resolve_willing_price", fake)
    r = client.get("/api/wheel/roll-options", params={"cycle_id": _csp()})
    assert r.status_code == 200, r.text
    assert ("XYZ", None) in calls
    # 之前 UnboundLocalError 被吞 → 永远回退缓存 floor(无 target 时为 None)
    assert r.json()["put_strike_cap"] == 88.0
    assert not any("愿接价计算失败" in w for w in r.json().get("warnings", []))


def test_put_cap_failure_is_logged_not_silent(client, monkeypatch, caplog):
    from app.core import wheel_floor

    def boom(*a, **k):
        raise RuntimeError("kline down")

    monkeypatch.setattr(wheel_floor, "resolve_willing_price", boom)
    with caplog.at_level("WARNING"):
        r = client.get("/api/wheel/roll-options", params={"cycle_id": _csp()})
    assert r.status_code == 200, r.text
    assert any("愿接价计算失败" in w for w in r.json().get("warnings", []))
    assert "愿接价计算失败" in caplog.text
    assert r.json()["put_strike_cap"] is None  # 无 target → 缓存 floor 也为空
