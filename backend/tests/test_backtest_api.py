import asyncio
import unittest
from unittest.mock import patch

from app.api.backtest import BacktestParams, run_backtest
from app.data.database import get_db, init_db


class BacktestApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        conn = get_db()
        row = conn.execute("SELECT id FROM strategies ORDER BY updated_at DESC LIMIT 1").fetchone()
        conn.close()
        cls.strategy_id = row["id"]

    def test_run_backtest_accepts_single_symbol_payload_and_returns_full_result(self):
        payload = asyncio.run(
            run_backtest(
                BacktestParams(
                    strategy_id=self.strategy_id,
                    symbol="AAPL",
                    timeframe="1d",
                    start_date="2024-01-01",
                    end_date="2024-03-01",
                    initial_capital=100000,
                )
            )
        )
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["symbols"], ["AAPL"])
        self.assertIn("metrics", payload)
        self.assertIn("trades", payload)
        self.assertIn("equity_curve", payload)
        self.assertIn("data_sources", payload)

    def test_run_backtest_errors_instead_of_mock_when_local_history_load_fails(self):
        # 45c0ba8 起移除 mock 数据:历史加载失败必须报错(502),不能静默用假数据回测
        from fastapi import HTTPException
        with patch("app.api.backtest.ensure_local_kline_range", side_effect=RuntimeError("history unavailable")), \
                self.assertRaises(HTTPException) as cm:
            asyncio.run(
                run_backtest(
                    BacktestParams(
                        strategy_id=self.strategy_id,
                        symbols=["AAPL"],
                        timeframe="1d",
                        start_date="2024-01-01",
                        end_date="2024-03-01",
                    )
                )
            )
        self.assertEqual(cm.exception.status_code, 502)
        self.assertIn("历史数据获取失败", str(cm.exception.detail))


if __name__ == "__main__":
    unittest.main()
