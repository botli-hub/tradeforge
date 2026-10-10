"""PR feat/wheel-review-fixes: 张数 / 冻结愿接价 / 真实ATR+√DTE / POP / 扣费年化 / 事件 fail-safe。"""
import json
import math

import pytest

from app.core import wheel_score as ws
from app.core.wheel_sizing import suggest_qty


# ── 2. sizing ───────────────────────────────────────────────────────────────
PUT = {"side": "PUT", "strike": 10.0, "contract_size": 100}


def test_sizing_default_is_one_contract():
    out = suggest_qty(PUT, cfg={}, headroom=50_000, size_mult=1.1)
    assert out["suggest_qty"] == 1
    assert out["uncapped"] == 55  # 50k/1000 × 1.1,可见但被 max_contracts=1 压住
    assert "max_contracts" in out["capped_by"]


def test_sizing_headroom_and_iv_mult():
    cfg = {"wheel_sizing": {"max_contracts": 20}}
    assert suggest_qty(PUT, cfg=cfg, headroom=10_000)["suggest_qty"] == 10
    assert suggest_qty(PUT, cfg=cfg, headroom=10_000, size_mult=0.65)["suggest_qty"] == 6
    off = {"wheel_sizing": {"max_contracts": 20, "apply_iv_size_mult": False}}
    assert suggest_qty(PUT, cfg=off, headroom=10_000, size_mult=0.65)["suggest_qty"] == 10
    # 余量不足 → 仍建议 1 张(资金旗标另行拦)
    assert suggest_qty(PUT, cfg=cfg, headroom=100)["suggest_qty"] == 1


def test_sizing_call_uses_uncovered_shares():
    cfg = {"wheel_sizing": {"max_contracts": 5}}
    call = {"side": "CALL", "strike": 20, "contract_size": 100}
    assert suggest_qty(call, cfg=cfg, uncovered_shares=250)["suggest_qty"] == 2


def test_sizing_risk_budget_only_when_enabled():
    cfg = {"wheel_sizing": {"max_contracts": 10}}
    on = {"enabled": True, "suggested_max_qty": 3}
    assert suggest_qty(PUT, cfg=cfg, headroom=100_000, risk_budget=on)["suggest_qty"] == 3
    off = {"enabled": False, "suggested_max_qty": 3}
    assert suggest_qty(PUT, cfg=cfg, headroom=100_000, risk_budget=off)["suggest_qty"] == 10
    zero = {"enabled": True, "suggested_max_qty": 0}
    assert suggest_qty(PUT, cfg=cfg, headroom=100_000, risk_budget=zero)["suggest_qty"] == 0


def test_config_defaults_documented():
    from app.core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["wheel_sizing"]["max_contracts"] == 1
    assert DEFAULT_CONFIG["wheel_floor"]["mode"] == "frozen_at_open"
    assert DEFAULT_CONFIG["wheel_scan"]["earnings_unknown_policy"] == "warn"
    assert DEFAULT_CONFIG["wheel_portfolio"]["fee_per_contract"] == 0.65


# ── 3. floor freeze ─────────────────────────────────────────────────────────
@pytest.fixture
def books(tmp_path, monkeypatch):
    from app.data import database as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "wheel.db")
    db.init_db()
    return tmp_path


def test_sell_put_freezes_entry_floor_and_position_uses_it(books, monkeypatch):
    from app.core import wheel_floor
    from app.data import wheel_repository as repo
    monkeypatch.setattr(wheel_floor, "resolve_willing_price", lambda *a, **k: 90.0)
    c = repo.record_trade("XYZ", "SELL_PUT", contract_code="P", strike=88, expiry="2027-01-15",
                          price=2, qty=1, traded_at="2026-09-01T10:00:00")
    cyc = repo.get_cycle(c["id"])
    meta = json.loads(cyc["entry_meta"])
    assert meta["entry_floor"] == 90.0 and meta["floor_mode_at_open"] == "frozen_at_open"

    # 价格下跌后推荐价下移到 80:冻结模式仍用 90,live 模式跟随
    monkeypatch.setattr(wheel_floor, "resolve_willing_price", lambda *a, **k: 80.0)
    frozen = wheel_floor.position_floor("XYZ", cyc, cfg={"wheel_floor": {"mode": "frozen_at_open"}})
    assert frozen == {"floor": 90.0, "source": "entry"}
    live = wheel_floor.position_floor("XYZ", cyc, cfg={"wheel_floor": {"mode": "live"}})
    assert live == {"floor": 80.0, "source": "live"}
    # 旧周期无 entry_meta → 回退实时
    assert wheel_floor.position_floor("XYZ", {"entry_meta": None}, cfg={})["source"] == "live"


def test_explicit_entry_floor_from_draft_wins(books, monkeypatch):
    from app.core import wheel_floor
    from app.data import wheel_repository as repo
    monkeypatch.setattr(wheel_floor, "resolve_willing_price", lambda *a, **k: 70.0)
    res = repo.record_trades([dict(symbol="XYZ", trade_type="SELL_PUT", contract_code="P",
                                   strike=60, expiry="2027-01-15", price=1, qty=1,
                                   entry_floor=65.0)])
    assert json.loads(repo.get_cycle(res["cycle"]["id"])["entry_meta"])["entry_floor"] == 65.0


# ── 4. ATR / buffer / POP / fees ───────────────────────────────────────────
def test_true_atr_uses_high_low():
    bars = [{"high": 11, "low": 9, "close": 10}] * 20
    assert ws.compute_true_atr(bars, 14) == pytest.approx(2.0)
    # 收盘不变时旧 |Δclose| ATR=0,真实 ATR 仍反映日内振幅
    assert ws.compute_atr([10.0] * 21, 20) == 0
    assert ws.compute_true_atr(bars[:10], 14) is None


def test_buffer_scales_with_dte():
    one = ws.buffer_atr_multiple("PUT", 100, 90, 2.0)
    assert one == 5.0
    assert ws.buffer_atr_multiple("PUT", 100, 90, 2.0, dte=25) == 1.0  # 10/(2×5)
    assert ws.buffer_atr_multiple("PUT", 100, 90, 2.0, dte=4) == 2.5
    cfg = ws.buffer_scan_cfg({"buffer_atr_min": 0.8})
    assert cfg["buffer_atr_min"] == 0.5
    assert ws.buffer_scan_cfg({"buffer_mode": "legacy", "buffer_atr_min": 0.8})["buffer_atr_min"] == 0.8


def test_pop_bs_vs_delta_fallback():
    # ATM 短期:N(d2) 略高于 0.5 的反面 → 卖 Put POP < 0.5 (漂移项 −σ²/2)
    pop = ws.estimate_pop("PUT", 0.5, spot=100, strike=100, iv=50, dte=30)
    assert 0.45 < pop < 0.5
    otm = ws.estimate_pop("PUT", 0.2, spot=100, strike=90, iv=0.4, dte=30)
    assert otm > 0.8
    call = ws.estimate_pop("CALL", 0.2, spot=100, strike=110, iv=40, dte=30)
    assert call > 0.8
    assert ws.estimate_pop("PUT", 0.2) == pytest.approx(0.8)  # 缺 IV 回退 1−|Δ|


def test_annualized_net_of_fees():
    gross = 1.0 / 100 * 365 / 30 * 100
    net = ws.annualized_net(1.0, 100, 30, fee_per_contract=0.65, contract_size=100)
    assert net == round((100 - 0.65) / 10_000 * 365 / 30 * 100, 2)
    assert net < round(gross, 2)
    assert ws.annualized_net(1.0, 100, 30) == round(gross, 2)


# ── 5. events fail-safe ─────────────────────────────────────────────────────
def test_earnings_without_key_is_unknown_not_none(monkeypatch):
    from app.core import config, earnings
    earnings._CACHE.clear()
    monkeypatch.setattr(config, "get_effective_config", lambda: {"finnhub_api_key": ""})
    st = earnings.get_earnings_status("ZZZ")
    assert st["status"] == "unknown" and st["date"] is None
    assert earnings.get_next_earnings("ZZZ") is None
    assert earnings.get_earnings_status("0700.HK")["status"] == "unsupported"
    earnings._CACHE.clear()


def _cc(**kw):
    base = dict(side="CALL", strike=100.0, spot=105.0, dte=20, current_price=5.1,
                buyback_ask=5.2, bid=5.0, open_price=3.0, itm=True, delta=0.55,
                qty=1, contract_size=100, days_to_ex_div=3, dividend_amount=0.5)
    base.update(kw)
    return base


def test_ex_div_extrinsic_below_dividend_flags_early_assign():
    from app.core.wheel_decision import decide_position
    r = decide_position(_cc(), 15, 50)
    assert r["extrinsic"] == pytest.approx(0.1)
    assert r["ex_div_extrinsic_risk"] is True
    assert r["early_assign_risk"] is True


def test_ex_div_extrinsic_above_dividend_not_flagged_by_rule():
    from app.core.wheel_decision import decide_position
    r = decide_position(_cc(buyback_ask=7.1, bid=6.9, current_price=7.0), 15, 50)
    assert r["extrinsic"] == pytest.approx(2.0)
    assert r["ex_div_extrinsic_risk"] is False
    r2 = decide_position(_cc(dividend_amount=None), 15, 50)
    assert r2["ex_div_extrinsic_risk"] is False


def test_dte_buffer_0p5_is_stricter_than_legacy_0p8_at_30_45_dte():
    """旧: d ≥ 0.8×ATRc(|Δclose| 均值);新: d ≥ 0.5×ATRtrue×√DTE,ATRtrue ≥ ATRc。"""
    atr_c = 1.0
    for k in (1.0, 1.3):  # 真实ATR/收盘差ATR 比值,经验 1.2~1.5,取下界 1.0 最保守
        for dte in (30, 45):
            need_new = 0.5 * k * atr_c * math.sqrt(dte)
            need_old = 0.8 * atr_c
            assert need_new > 3 * need_old
    # 同一 strike:旧口径刚好 0.8 → 新口径远低于 0.5 → 被惩罚
    d = 0.8
    assert ws.buffer_atr_multiple("PUT", 100, 100 - d, 1.0, dte=30) < 0.5


def test_opportunity_earnings_fields_machine_readable():
    from app.core.wheel_opportunities import apply_earnings_fields, EARNINGS_UNKNOWN_FLAG
    it = {"side": "PUT"}
    apply_earnings_fields(it, {"earnings_status": "unknown", "earnings_unknown": True})
    assert it["earnings_status"] == "unknown" and it["earnings_unknown"] is True
    assert EARNINGS_UNKNOWN_FLAG in it["flags"] and "earnings_unknown" in it["warnings"]
    apply_earnings_fields(it, {"earnings_status": "unknown"})  # 幂等不重复
    assert it["flags"].count(EARNINGS_UNKNOWN_FLAG) == 1
    k = {"side": "PUT"}
    apply_earnings_fields(k, {"earnings_status": "known", "earnings_date": "2026-10-22"})
    assert k["earnings_status"] == "known" and k["earnings_date"] == "2026-10-22"
    assert "flags" not in k
    n = {"side": "CALL"}
    apply_earnings_fields(n, {"earnings_status": "none"})
    assert n["earnings_status"] == "none" and n["earnings_unknown"] is False
    t = {"side": "PUT"}  # 纯触线、无扫描报价 → 未查询 = unknown
    apply_earnings_fields(t, {})
    assert t["earnings_status"] == "unknown"


def test_public_earnings_status_mapping():
    from app.api.wheel import _public_earnings_status as f
    assert [f("ok"), f("none"), f("unknown"), f("unsupported"), f(None)] == \
        ["known", "none", "unknown", "unknown", "unknown"]
