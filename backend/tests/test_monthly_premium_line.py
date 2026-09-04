"""本月权利金收入:口径与边界(日历月) + 持仓 digest 行。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.premium_ledger import (  # noqa: E402
    calendar_month_bounds,
    format_monthly_premium_line,
    net_premium_from_trades,
)
from app.services.alert_engine import format_position_digest  # noqa: E402


def test_calendar_month_bounds_and_rollover():
    s, e, label = calendar_month_bounds(date(2026, 9, 5))
    assert s == "2026-09-01"
    assert e == "2026-10-01"
    assert label == "2026-09"
    s2, e2, label2 = calendar_month_bounds(date(2026, 12, 31))
    assert s2 == "2026-12-01"
    assert e2 == "2027-01-01"
    assert label2 == "2026-12"


def test_net_premium_sell_minus_buy_close_with_fee():
    trades = [
        {
            "trade_type": "SELL_PUT",
            "traded_at": "2026-09-02T10:00:00",
            "qty": 1,
            "price": 2.0,
            "contract_size": 100,
            "fee": 1.0,
        },
        {
            "trade_type": "BUY_PUT_CLOSE",
            "traded_at": "2026-09-10T10:00:00",
            "qty": 1,
            "price": 0.5,
            "contract_size": 100,
            "fee": 1.0,
        },
        # 上月不计入
        {
            "trade_type": "SELL_CALL",
            "traded_at": "2026-08-28T10:00:00",
            "qty": 1,
            "price": 9.0,
            "contract_size": 100,
            "fee": 0,
        },
        # 下月不计入
        {
            "trade_type": "SELL_CALL",
            "traded_at": "2026-10-01T00:00:00",
            "qty": 1,
            "price": 3.0,
            "contract_size": 100,
            "fee": 0,
        },
    ]
    # Sep: +(200-1) - (50+1) = 148
    v = net_premium_from_trades(
        trades, month_start="2026-09-01", month_end_exclusive="2026-10-01",
    )
    assert v == 148.0


def test_format_line_and_digest_includes_monthly():
    line = format_monthly_premium_line(amount=1234.5, month="2026-09")
    assert "本月权利金收入" in line
    assert "1,234.50" in line or "1234.50" in line
    body = format_position_digest(
        [{"symbol": "AAPL", "side": "PUT", "dte": 5, "action_hint": "关注", "profit_pct": 10}],
        monthly_premium_line=line,
    )
    assert "本月权利金收入" in body
    assert "AAPL" in body
