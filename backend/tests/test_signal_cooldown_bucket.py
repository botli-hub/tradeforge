"""信号桶冷却: SYMBOL|SIDE|TF|EMA 共享窗口(防同桶多行权价刷 TG)。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestSignalCooldownBucket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "bucket_cd.db"
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
            conn.execute("DELETE FROM leaps_cooldowns")
            conn.commit()
        finally:
            conn.close()

    def test_key_format(self):
        from app.data import leaps_repository as repo

        self.assertEqual(
            repo.make_signal_cooldown_key("qqq", "PUT", "1h", "EMA50"),
            "QQQ|PUT|1h|EMA50",
        )
        self.assertEqual(
            repo.make_signal_cooldown_key("QQQ", "call", "60m", "200"),
            "QQQ|CALL|1h|EMA200",
        )
        self.assertEqual(
            repo.make_signal_cooldown_key("SPY", "WHEEL_PUT", "daily", "ema50"),
            "SPY|PUT|1d|EMA50",
        )

    def test_same_bucket_second_strike_skipped(self):
        """同桶推过一张后,另一行权价应判定在冷却中。"""
        from app.data import leaps_repository as repo

        key = repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", 1)
        self.assertEqual(key, "QQQ|PUT|1h|EMA50")
        self.assertTrue(repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50"))
        # 另一合约 code 不再是键;同桶仍冷却
        self.assertTrue(
            repo.is_contract_in_cooldown(
                repo.make_signal_cooldown_key("QQQ", "PUT", "1h", "EMA50")
            )
        )
        # 旧合约键不在表里 → 不算冷却(迁移:旧键自然过期/忽略)
        self.assertFalse(repo.is_contract_in_cooldown("US.QQQ250919P450000"))

    def test_different_ema_type_not_skipped(self):
        from app.data import leaps_repository as repo

        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", 1)
        self.assertFalse(repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA200"))

    def test_different_timeframe_not_skipped(self):
        from app.data import leaps_repository as repo

        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", 1)
        self.assertFalse(repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1d", "EMA50"))

    def test_different_side_not_skipped(self):
        from app.data import leaps_repository as repo

        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", 1)
        self.assertFalse(repo.is_signal_bucket_in_cooldown("QQQ", "CALL", "1h", "EMA50"))

    def test_arm_from_signals_unique_buckets(self):
        from app.data import leaps_repository as repo

        sigs = [
            SimpleNamespace(
                symbol="QQQ", signal_level="WHEEL_PUT", timeframe="1h",
                ema_type="EMA50",
            ),
            SimpleNamespace(
                symbol="QQQ", signal_level="WHEEL_PUT", timeframe="1h",
                ema_type="EMA50",  # 同桶
            ),
            SimpleNamespace(
                symbol="QQQ", signal_level="WHEEL_PUT", timeframe="1h",
                ema_type="EMA200",
            ),
        ]
        armed = repo.arm_signal_bucket_cooldowns(sigs, 1)
        self.assertEqual(sorted(armed), ["QQQ|PUT|1h|EMA200", "QQQ|PUT|1h|EMA50"])
        self.assertTrue(repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50"))
        self.assertTrue(repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA200"))

    def test_calendar_day_still_no_1_4(self):
        from app.data import leaps_repository as repo

        before = datetime.now()
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", 5)
        after = datetime.now()
        rows = [
            r for r in repo.get_all_cooldowns()
            if r["contract_code"] == "QQQ|PUT|1h|EMA50"
        ]
        self.assertEqual(len(rows), 1)
        until = datetime.fromisoformat(rows[0]["cooldown_until"])
        self.assertGreaterEqual(until, before + timedelta(days=5) - timedelta(seconds=5))
        self.assertLessEqual(until, after + timedelta(days=5) + timedelta(seconds=5))
        self.assertLess(until, before + timedelta(days=7) - timedelta(hours=1))

    def test_key_from_signal_dict(self):
        from app.data import leaps_repository as repo

        key = repo.signal_cooldown_key_from_signal({
            "symbol": "QQQ",
            "side": "PUT",
            "signal_level": "WHEEL_PUT",
            "timeframe": "1h",
            "ema_type": "EMA50",
        })
        self.assertEqual(key, "QQQ|PUT|1h|EMA50")


if __name__ == "__main__":
    unittest.main()
