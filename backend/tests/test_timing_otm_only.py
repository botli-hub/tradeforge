"""触线扫描严格 OTM: Put strike<spot / Call strike>spot; ATM 排除。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_timing_klines import (  # noqa: E402
    call_strike_min,
    is_otm_call,
    is_otm_put,
)


def test_is_otm_put_keeps_otm_excludes_itm_atm():
    spot = 100.0
    assert is_otm_put(95, spot) is True
    assert is_otm_put(99.99, spot) is True
    assert is_otm_put(100, spot) is False  # ATM
    assert is_otm_put(105, spot) is False  # ITM
    assert is_otm_put(0, spot) is False
    assert is_otm_put(95, None) is False
    assert is_otm_put(None, spot) is False


def test_is_otm_call_keeps_otm_excludes_itm_atm():
    spot = 100.0
    assert is_otm_call(105, spot) is True
    assert is_otm_call(100.01, spot) is True
    assert is_otm_call(100, spot) is False  # ATM
    assert is_otm_call(95, spot) is False  # ITM
    assert is_otm_call(0, spot) is False
    assert is_otm_call(105, None) is False


def test_call_sell_above_floor_stacks_with_otm_vs_spot():
    """Call 口径: OTM 锚=现价(strike>spot); 另叠 strike≥max(cost, sell_above)。"""
    spot = 100.0
    cost_basis = 90.0
    sell_above = 110.0
    floor = call_strike_min(cost_basis, sell_above)
    assert floor == 110.0

    # ITM vs spot — 即使 ≥ floor 也不算(本例 floor>spot,不会出现)
    assert is_otm_call(95, spot) is False

    # ATM
    assert is_otm_call(100, spot) is False

    # OTM vs spot 但低于愿卖价 → 触线链上还会被 strike_min 去掉
    assert is_otm_call(105, spot) is True
    assert 105 < floor

    # 同时满足 OTM 与愿卖价下限
    assert is_otm_call(115, spot) is True
    assert 115 >= floor

    # 无愿卖/成本时仅看 spot
    assert call_strike_min(None, None) is None
    assert is_otm_call(101, spot) is True


def test_put_itm_atm_excluded_matrix():
    rows = [
        (90, 100, True),
        (100, 100, False),
        (110, 100, False),
    ]
    for strike, spot, want in rows:
        assert is_otm_put(strike, spot) is want, (strike, spot)


def test_call_itm_atm_excluded_matrix():
    rows = [
        (110, 100, True),
        (100, 100, False),
        (90, 100, False),
    ]
    for strike, spot, want in rows:
        assert is_otm_call(strike, spot) is want, (strike, spot)


class TestSimTimingOtmGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "sim_otm.db"
        import app.data.database as database
        database.DB_PATH = db_path
        from app.data.sim_repository import ensure_sim_tables
        ensure_sim_tables()

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def setUp(self):
        import app.data.database as database
        conn = database.get_db()
        try:
            for t in ("sim_leg", "sim_event", "sim_stats", "sim_cycle"):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        finally:
            conn.close()
        self.cfg = {
            "sim_wheel": {
                "enabled": True,
                "equity": 1_000_000,
                "levels": {"L1": 0.02, "L2": 0.04, "L3": 0.06},
                "max_symbol_pct": 0.25,
                "dte_default": 30,
                "call_without_shares": "skip",
            }
        }
        from app.data import sim_repository as repo
        from app.core.sim_wheel import SimWheelEngine
        self.repo = repo
        self.eng = SimWheelEngine(repo, self.cfg)

    def _put(self, **kw) -> Dict[str, Any]:
        base = {
            "symbol": "AAA",
            "signal_level": "WHEEL_PUT",
            "category": "timing_put",
            "side": "PUT",
            "ema_type": "EMA50",
            "strike": 95.0,
            "underlying_price": 100.0,
            "bid": 2.0,
            "floor_price": 110.0,
            "contract_code": "US.AAA260320P00095000",
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "dte": 30,
            "fingerprint": kw.pop("fingerprint", f"fp-put-{kw.get('tag', '1')}"),
        }
        base.update(kw)
        return base

    def _call(self, **kw) -> Dict[str, Any]:
        base = {
            "symbol": "AAA",
            "signal_level": "WHEEL_CALL",
            "category": "timing_call",
            "side": "CALL",
            "ema_type": "EMA50",
            "strike": 110.0,
            "underlying_price": 100.0,
            "bid": 1.5,
            "contract_code": "US.AAA260320C00110000",
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "dte": 30,
            "fingerprint": kw.pop("fingerprint", f"fp-call-{kw.get('tag', '1')}"),
        }
        base.update(kw)
        return base

    def test_sim_skips_put_itm_and_atm(self):
        r = self.eng.on_alert(self._put(strike=105, underlying_price=100, fingerprint="itm-put"))
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("reason"), "not_otm")
        r2 = self.eng.on_alert(self._put(strike=100, underlying_price=100, fingerprint="atm-put"))
        self.assertFalse(r2.get("ok"))
        self.assertEqual(r2.get("reason"), "not_otm")

    def test_sim_keeps_put_otm(self):
        r = self.eng.on_alert(self._put(strike=95, underlying_price=100, fingerprint="otm-put"))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("action"), "open_csp")

    def test_sim_skips_call_itm_atm_keeps_otm(self):
        # 先造 HOLDING
        put = self.eng.on_alert(self._put(fingerprint="seed-put"))
        self.assertTrue(put.get("ok"), put)
        # 指派到持股
        from app.core.sim_wheel import SimWheelEngine
        cyc = self.repo.find_symbol_active("AAA", statuses=("CSP_OPEN",))
        self.assertIsNotNone(cyc)
        self.eng._assign_put(cyc, datetime.now())
        holding = self.repo.find_symbol_active("AAA", statuses=("HOLDING",))
        self.assertIsNotNone(holding)

        r_itm = self.eng.on_alert(self._call(strike=90, underlying_price=100, fingerprint="itm-call"))
        self.assertFalse(r_itm.get("ok"))
        self.assertEqual(r_itm.get("reason"), "not_otm")

        r_atm = self.eng.on_alert(self._call(strike=100, underlying_price=100, fingerprint="atm-call"))
        self.assertFalse(r_atm.get("ok"))
        self.assertEqual(r_atm.get("reason"), "not_otm")

        # cost_basis 抬升后仍须 > spot
        r_otm = self.eng.on_alert(self._call(strike=110, underlying_price=100, fingerprint="otm-call"))
        self.assertTrue(r_otm.get("ok"), r_otm)
        self.assertEqual(r_otm.get("action"), "open_cc")


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
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSimTimingOtmGate)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(fails + (0 if result.wasSuccessful() else 1))
