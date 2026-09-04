"""Sim Wheel 验收 — PRD §12 六条。不连富途/TG,不碰实盘 wheel_cycles。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestSimWheelAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "sim_test.db"
        # 指向临时库,避免污染开发库
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
                "hard_profit_pct": 42,
                "soft_profit_pct": 28,
                "cc_force_days": 5,
                "put_breach_floor": "hold_to_assign",
                "call_without_shares": "skip",
                "dte_default": 30,
                "levels": {"L1": 0.02, "L2": 0.04, "L3": 0.06},
                "max_symbol_pct": 0.25,
            }
        }
        from app.data import sim_repository as repo
        from app.core.sim_wheel import SimWheelEngine
        self.repo = repo
        self.eng = SimWheelEngine(repo, self.cfg)

    def _put_alert(self, symbol="AAPL", **kw) -> Dict[str, Any]:
        base = {
            "symbol": symbol,
            "signal_level": "WHEEL_PUT",
            "category": "timing_put",
            "side": "PUT",
            "ema_type": "EMA50",
            "strike": 100.0,
            "underlying_price": 105.0,
            "bid": 2.0,
            "premium": 2.0,
            "floor_price": 110.0,
            "contract_code": f"US.{symbol}260320P00100000",
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "dte": 30,
            "fingerprint": kw.pop("fingerprint", f"fp-put-{symbol}-{kw.get('tag', '1')}"),
        }
        base.update(kw)
        return base

    def _call_alert(self, symbol="AAPL", **kw) -> Dict[str, Any]:
        base = {
            "symbol": symbol,
            "signal_level": "WHEEL_CALL",
            "category": "timing_call",
            "side": "CALL",
            "ema_type": "EMA50",
            "strike": 110.0,
            "underlying_price": 108.0,
            "bid": 1.5,
            "premium": 1.5,
            "contract_code": f"US.{symbol}260320C00110000",
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "dte": 30,
            "fingerprint": kw.pop("fingerprint", f"fp-call-{symbol}-{kw.get('tag', '1')}"),
        }
        base.update(kw)
        return base

    # 1. Put 触线 → CSP_OPEN; mock 涨到止盈 → IDLE; stats +1 闭环
    def test_01_put_touch_take_profit_closes_stats(self):
        r = self.eng.on_alert(self._put_alert())
        self.assertTrue(r["ok"], r)
        cyc = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc["status"], "CSP_OPEN")
        # 权利金从 2.0 跌到 1.0 → 止盈 50% ≥ 42%
        out = self.eng.tick(
            {"AAPL": 110.0},
            marks={cyc["id"]: 1.0},
            now=datetime.now(),
        )
        actions = [a.get("action") for a in out["actions"]]
        self.assertIn("close_put", actions)
        cyc2 = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc2["status"], "CLOSED")
        stats = self.repo.list_stats(strategy="put_touch", symbol="AAPL")
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["closed_cycles"], 1)
        self.assertGreater(stats[0]["total_pnl"], 0)
        self.assertEqual(stats[0]["familiarity"], "Cold")
        self.assertEqual(stats[0]["label"], "纸面/非实盘")

    # 2. Put 触线 → mock 跌至 ITM 到期 → HOLDING,成本基础正确
    def test_02_put_assign_holding_cost_basis(self):
        exp = date.today() + timedelta(days=5)
        r = self.eng.on_alert(self._put_alert(
            strike=100.0, underlying_price=102.0, bid=2.0,
            expiry=exp.isoformat(), dte=5, fingerprint="fp-assign-1",
        ))
        self.assertTrue(r["ok"], r)
        cyc = self.repo.get_cycle(r["cycle_id"])
        # 到期日 spot < strike → ASSIGN
        out = self.eng.tick(
            {"AAPL": 95.0},
            as_of=exp,
            now=datetime.combine(exp, datetime.min.time()),
        )
        actions = [a.get("action") for a in out["actions"]]
        self.assertIn("assign", actions)
        cyc2 = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc2["status"], "HOLDING")
        self.assertEqual(cyc2["share_cost"], 100.0)
        # cost_basis = strike − premium_per_share; 1张×100股,prem=2*100=200 → 2.0/股
        self.assertAlmostEqual(cyc2["cost_basis"], 100.0 - 2.0, places=4)
        self.assertEqual(cyc2["shares"], 100.0)

    # 3. HOLDING + Call 触线 → CC_OPEN; mock 到期 ITM → IDLE,股票交割 PnL 正确
    def test_03_holding_call_then_called_away_pnl(self):
        exp_put = date.today()
        r = self.eng.on_alert(self._put_alert(
            strike=100.0, bid=2.0, expiry=exp_put.isoformat(), dte=0,
            fingerprint="fp-full-1",
        ))
        self.eng.tick({"AAPL": 90.0}, as_of=exp_put)
        cyc = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc["status"], "HOLDING")
        cost_basis = cyc["cost_basis"]

        exp_cc = date.today() + timedelta(days=10)
        r2 = self.eng.on_alert(self._call_alert(
            strike=110.0, underlying_price=108.0, bid=1.5,
            expiry=exp_cc.isoformat(), fingerprint="fp-full-cc",
        ))
        self.assertTrue(r2["ok"], r2)
        cyc2 = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc2["status"], "CC_OPEN")

        # 到期 spot > strike → CALLED_AWAY
        out = self.eng.tick({"AAPL": 120.0}, as_of=exp_cc)
        actions = [a.get("action") for a in out["actions"]]
        self.assertIn("called_away", actions)
        cyc3 = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc3["status"], "CLOSED")
        # PnL = (strike − cost_basis)*shares + total_premium
        # put prem 200 + call prem 150 = 350; stock (110-98)*100 = 1200; total 1550
        expected = (110.0 - cost_basis) * 100.0 + cyc3["total_premium"]
        # total_premium already includes both legs and wasn't reduced
        self.assertAlmostEqual(cyc3["realized_pnl"], expected, places=2)

    # 4. HOLDING 无触线超过 cc_force_days 且 spot≥成本 → 自动 force_cc
    def test_04_force_cc_after_holding_days(self):
        exp = date.today()
        r = self.eng.on_alert(self._put_alert(
            strike=100.0, bid=2.0, expiry=exp.isoformat(), fingerprint="fp-force-1",
        ))
        self.eng.tick({"AAPL": 90.0}, as_of=exp)
        cyc = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc["status"], "HOLDING")
        # holding_since = exp; 推进 7 个日历日(≥5 交易日),spot ≥ cost_basis
        as_of = exp + timedelta(days=10)
        out = self.eng.tick({"AAPL": 105.0}, as_of=as_of)
        actions = [a.get("action") for a in out["actions"]]
        self.assertTrue(any(a == "force_cc" or (isinstance(a, str) and "force" in a) for a in actions)
                        or any(x.get("action") == "force_cc" for x in out["actions"]))
        cyc2 = self.repo.get_cycle(r["cycle_id"])
        self.assertEqual(cyc2["status"], "CC_OPEN")
        ev = self.repo.list_events(symbol="AAPL")
        self.assertTrue(any(e["event_type"] == "force_cc" for e in ev))

    # 5. 无持股时 Call 触线 → 不开 CC,记 skip
    def test_05_call_without_shares_skips(self):
        r = self.eng.on_alert(self._call_alert(fingerprint="fp-skip-1"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "skipped_no_shares")
        ev = self.repo.list_events(symbol="AAPL")
        self.assertTrue(any(e["event_type"] == "skipped_no_shares" for e in ev))
        self.assertEqual(self.repo.count_open(), 0)

    # 6. 实盘台账为零时 sim 可独立有持仓;互不改写
    def test_06_sim_independent_of_real_wheel(self):
        import app.data.database as database
        from app.data.database import get_db
        # 确保实盘 wheel 表存在且为空
        conn = get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS wheel_cycles (
                    id TEXT PRIMARY KEY, symbol TEXT, status TEXT,
                    started_at TEXT, updated_at TEXT
                )"""
            )
            conn.execute("DELETE FROM wheel_cycles")
            conn.commit()
            real_n = conn.execute("SELECT COUNT(1) AS c FROM wheel_cycles").fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(real_n, 0)

        r = self.eng.on_alert(self._put_alert(fingerprint="fp-indep-1"))
        self.assertTrue(r["ok"])
        self.assertEqual(self.repo.count_open(), 1)

        conn = get_db()
        try:
            real_n2 = conn.execute("SELECT COUNT(1) AS c FROM wheel_cycles").fetchone()["c"]
            sim_n = conn.execute("SELECT COUNT(1) AS c FROM sim_cycle").fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(real_n2, 0)  # 实盘仍为空
        self.assertEqual(sim_n, 1)

    def test_level_mapping_and_cap(self):
        from app.core.sim_wheel import map_level, size_contracts
        self.assertEqual(map_level(signal_kind="WHEEL_PUT", ema_type="EMA50"), "L1")
        self.assertEqual(map_level(signal_kind="WHEEL_PUT", ema_type="EMA200"), "L2")
        self.assertEqual(map_level(signal_kind="B1", timeframe="5m", chan_kind="B1"), "L1")
        self.assertEqual(map_level(signal_kind="B2", timeframe="5m", chan_kind="B2"), "L2")
        self.assertEqual(map_level(signal_kind="B1", timeframe="30m", chan_kind="B1"), "L2")
        self.assertEqual(map_level(signal_kind="B3", timeframe="30m", chan_kind="B3"), "L3")
        # equity 1M, spot 100 → L3 6% = 60k → 6 张; 单票硬顶 25% = 250 张
        q = size_contracts("L3", 100.0, 1_000_000, max_symbol_pct=0.25)
        self.assertEqual(q, 6)
        # 已占用 20% 时剩余硬顶 5% = 50k, L3 预算 60k → min → 5 张
        q2 = size_contracts("L3", 100.0, 1_000_000, used_symbol=200_000, max_symbol_pct=0.25)
        self.assertEqual(q2, 5)


if __name__ == "__main__":
    unittest.main()
