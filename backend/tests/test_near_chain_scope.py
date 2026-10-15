"""触线拉链:近 4 周周五周权 + 近 3 个月月权,且只快照现价附近虚值。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.leaps_monitor import LeapsMonitor  # noqa: E402
from app.core.wheel_expiries import select_near_term_expiries  # noqa: E402


def _strike_of(code: str) -> float:
    return int(code[-8:]) / 1000.0


def test_fetch_scans_near_weeklies_monthlies_and_near_otm_only():
    today = date.today()
    rows = []
    d = today + timedelta(days=1)
    while (d - today).days <= 130:
        if d.weekday() == 4:
            kind = "MONTH" if 15 <= d.day <= 21 else "WEEK"
            rows.append((d.isoformat(), kind))
        elif d.weekday() in (0, 2):
            rows.append((d.isoformat(), "WEEK"))
        d += timedelta(days=1)
    # 明确的远月 LEAP,即使标成月权也不进近 3 个月
    leap = today + timedelta(days=200)
    rows.append((leap.isoformat(), "MONTH"))

    expirations = [r[0] for r in rows]
    cycles = {r[0]: r[1] for r in rows}
    expected, _skipped = select_near_term_expiries(
        expirations, today=today, cycles=cycles, weekly_weeks=4, monthly_months=3,
    )
    expected_dates = [str(e[0])[:10] for e in expected]
    assert expected_dates, "fixture should yield at least one near expiry"
    assert all(e[1] <= 28 for e in expected if e[2] == "weekly")
    assert all(date.fromisoformat(str(e[0])[:10]).weekday() == 4 for e in expected)
    assert sum(1 for e in expected if e[2] == "monthly") <= 3
    assert leap.isoformat() not in expected_dates

    class Ctx:
        def __init__(self):
            self.chain_starts = []
            self.snapshots = []

        def get_option_expiration_date(self, _symbol):
            import futu
            df = pd.DataFrame({
                "strike_time": expirations,
                "expiration_cycle": [cycles[e] for e in expirations],
            })
            return futu.RET_OK, df

        def get_option_chain(self, _symbol, start, end, option_type=None):
            import futu
            self.chain_starts.append(str(start)[:10])
            yymmdd = str(start)[:10].replace("-", "")[2:]
            # 整条链:深度实值、平值、近端虚值、远端虚值
            strikes = [50, 80, 90, 100, 105, 110, 150]
            data = []
            for strike in strikes:
                code = f"US.AAA{yymmdd}P{int(strike * 1000):08d}"
                data.append({"code": code, "strike_price": strike})
            return futu.RET_OK, pd.DataFrame(data)

        def get_market_snapshot(self, codes):
            import futu
            self.snapshots.extend(list(codes))
            data = []
            for code in codes:
                data.append({
                    "code": code,
                    "option_open_interest": 10,
                    "option_implied_volatility": 30,
                    "last_price": 1.2,
                    "high_price": 1.3,
                    "bid_price": 1.1,
                    "ask_price": 1.3,
                    "volume": 5,
                    "option_delta": -0.25,
                    "option_theta": -0.02,
                })
            return futu.RET_OK, pd.DataFrame(data)

    ctx = Ctx()
    mon = LeapsMonitor({
        "futu": {"host": "127.0.0.1", "port": 11111},
        "signal": {"contract_max_per_symbol": 0, "strike_range_pct": 0.20},
    })
    mon.expiry_mode = "near_weekly_monthly"
    mon.weekly_weeks = 4
    mon.monthly_months = 3

    with patch("app.core.leaps_monitor._throttle", lambda *a, **k: None):
        contracts = mon._fetch_eligible_contracts(
            "AAA", 100.0, option_type="PUT",
            range_down=0.20, range_up=0.10,
            quote_ctx=ctx,
        )

    assert sorted(ctx.chain_starts) == sorted(expected_dates)
    assert leap.isoformat() not in ctx.chain_starts
    snapped = {_strike_of(c) for c in ctx.snapshots}
    assert snapped == {80.0, 90.0}, snapped
    assert 50.0 not in snapped  # 远端
    assert 100.0 not in snapped  # 平值
    assert 150.0 not in snapped  # 深度实值
    assert {c["strike"] for c in contracts} == {80.0, 90.0}
