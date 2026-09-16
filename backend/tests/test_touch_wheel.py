"""Touch Wheel 验收 — PRD §11。不连富途/TG,不碰实盘 wheel_cycles / FirstTrade。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestTouchWheelPRD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "touch_wheel_test.db"
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
            "touch_wheel": {
                "qty_by_timeframe": {"1h": 1, "1d": 2},
                "same_batch_1h_1d": "prefer_daily",
                "put_tp_mode": "call_touch",
                "premium_tp_override": False,
                "threat_exit": False,
                "put_touch_closes_call": True,
                "cc_force_days": 0,
                "put_breach_floor": "hold_to_assign",
                "call_without_shares": "skip",
                "allow_parallel_csp": True,
            },
            "sim_wheel": {
                "enabled": True,
                "equity": 1_000_000,
                "cc_force_days": 0,
                "dte_default": 30,
                "max_portfolio_pct": 0.80,
            },
        }
        from app.data import sim_repository as repo
        from app.core.sim_wheel import SimWheelEngine

        self.repo = repo
        self.eng = SimWheelEngine(repo, self.cfg)

    def _put(self, symbol="QQQ", **kw) -> Dict[str, Any]:
        tf = kw.pop("timeframe", "1h")
        ema = kw.pop("ema_type", "EMA50")
        base = {
            "symbol": symbol,
            "signal_level": "WHEEL_PUT",
            "category": "timing_put",
            "side": "PUT",
            "ema_type": ema,
            "timeframe": tf,
            "strike": 100.0,
            "underlying_price": 105.0,
            "bid": 2.0,
            "premium": 2.0,
            "floor_price": 110.0,
            "contract_code": f"US.{symbol}260320P00100000",
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "dte": 30,
            "fingerprint": kw.pop(
                "fingerprint", f"fp-put-{symbol}-{tf}-{ema}-{kw.get('tag', '1')}"
            ),
        }
        base.update(kw)
        return base

    def _call(self, symbol="QQQ", **kw) -> Dict[str, Any]:
        tf = kw.pop("timeframe", "1h")
        ema = kw.pop("ema_type", "EMA50")
        base = {
            "symbol": symbol,
            "signal_level": "WHEEL_CALL",
            "category": "timing_call",
            "side": "CALL",
            "ema_type": ema,
            "timeframe": tf,
            "strike": 110.0,
            "underlying_price": 108.0,
            "bid": 1.5,
            "premium": 1.5,
            "contract_code": f"US.{symbol}260320C00110000",
            "expiry": (date.today() + timedelta(days=30)).isoformat(),
            "dte": 30,
            "fingerprint": kw.pop(
                "fingerprint", f"fp-call-{symbol}-{tf}-{ema}-{kw.get('tag', '1')}"
            ),
        }
        base.update(kw)
        return base

    # §11.1 Put 1h EMA50 → 1 张; 1d EMA200 → 2 张
    def test_01_qty_by_timeframe_not_ema(self):
        r1 = self.eng.on_alert(self._put(timeframe="1h", ema_type="EMA50", fingerprint="t1-1h"))
        self.assertTrue(r1["ok"], r1)
        self.assertEqual(r1["qty"], 1)
        c1 = self.repo.get_cycle(r1["cycle_id"])
        self.assertEqual(c1["open_qty"], 1)
        self.assertEqual(c1["strategy"], "put_1h_ema50")

        r2 = self.eng.on_alert(
            self._put(
                timeframe="1d",
                ema_type="EMA200",
                fingerprint="t1-1d",
                contract_code="US.QQQ260320P00095000",
                strike=95.0,
            )
        )
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["qty"], 2)
        c2 = self.repo.get_cycle(r2["cycle_id"])
        self.assertEqual(c2["open_qty"], 2)
        self.assertEqual(c2["strategy"], "put_1d_ema200")
        # EMA200 不加倍:同为 1h 时仍 1 张
        r3 = self.eng.on_alert(
            self._put(
                symbol="SPCX",
                timeframe="1h",
                ema_type="EMA200",
                fingerprint="t1-1h200",
            )
        )
        self.assertTrue(r3["ok"], r3)
        self.assertEqual(r3["qty"], 1)

    # §11.2 同批 1h+1d → 只开 2 张(prefer_daily)
    def test_02_same_batch_prefer_daily(self):
        from app.core.sim_wheel import select_touch_batch_for_push, resolve_same_batch_1h_1d

        a1h = self._put(timeframe="1h", ema_type="EMA50", annualized=30, fingerprint="b-1h")
        a1d = self._put(
            timeframe="1d",
            ema_type="EMA50",
            annualized=20,
            strike=98.0,
            contract_code="US.QQQ260320P00098000",
            fingerprint="b-1d",
        )
        keepers, shadows = select_touch_batch_for_push(
            [a1h, a1d], same_batch_1h_1d="prefer_daily"
        )
        self.assertEqual(len(keepers), 1)
        self.assertEqual(keepers[0]["timeframe"], "1d")
        self.assertEqual(len(shadows), 1)
        self.assertEqual(shadows[0]["reason"], "shadow_superseded_by_1d")

        r = self.eng.on_alert(keepers[0])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["qty"], 2)
        self.assertEqual(self.repo.count_open(), 1)

    # §11.3 CSP 中 Call 1h → 买回 1;无 Call 即使浮盈大也不平
    def test_03_call_touch_closes_put_no_premium_tp(self):
        r = self.eng.on_alert(self._put(timeframe="1h", fingerprint="t3-put"))
        self.assertTrue(r["ok"], r)
        cid = r["cycle_id"]
        # 浮盈很大也不平
        out = self.eng.tick({"QQQ": 120.0}, marks={cid: 0.2}, now=datetime.now())
        actions = [a.get("action") for a in out["actions"]]
        self.assertNotIn("close_put", actions)
        self.assertEqual(self.repo.get_cycle(cid)["status"], "CSP_OPEN")

        r2 = self.eng.on_alert(
            self._call(
                timeframe="1h",
                fingerprint="t3-call",
                put_close_mark=0.5,
            )
        )
        self.assertTrue(r2["ok"] or r2.get("prior_actions"), r2)
        prior = r2.get("prior_actions") or []
        self.assertTrue(any(a.get("action") == "close_put" for a in prior), r2)
        closed_qty = sum(a.get("qty") or 0 for a in prior if a.get("action") == "close_put")
        self.assertEqual(closed_qty, 1)
        self.assertEqual(self.repo.get_cycle(cid)["status"], "CLOSED")

    # §11.4 CSP×3 中 Call 1d → 买回 2(按 DTE/strike 优先级)
    def test_04_multi_leg_call_1d_buys_back_2(self):
        # 三笔各 1 张:短 DTE / 高 strike 优先
        legs = [
            dict(
                timeframe="1h",
                strike=100.0,
                dte=10,
                expiry=(date.today() + timedelta(days=10)).isoformat(),
                fingerprint="t4-a",
                contract_code="US.QQQ260110P00100000",
                tag="a",
            ),
            dict(
                timeframe="1h",
                strike=98.0,
                dte=40,
                expiry=(date.today() + timedelta(days=40)).isoformat(),
                fingerprint="t4-b",
                contract_code="US.QQQ260410P00098000",
                tag="b",
            ),
            dict(
                timeframe="1h",
                strike=99.0,
                dte=20,
                expiry=(date.today() + timedelta(days=20)).isoformat(),
                fingerprint="t4-c",
                contract_code="US.QQQ260220P00099000",
                tag="c",
            ),
        ]
        ids = []
        for kw in legs:
            r = self.eng.on_alert(self._put(**kw))
            self.assertTrue(r["ok"], r)
            ids.append(r["cycle_id"])
        total = sum(self.repo.get_cycle(i)["open_qty"] for i in ids)
        self.assertEqual(total, 3)

        r2 = self.eng.on_alert(
            self._call(timeframe="1d", fingerprint="t4-call", put_close_mark=0.4)
        )
        prior = r2.get("prior_actions") or []
        closed = sum(a.get("qty") or 0 for a in prior if a.get("action") == "close_put")
        self.assertEqual(closed, 2)
        # 最短 DTE(10d strike100) 与次短(20d strike99) 应优先
        remaining = [
            (self.repo.get_cycle(i)["status"], self.repo.get_cycle(i)["open_strike"], self.repo.get_cycle(i)["open_qty"])
            for i in ids
        ]
        open_left = [x for x in remaining if x[0] == "CSP_OPEN"]
        self.assertEqual(len(open_left), 1)
        self.assertEqual(open_left[0][1], 98.0)  # 最长 DTE 留下

    # §11.5 CSP+持股, Call → 先平 Put 再挂 CC
    def test_05_csp_plus_holding_call_closes_put_then_cc(self):
        # 接货持股
        exp = date.today()
        r_hold = self.eng.on_alert(
            self._put(
                timeframe="1d",
                strike=100.0,
                bid=2.0,
                expiry=exp.isoformat(),
                dte=0,
                fingerprint="t5-assign",
            )
        )
        self.eng.tick({"QQQ": 90.0}, as_of=exp)
        hold = self.repo.get_cycle(r_hold["cycle_id"])
        self.assertEqual(hold["status"], "HOLDING")
        self.assertEqual(hold["shares"], 200.0)  # 1d=2 张

        # 另开一笔 CSP 1 张
        r_csp = self.eng.on_alert(
            self._put(
                timeframe="1h",
                strike=95.0,
                fingerprint="t5-csp",
                contract_code="US.QQQ260320P00095000",
                expiry=(date.today() + timedelta(days=30)).isoformat(),
                dte=30,
            )
        )
        self.assertTrue(r_csp["ok"], r_csp)
        self.assertEqual(self.repo.get_cycle(r_csp["cycle_id"])["status"], "CSP_OPEN")

        r_call = self.eng.on_alert(
            self._call(
                timeframe="1h",
                strike=110.0,
                underlying_price=108.0,
                fingerprint="t5-call",
                put_close_mark=0.5,
            )
        )
        self.assertTrue(r_call["ok"], r_call)
        prior = r_call.get("prior_actions") or []
        self.assertTrue(any(a.get("action") == "close_put" for a in prior))
        self.assertEqual(self.repo.get_cycle(r_csp["cycle_id"])["status"], "CLOSED")
        self.assertEqual(r_call.get("action"), "open_cc")
        hold2 = self.repo.get_cycle(r_hold["cycle_id"])
        self.assertEqual(hold2["status"], "CC_OPEN")
        # Call 1h → CC qty=1(裸股 200 够)
        self.assertEqual(hold2["open_qty"], 1)

    # §11.6 无持股仅 Call → skip
    def test_06_call_without_shares_skips(self):
        r = self.eng.on_alert(self._call(fingerprint="t6-skip"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "skipped_no_shares")
        self.assertEqual(self.repo.count_open(), 0)

    # §11.7 破愿接不买回 → ASSIGN
    def test_07_breach_floor_hold_to_assign(self):
        exp = date.today() + timedelta(days=5)
        r = self.eng.on_alert(
            self._put(
                timeframe="1h",
                strike=100.0,
                floor_price=110.0,
                underlying_price=105.0,
                expiry=exp.isoformat(),
                dte=5,
                fingerprint="t7-breach",
            )
        )
        self.assertTrue(r["ok"], r)
        cid = r["cycle_id"]
        # spot < floor 且提供 mark 高浮盈 — 仍不平
        out = self.eng.tick(
            {"QQQ": 100.0},  # < floor 110
            marks={cid: 0.1},
            as_of=date.today(),
        )
        actions = [a.get("action") for a in out["actions"]]
        self.assertIn("breach_floor_hold", actions)
        self.assertNotIn("close_put", actions)
        # 到期 ITM → ASSIGN
        out2 = self.eng.tick({"QQQ": 90.0}, as_of=exp)
        self.assertIn("assign", [a.get("action") for a in out2["actions"]])
        self.assertEqual(self.repo.get_cycle(cid)["status"], "HOLDING")

    # §11.8 冷却 1 自然日且无 ×1.4(与 leaps_repository 对齐)
    def test_08_cooldown_one_calendar_day_no_1_4(self):
        from app.data import leaps_repository as leaps_repo
        import app.data.database as database

        # ensure leaps tables
        database.init_db()
        conn = database.get_db()
        try:
            conn.execute("DELETE FROM leaps_cooldowns")
            conn.commit()
        finally:
            conn.close()

        before = datetime.now()
        leaps_repo.set_contract_cooldown("US.QQQ.TEST", "QQQ", 1)
        after = datetime.now()
        rows = [r for r in leaps_repo.get_all_cooldowns() if r["contract_code"] == "US.QQQ.TEST"]
        self.assertEqual(len(rows), 1)
        until = datetime.fromisoformat(rows[0]["cooldown_until"])
        lo = before + timedelta(days=1) - timedelta(seconds=2)
        hi = after + timedelta(days=1) + timedelta(seconds=2)
        self.assertGreaterEqual(until, lo)
        self.assertLessEqual(until, hi)
        # days=5 不是 ×1.4→7
        leaps_repo.set_contract_cooldown("US.QQQ.TEST5", "QQQ", 5)
        until5 = datetime.fromisoformat(
            [r for r in leaps_repo.get_all_cooldowns() if r["contract_code"] == "US.QQQ.TEST5"][0][
                "cooldown_until"
            ]
        )
        self.assertLess(until5, before + timedelta(days=7) - timedelta(hours=1))

    # §11.9 实盘台账与 Sim 互不改写
    def test_09_sim_does_not_rewrite_real_ledger(self):
        from app.data.database import get_db

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
        finally:
            conn.close()

        r = self.eng.on_alert(self._put(fingerprint="t9-indep"))
        self.assertTrue(r["ok"])
        conn = get_db()
        try:
            real_n = conn.execute("SELECT COUNT(1) AS c FROM wheel_cycles").fetchone()["c"]
            sim_n = conn.execute("SELECT COUNT(1) AS c FROM sim_cycle").fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(real_n, 0)
        self.assertEqual(sim_n, 1)

    def test_helpers_qty_and_strategy(self):
        from app.core.sim_wheel import (
            qty_from_timeframe,
            strategy_of_alert,
            normalize_timeframe,
        )

        self.assertEqual(qty_from_timeframe("1h"), 1)
        self.assertEqual(qty_from_timeframe("1d"), 2)
        self.assertEqual(normalize_timeframe("60m"), "1h")
        self.assertEqual(
            strategy_of_alert(self._put(timeframe="1d", ema_type="EMA200")),
            "put_1d_ema200",
        )
        self.assertEqual(
            strategy_of_alert(self._call(timeframe="1h", ema_type="EMA50")),
            "call_1h_ema50",
        )


if __name__ == "__main__":
    unittest.main()
