"""缠论纸面买卖正股 — PRD 附录 A 验收。不连富途/TG,不碰实盘。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestChanEquitySimAppendixA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "chan_equity_test.db"
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
            for t in (
                "sim_leg", "sim_event", "sim_stats", "sim_cycle",
                "sim_equity_trade", "sim_equity_position",
            ):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        finally:
            conn.close()
        self.cfg = {
            "sim_wheel": {
                "enabled": True,
                "chan_buy_mode": "equity_long",
                "equity": 1_000_000,
                "chan_equity_sim": {
                    "share_pool": "isolated",
                    "qty_by_timeframe": {"5m": 5, "30m": 30, "1d": 100},
                },
            }
        }
        from app.data import sim_repository as repo
        from app.core.sim_wheel import SimWheelEngine
        self.repo = repo
        self.eng = SimWheelEngine(repo, self.cfg)

    def _chan(self, *, symbol="SPCX", timeframe="5m", kind="B2", price=25.0, **kw) -> Dict[str, Any]:
        base = {
            "symbol": symbol,
            "category": "chan",
            "timeframe": timeframe,
            "kind": kind,
            "label": kind,
            "price": price,
            "underlying_price": price,
            "ts": kw.pop("ts", f"2026-09-17T10:00:00|{kind}|{timeframe}"),
        }
        base.update(kw)
        if "fingerprint" not in base:
            from app.core.sim_wheel import alert_fingerprint
            base["fingerprint"] = alert_fingerprint(base)
        return base

    # 1. SPCX 5m B2 → +5; 再来 5m S1 → −5
    def test_01_spcx_5m_b2_then_s1(self):
        r1 = self.eng.on_alert(self._chan(kind="B2", timeframe="5m", price=12.5, ts="t-b2"))
        self.assertTrue(r1["ok"], r1)
        self.assertEqual(r1["action"], "equity_buy")
        self.assertEqual(r1["qty"], 5)
        self.assertEqual(r1["strategy"], "chan5m")
        self.assertEqual(r1["shares"], 5)
        pos = self.repo.get_equity_position("chan5m", "SPCX")
        self.assertEqual(pos["shares"], 5)
        self.assertAlmostEqual(pos["avg_cost"], 12.5)

        r2 = self.eng.on_alert(self._chan(kind="S1", timeframe="5m", price=13.0, ts="t-s1"))
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["action"], "equity_sell")
        self.assertEqual(r2["qty"], 5)
        self.assertEqual(r2["shares"], 0)
        pos2 = self.repo.get_equity_position("chan5m", "SPCX")
        self.assertEqual(pos2["shares"], 0)

    # 2. 30m B1 → +30; 1d B1 → +100
    def test_02_30m_and_1d_buy_qty(self):
        r30 = self.eng.on_alert(self._chan(kind="B1", timeframe="30m", price=20.0, ts="t-30"))
        self.assertTrue(r30["ok"], r30)
        self.assertEqual(r30["qty"], 30)
        self.assertEqual(r30["strategy"], "chan30m")

        r1d = self.eng.on_alert(self._chan(kind="B1", timeframe="1d", price=21.0, ts="t-1d"))
        self.assertTrue(r1d["ok"], r1d)
        self.assertEqual(r1d["qty"], 100)
        self.assertEqual(r1d["strategy"], "chan1d")

        # 分桶: 30m 仓与 1d 仓互不影响
        self.assertEqual(self.repo.get_equity_position("chan30m", "SPCX")["shares"], 30)
        self.assertEqual(self.repo.get_equity_position("chan1d", "SPCX")["shares"], 100)

    # 3. 无持仓时 S → skipped_no_shares
    def test_03_sell_without_shares_skips(self):
        r = self.eng.on_alert(self._chan(kind="S2", timeframe="5m", ts="t-s-empty"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "skipped_no_shares")
        ev = self.repo.list_events(symbol="SPCX")
        self.assertTrue(any(e["event_type"] == "skipped_no_shares" for e in ev))
        # fingerprint 已消费,二次同指纹不重复
        r2 = self.eng.on_alert(self._chan(kind="S2", timeframe="5m", ts="t-s-empty"))
        self.assertEqual(r2["reason"], "dup_fingerprint")

    # 4. TG 缠论消息格式不变(format 路径未改写)
    def test_04_tg_chan_format_unchanged(self):
        from app.services.chan_alerts import format_chan_alert, process_chan_signals

        text = format_chan_alert("SPCX", "5m", {
            "kind": "B2", "label": "二买", "price": 12.5, "note": "离开中枢",
        })
        self.assertIn("SPCX · 5分钟 · 二买 · $12.50", text)
        self.assertNotIn("equity", text.lower())
        self.assertNotIn("张", text)  # 不含交易裁决/张数

        sent_bodies = []

        def send_fn(body, row):
            sent_bodies.append(body)
            return {"ok": True, "sent": True}

        # dry 推送仍走 format 文案;sim 挂钩不应改 body
        item = {
            "symbol": "SPCX", "timeframe": "5m", "kind": "B2", "label": "二买",
            "price": 12.5, "ts": "2026-09-17T11:00:00", "note": "离开中枢",
        }
        out = process_chan_signals(
            [item],
            cfg={**self.cfg, "chan_alerts": {"enabled": True}},
            dry_run=False,
            send_fn=send_fn,
            now=datetime(2026, 9, 17, 12, 0, 0),
            state={},
            force=True,
        )
        self.assertEqual(out["sent_count"], 1)
        self.assertEqual(sent_bodies[0], text)

    def test_05_partial_exit_and_isolation_from_wheel(self):
        # 先买 5;再卖 30m 桶(空) skip;再同桶卖不足股数 → partial
        self.eng.on_alert(self._chan(kind="B1", timeframe="5m", price=10.0, ts="b-5"))
        # 另一策略桶 S 不影响
        r_skip = self.eng.on_alert(self._chan(kind="S1", timeframe="30m", price=10.0, ts="s-30"))
        self.assertEqual(r_skip["reason"], "skipped_no_shares")
        self.assertEqual(self.repo.get_equity_position("chan5m", "SPCX")["shares"], 5)

        # 人为只留 3 股再 S(信号 5) → partial_exit 卖 3
        self.repo.upsert_equity_position({
            "strategy": "chan5m", "symbol": "SPCX",
            "shares": 3, "avg_cost": 10.0, "realized_pnl": 0, "updated_at": "t",
        })
        r = self.eng.on_alert(self._chan(kind="S1", timeframe="5m", price=11.0, ts="s-partial"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["action"], "partial_exit")
        self.assertEqual(r["qty"], 3)
        self.assertTrue(r["partial"])

        # Touch Wheel 持股不受缠论正股影响: 无 sim_cycle HOLDING
        cycles = self.repo.list_cycles(symbol="SPCX", include_closed=True)
        self.assertEqual(cycles, [])

    def test_06_strategy_keys_and_default_mode(self):
        from app.core.sim_wheel import get_sim_cfg, strategy_of_alert, equity_qty_from_timeframe

        cfg = get_sim_cfg({})
        self.assertEqual(cfg["chan_buy_mode"], "equity_long")
        self.assertEqual(cfg["chan_equity_sim"]["share_pool"], "isolated")
        self.assertEqual(equity_qty_from_timeframe("5m"), 5)
        self.assertEqual(equity_qty_from_timeframe("30m"), 30)
        self.assertEqual(equity_qty_from_timeframe("1d"), 100)
        self.assertEqual(strategy_of_alert({"category": "chan", "timeframe": "1d", "kind": "B1"}), "chan1d")
        self.assertEqual(strategy_of_alert({"category": "chan", "timeframe": "30m", "kind": "S1"}), "chan30m")
        self.assertEqual(strategy_of_alert({"category": "chan", "timeframe": "5m", "kind": "B2"}), "chan5m")

    def test_07_sell_put_mode_still_maps_b_to_put_path(self):
        """显式 chan_buy_mode=sell_put 时仍走旧 CSP 路径(不进 equity)。"""
        cfg = {
            "sim_wheel": {
                "enabled": True,
                "chan_buy_mode": "sell_put",
                "equity": 1_000_000,
                "dte_default": 30,
                "levels": {"L1": 0.02, "L2": 0.04, "L3": 0.06},
            }
        }
        from app.core.sim_wheel import SimWheelEngine
        eng = SimWheelEngine(self.repo, cfg)
        # 无 strike 的缠论 B → CSP 路径会因 no_strike 失败,但不应写入 equity
        r = eng.on_alert(self._chan(kind="B1", timeframe="5m", ts="legacy-b"))
        self.assertNotEqual(r.get("action"), "equity_buy")
        self.assertIsNone(self.repo.get_equity_position("chan5m", "SPCX"))


if __name__ == "__main__":
    unittest.main()
