"""席位可执行卖期权信号: pending / ACK / 幂等 / 标的过滤 / suggested_limit=bid。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestExecSignals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "exec_signals.db"
        import app.data.database as database

        database.DB_PATH = db_path
        database.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def setUp(self):
        import app.data.database as database

        conn = database.get_db()
        try:
            conn.execute("DELETE FROM wheel_exec_signal_acks")
            conn.execute("DELETE FROM wheel_exec_signals")
            conn.commit()
        finally:
            conn.close()

    def _emit_tsll_put(self, bid=1.25, strike=15.0, expiry="2026-11-21", **kw):
        from app.data import exec_signal_repository as repo

        return repo.emit_signal(
            symbol="TSLL",
            side="PUT",
            strike=strike,
            expiry=expiry,
            source=kw.pop("source", "touch"),
            bid=bid,
            ask=kw.pop("ask", 1.35),
            contract_code=kw.pop("contract_code", "US.TSLL261121P00015000"),
            quote_asof=kw.pop("quote_asof", "2026-10-09T10:00:00"),
            **kw,
        )

    def test_non_allowed_symbol_not_queued(self):
        from app.data import exec_signal_repository as repo
        from app.core.exec_signals import maybe_emit_from_touch

        self.assertIsNone(
            repo.emit_signal(
                symbol="AAPL",
                side="Put",
                strike=100,
                expiry="2026-11-21",
                bid=2.0,
                source="touch",
            )
        )
        self.assertIsNone(
            maybe_emit_from_touch(
                {
                    "symbol": "QQQ",
                    "signal_level": "WHEEL_PUT",
                    "strike": 400,
                    "expiry": "2026-11-21",
                    "bid": 3.0,
                    "contract_code": "US.QQQ261121P00400000",
                }
            )
        )
        self.assertEqual(repo.list_pending(), [])

    def test_pending_filters_symbols_and_side(self):
        from app.data import exec_signal_repository as repo

        self._emit_tsll_put(bid=1.1, strike=15)
        repo.emit_signal(
            symbol="SPCH",
            side="Call",
            strike=20,
            expiry="2026-12-18",
            source="touch",
            bid=0.55,
            contract_code="US.SPCH261218C00020000",
        )
        # 故意再 emit TSLL Call
        repo.emit_signal(
            symbol="TSLL",
            side="CALL",
            strike=18,
            expiry="2026-12-18",
            source="dual",
            bid=0.8,
            contract_code="US.TSLL261218C00018000",
        )

        all_p = repo.list_pending()
        self.assertEqual(len(all_p), 3)

        only_tsll = repo.list_pending(symbols=["TSLL"])
        self.assertEqual({x["symbol"] for x in only_tsll}, {"TSLL"})
        self.assertEqual(len(only_tsll), 2)

        only_spch = repo.list_pending(symbols=["SPCH", "AAPL"])  # AAPL 忽略
        self.assertEqual([x["symbol"] for x in only_spch], ["SPCH"])

        puts = repo.list_pending(side="Put")
        self.assertTrue(all(x["side"] == "Put" for x in puts))
        self.assertEqual(len(puts), 1)

        calls = repo.list_pending(side="CALL")
        self.assertTrue(all(x["side"] == "Call" for x in calls))
        self.assertEqual(len(calls), 2)

    def test_suggested_limit_equals_bid(self):
        from app.data import exec_signal_repository as repo

        row = self._emit_tsll_put(bid=2.34)
        self.assertEqual(row["suggested_limit"], 2.34)
        self.assertEqual(row["bid"], 2.34)
        self.assertEqual(row["qty"], 1)
        self.assertEqual(row["side"], "Put")
        self.assertEqual(row["symbol"], "TSLL")
        self.assertIn("ask", row)
        pending = repo.list_pending(symbols=["TSLL"])
        self.assertEqual(pending[0]["suggested_limit"], pending[0]["bid"])

    def test_ack_consumed_leaves_pending(self):
        from app.data import exec_signal_repository as repo

        row = self._emit_tsll_put()
        sid = row["signal_id"]
        out = repo.ack_signal(sid, consumer="seat", status="consumed")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "consumed")
        self.assertEqual(repo.list_pending(), [])

    def test_ack_ignored_leaves_pending(self):
        from app.data import exec_signal_repository as repo

        row = self._emit_tsll_put()
        sid = row["signal_id"]
        out = repo.ack_signal(sid, consumer="seat", status="ignored", note="dup")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ignored")
        self.assertEqual(repo.list_pending(), [])

    def test_ack_idempotent(self):
        from app.data import exec_signal_repository as repo

        row = self._emit_tsll_put()
        sid = row["signal_id"]
        a1 = repo.ack_signal(sid, consumer="seat", status="consumed")
        self.assertFalse(a1.get("idempotent"))
        a2 = repo.ack_signal(sid, consumer="seat", status="consumed")
        self.assertTrue(a2["ok"])
        self.assertTrue(a2["idempotent"])
        # 已 consumed 再 ack ignored 也幂等成功(不报错)
        a3 = repo.ack_signal(sid, consumer="seat", status="ignored")
        self.assertTrue(a3["ok"])
        self.assertTrue(a3["idempotent"])
        self.assertEqual(repo.list_pending(), [])

    def test_emit_idempotent_same_day_bucket(self):
        from app.data import exec_signal_repository as repo

        r1 = self._emit_tsll_put(bid=1.0, timeframe="1h", ema_type="EMA50")
        r2 = self._emit_tsll_put(bid=1.5, timeframe="1h", ema_type="EMA50")
        self.assertEqual(r1["signal_id"], r2["signal_id"])
        # 第二次不覆盖 bid(保持首次)
        self.assertEqual(r2["bid"], 1.0)
        self.assertEqual(len(repo.list_pending()), 1)

    def test_maybe_emit_from_touch_and_opportunity(self):
        from app.core.exec_signals import maybe_emit_from_touch, maybe_emit_from_opportunity
        from app.data import exec_signal_repository as repo

        touch = maybe_emit_from_touch(
            {
                "symbol": "SPCH",
                "signal_level": "WHEEL_CALL",
                "strike": 12.5,
                "expiry": "2026-10-16",
                "bid": 0.42,
                "contract_code": "US.SPCH261016C00012500",
                "timeframe": "1d",
                "ema_type": "EMA200",
                "ema_value": 0.38,
            }
        )
        self.assertIsNotNone(touch)
        self.assertEqual(touch["source"], "touch")
        self.assertEqual(touch["side"], "Call")
        self.assertEqual(touch["suggested_limit"], 0.42)
        self.assertEqual(touch["touch_ma"], "EMA200")
        self.assertEqual(touch["touch_timeframe"], "1d")
        self.assertEqual(touch["touch_ma_value"], 0.38)

        opp = maybe_emit_from_opportunity(
            {
                "symbol": "TSLL",
                "side": "PUT",
                "strike": 14,
                "expiry": "2026-11-21",
                "bid": 1.01,
                "ask": 1.1,
                "source": "dual",
                "contract_code": "US.TSLL261121P00014000",
                "timing": {"ema_type": "EMA50", "timeframe": "1h"},
            }
        )
        self.assertIsNotNone(opp)
        self.assertEqual(opp["source"], "dual")
        self.assertEqual(opp["suggested_limit"], 1.01)
        self.assertIsNone(opp["touch_ma"])
        self.assertIsNone(opp["touch_timeframe"])
        self.assertIsNone(opp["touch_ma_value"])
        self.assertEqual(len(repo.list_pending()), 2)

    def test_http_pending_and_ack(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.exec_signal_routes import router
        from app.data import exec_signal_repository as repo

        row = self._emit_tsll_put(
            bid=0.99, timeframe="1h", ema_type="EMA200", ema_value=0.91,
        )
        app = FastAPI()
        app.include_router(router, prefix="/api/wheel")
        client = TestClient(app)

        r = client.get("/api/wheel/exec-signals/pending")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertGreaterEqual(body["count"], 1)
        self.assertTrue(any(i["signal_id"] == row["signal_id"] for i in body["items"]))
        item = next(i for i in body["items"] if i["signal_id"] == row["signal_id"])
        self.assertEqual(item["suggested_limit"], item["bid"])
        self.assertEqual(item["touch_ma"], "EMA200")
        self.assertEqual(item["touch_timeframe"], "1h")
        self.assertEqual(item["touch_ma_value"], 0.91)
        self.assertIn("touch_ma", item)
        self.assertIn("touch_timeframe", item)
        self.assertIn("touch_ma_value", item)

        r2 = client.get("/api/wheel/exec-signals/pending", params={"symbols": "TSLL", "side": "Put"})
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(all(i["symbol"] == "TSLL" and i["side"] == "Put" for i in r2.json()["items"]))

        ack = client.post(
            f"/api/wheel/exec-signals/{row['signal_id']}/ack",
            json={"consumer": "firsttrade-seat", "status": "consumed"},
        )
        self.assertEqual(ack.status_code, 200, ack.text)
        self.assertTrue(ack.json()["ok"])

        ack2 = client.post(
            f"/api/wheel/exec-signals/{row['signal_id']}/ack",
            json={"consumer": "firsttrade-seat", "status": "consumed"},
        )
        self.assertEqual(ack2.status_code, 200)
        self.assertTrue(ack2.json()["idempotent"])

        pending = client.get("/api/wheel/exec-signals/pending").json()
        self.assertFalse(any(i["signal_id"] == row["signal_id"] for i in pending["items"]))
        self.assertEqual(repo.list_pending(symbols=["TSLL"]), [])

    def test_touch_ma_timeframe_and_non_touch_null(self):
        from app.core.exec_signals import maybe_emit_from_touch, maybe_emit_from_opportunity
        from app.data import exec_signal_repository as repo

        # 规范化: ema200 / 60m → EMA200 / 1h
        touch = maybe_emit_from_touch(
            {
                "symbol": "TSLL",
                "signal_level": "WHEEL_PUT",
                "strike": 15,
                "expiry": "2026-11-21",
                "bid": 1.2,
                "contract_code": "US.TSLL261121P00015000",
                "timeframe": "60m",
                "ema_type": "ema200",
                "ema_value": 1.15,
            }
        )
        self.assertIsNotNone(touch)
        self.assertEqual(touch["touch_ma"], "EMA200")
        self.assertEqual(touch["touch_timeframe"], "1h")
        self.assertEqual(touch["touch_ma_value"], 1.15)
        pending = repo.list_pending(symbols=["TSLL"])
        self.assertEqual(pending[0]["touch_ma"], "EMA200")
        self.assertEqual(pending[0]["touch_timeframe"], "1h")
        self.assertEqual(pending[0]["touch_ma_value"], 1.15)

        suggest = maybe_emit_from_opportunity(
            {
                "symbol": "SPCH",
                "side": "CALL",
                "strike": 10,
                "expiry": "2026-12-18",
                "bid": 0.3,
                "source": "suggest",
                "contract_code": "US.SPCH261218C00010000",
                "timing": {"ema_type": "EMA50", "timeframe": "1d"},
            }
        )
        self.assertIsNotNone(suggest)
        self.assertEqual(suggest["source"], "suggest")
        self.assertIsNone(suggest["touch_ma"])
        self.assertIsNone(suggest["touch_timeframe"])
        self.assertIsNone(suggest["touch_ma_value"])

        score = repo.emit_signal(
            symbol="TSLL",
            side="Put",
            strike=16,
            expiry="2026-11-21",
            source="score",
            bid=0.5,
            timeframe="1h",
            ema_type="EMA50",
            contract_code="US.TSLL261121P00016000",
        )
        self.assertIsNone(score["touch_ma"])
        self.assertIsNone(score["touch_timeframe"])
        self.assertIsNone(score["touch_ma_value"])

    def test_normalize_touch_helpers(self):
        from app.data.exec_signal_repository import (
            normalize_touch_ma, normalize_touch_timeframe,
        )
        self.assertEqual(normalize_touch_ma("EMA50"), "EMA50")
        self.assertEqual(normalize_touch_ma("ema200"), "EMA200")
        self.assertEqual(normalize_touch_ma("50"), "EMA50")
        self.assertIsNone(normalize_touch_ma(""))
        self.assertIsNone(normalize_touch_ma(None))
        self.assertEqual(normalize_touch_timeframe("1h"), "1h")
        self.assertEqual(normalize_touch_timeframe("K_DAY"), "1d")
        self.assertEqual(normalize_touch_timeframe("60m"), "1h")
        self.assertIsNone(normalize_touch_timeframe("5m"))



if __name__ == "__main__":
    unittest.main()
