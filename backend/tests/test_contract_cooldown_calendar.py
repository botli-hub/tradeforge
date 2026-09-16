"""合约冷却：config 天数 = 自然日历日（不再 ×1.4）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestContractCooldownCalendarDays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmpdir.name) / "cooldown_test.db"
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

    def test_days_1_is_one_calendar_day_not_1_4(self):
        """fill/config days=1 → 冷却约 1 个自然日，而不是 int(1×1.4)=1 的巧合，
        用 days=2 可区分：旧逻辑 int(2×1.4)=2 仍碰巧；用 days=5：
        旧 = int(5×1.4)=7，新 = 5。
        """
        from app.data import leaps_repository as repo

        before = datetime.now()
        repo.set_contract_cooldown("TEST.C", "TEST", 1)
        after = datetime.now()

        rows = repo.get_all_cooldowns()
        self.assertEqual(len(rows), 1)
        until = datetime.fromisoformat(rows[0]["cooldown_until"])

        # 应落在 [now+1d − 小误差, now+1d + 小误差]
        lo = before + timedelta(days=1) - timedelta(seconds=2)
        hi = after + timedelta(days=1) + timedelta(seconds=2)
        self.assertGreaterEqual(until, lo)
        self.assertLessEqual(until, hi)

        # 明确不是旧 ×1.4 对 days=5 的行为（7 天）
        repo.set_contract_cooldown("TEST.C5", "TEST", 5)
        rows5 = [r for r in repo.get_all_cooldowns() if r["contract_code"] == "TEST.C5"]
        self.assertEqual(len(rows5), 1)
        until5 = datetime.fromisoformat(rows5[0]["cooldown_until"])
        # 新：~5 天；旧：~7 天。要求严格落在 ~5 天窗口，且明显早于 ~7 天。
        lo5 = before + timedelta(days=5) - timedelta(seconds=5)
        hi5 = after + timedelta(days=5) + timedelta(seconds=5)
        self.assertGreaterEqual(until5, lo5)
        self.assertLessEqual(until5, hi5)
        old_style_approx = before + timedelta(days=7) - timedelta(hours=1)
        self.assertLess(until5, old_style_approx)

    def test_is_contract_in_cooldown_true_then_false(self):
        from app.data import leaps_repository as repo

        repo.set_contract_cooldown("TEST.CD", "TEST", 1)
        self.assertTrue(repo.is_contract_in_cooldown("TEST.CD"))
        # 0 天：立即到期（或已过）
        repo.set_contract_cooldown("TEST.CD0", "TEST", 0)
        # cooldown_until ≈ now；is_contract_in_cooldown 用 > now，可能刚好为 False
        # 至少不应按 ×1.4 拉长
        rows = [r for r in repo.get_all_cooldowns() if r["contract_code"] == "TEST.CD0"]
        # days=0 → until≈now → 通常不在 active 列表（cooldown_until > now 失败）
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
