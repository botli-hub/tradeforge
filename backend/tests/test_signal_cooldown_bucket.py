"""信号桶冷却: SYMBOL|SIDE|TF|EMA 共享窗口;同美股交易日不重复。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NY = ZoneInfo("America/New_York")


def _ny(y, m, d, hh=10, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=NY)


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

        now = _ny(2026, 9, 17, 11, 0)
        key = repo.set_signal_bucket_cooldown(
            "QQQ", "PUT", "1h", "EMA50", now=now,
        )
        self.assertEqual(key, "QQQ|PUT|1h|EMA50")
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50", now=now)
        )
        self.assertTrue(
            repo.is_contract_in_cooldown(
                repo.make_signal_cooldown_key("QQQ", "PUT", "1h", "EMA50"),
                now=now,
            )
        )
        # 旧合约键不在表里 → 不算冷却
        self.assertFalse(repo.is_contract_in_cooldown("US.QQQ250919P450000", now=now))

    def test_same_ny_trading_day_skip(self):
        """同一美东交易日内多次检查 → 冷却中(含盘后)。"""
        from app.data import leaps_repository as repo

        open_t = _ny(2026, 9, 17, 10, 0)  # Wed RTH
        after_t = _ny(2026, 9, 17, 18, 0)  # same day after close
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", now=open_t)
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50", now=open_t)
        )
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50", now=after_t)
        )
        # 存的是交易日 id,不是 now+24h
        rows = [
            r for r in repo.get_all_cooldowns(now=open_t)
            if r["contract_code"] == "QQQ|PUT|1h|EMA50"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cooldown_until"], "2026-09-17")

    def test_after_date_roll_not_skip(self):
        """下一美股交易日开盘后 → 不再冷却。"""
        from app.data import leaps_repository as repo

        wed = _ny(2026, 9, 17, 11, 0)
        thu_before = _ny(2026, 9, 18, 9, 0)  # before RTH: still Wed session
        thu_open = _ny(2026, 9, 18, 9, 30)
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", now=wed)
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50", now=thu_before)
        )
        self.assertFalse(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50", now=thu_open)
        )

    def test_weekend_still_on_friday_session(self):
        """周五推送后周末仍冷却,周一开盘解冻。"""
        from app.data import leaps_repository as repo

        fri = _ny(2026, 9, 18, 15, 0)
        sat = _ny(2026, 9, 19, 12, 0)
        mon_pre = _ny(2026, 9, 21, 9, 0)
        mon_open = _ny(2026, 9, 21, 9, 30)
        repo.set_signal_bucket_cooldown("QQQ", "CALL", "1h", "EMA200", now=fri)
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "CALL", "1h", "EMA200", now=sat)
        )
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "CALL", "1h", "EMA200", now=mon_pre)
        )
        self.assertFalse(
            repo.is_signal_bucket_in_cooldown("QQQ", "CALL", "1h", "EMA200", now=mon_open)
        )

    def test_half_day_same_trading_day(self):
        """半日市(午后仍属同一 session 日)不重复。"""
        from app.data import leaps_repository as repo
        from app.core.wheel_today import us_equity_session_date

        # 常见半日收盘 ~13:00 ET;开盘后与午后同属一日
        morning = _ny(2026, 11, 27, 10, 0)  # 假定交易日(weekday)
        midday = _ny(2026, 11, 27, 12, 30)
        self.assertEqual(us_equity_session_date(morning), "2026-11-27")
        self.assertEqual(us_equity_session_date(midday), "2026-11-27")
        repo.set_signal_bucket_cooldown("SPY", "PUT", "1d", "EMA50", now=morning)
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("SPY", "PUT", "1d", "EMA50", now=midday)
        )

    def test_different_ema_type_not_skipped(self):
        from app.data import leaps_repository as repo

        now = _ny(2026, 9, 17, 11, 0)
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", now=now)
        self.assertFalse(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA200", now=now)
        )

    def test_different_timeframe_not_skipped(self):
        from app.data import leaps_repository as repo

        now = _ny(2026, 9, 17, 11, 0)
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", now=now)
        self.assertFalse(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1d", "EMA50", now=now)
        )

    def test_different_side_not_skipped(self):
        from app.data import leaps_repository as repo

        now = _ny(2026, 9, 17, 11, 0)
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", now=now)
        self.assertFalse(
            repo.is_signal_bucket_in_cooldown("QQQ", "CALL", "1h", "EMA50", now=now)
        )

    def test_arm_from_signals_unique_buckets(self):
        from app.data import leaps_repository as repo

        now = _ny(2026, 9, 17, 11, 0)
        sigs = [
            SimpleNamespace(
                symbol="QQQ", signal_level="WHEEL_PUT", timeframe="1h",
                ema_type="EMA50",
            ),
            SimpleNamespace(
                symbol="QQQ", signal_level="WHEEL_PUT", timeframe="1h",
                ema_type="EMA50",
            ),
            SimpleNamespace(
                symbol="QQQ", signal_level="WHEEL_PUT", timeframe="1h",
                ema_type="EMA200",
            ),
        ]
        armed = repo.arm_signal_bucket_cooldowns(sigs, 1, now=now)
        self.assertEqual(sorted(armed), ["QQQ|PUT|1h|EMA200", "QQQ|PUT|1h|EMA50"])
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA50", now=now)
        )
        self.assertTrue(
            repo.is_signal_bucket_in_cooldown("QQQ", "PUT", "1h", "EMA200", now=now)
        )

    def test_trading_days_param_ignored(self):
        """cooldown_trading_days / trading_days 参数对信号桶路径无效。"""
        from app.data import leaps_repository as repo

        now = _ny(2026, 9, 17, 11, 0)
        repo.set_signal_bucket_cooldown("QQQ", "PUT", "1h", "EMA50", trading_days=5, now=now)
        rows = [
            r for r in repo.get_all_cooldowns(now=now)
            if r["contract_code"] == "QQQ|PUT|1h|EMA50"
        ]
        self.assertEqual(rows[0]["cooldown_until"], "2026-09-17")

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


class TestUsEquitySessionDate(unittest.TestCase):
    def test_rth_and_preopen(self):
        from app.core.wheel_today import us_equity_session_date

        self.assertEqual(us_equity_session_date(_ny(2026, 9, 17, 9, 29)), "2026-09-16")
        self.assertEqual(us_equity_session_date(_ny(2026, 9, 17, 9, 30)), "2026-09-17")
        self.assertEqual(us_equity_session_date(_ny(2026, 9, 17, 16, 0)), "2026-09-17")

    def test_weekend_rolls_to_friday(self):
        from app.core.wheel_today import us_equity_session_date

        self.assertEqual(us_equity_session_date(_ny(2026, 9, 19, 12, 0)), "2026-09-18")
        self.assertEqual(us_equity_session_date(_ny(2026, 9, 20, 12, 0)), "2026-09-18")


if __name__ == "__main__":
    unittest.main()
