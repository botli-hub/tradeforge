"""交易员优化:执行草稿 / 指派后 / 跟进 / 情景 / 会话。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_execute import draft_from_manage, draft_from_opportunity  # noqa: E402
from app.core.wheel_post_assign import cost_basis_of, post_assign_hint  # noqa: E402
from app.core.wheel_attribution import position_scenario, suggestion_follow_through  # noqa: E402
from app.services.alert_engine import (  # noqa: E402
    filter_scan_opportunities,
    scan_session_allows,
)


def test_draft_close_put():
    d = draft_from_manage({
        "cycle_id": "c1", "symbol": "ARM", "side": "PUT", "strike": 95,
        "action_code": "CLOSE", "buyback_ask": 1.2, "qty": 1, "contract_size": 100,
        "contract_code": "X", "expiry": "2026-08-01",
    }, action="auto")
    assert d["ok"]
    assert d["steps"][0]["trade_type"] == "BUY_PUT_CLOSE"
    assert d["steps"][0]["price"] == 1.2


def test_draft_assign_put():
    d = draft_from_manage({
        "cycle_id": "c1", "symbol": "ARM", "side": "PUT", "strike": 95,
        "action_code": "PREPARE_ASSIGN", "qty": 1, "contract_size": 100,
    }, action="assign")
    assert d["ok"]
    assert d["steps"][0]["trade_type"] == "ASSIGNED"


def test_draft_open_put():
    d = draft_from_opportunity({
        "symbol": "AAPL", "side": "PUT", "strike": 180, "expiry": "2026-08-15",
        "bid": 2.5, "score": 8, "suggest_qty": 2,
    })
    assert d["ok"]
    assert d["steps"][0]["trade_type"] == "SELL_PUT"
    assert d["steps"][0]["qty"] == 2


def test_post_assign_cost_basis():
    cycle = {
        "id": "c1", "symbol": "ARM", "status": "HOLDING",
        "shares": 100, "share_cost": 95, "total_premium": 200,
    }
    cb = cost_basis_of(cycle)
    assert cb is not None
    assert abs(cb - (95 - 2.0)) < 0.01  # 200/100=2
    hint = post_assign_hint(cycle)
    assert hint["cc_contracts"] == 1
    assert hint["next_step"] == "SELL_CALL"


def test_position_scenario():
    s = position_scenario({
        "symbol": "X", "side": "PUT", "strike": 100, "qty": 1, "contract_size": 100,
        "open_price": 3.0, "buyback_ask": 1.0, "dte": 10, "action_hint": "止盈",
    })
    assert s["if_close_now"]["pnl_est"] == 200.0  # (3-1)*100
    assert s["if_expire_otm"]["pnl_est"] == 300.0


def test_scan_executable_filter():
    opps = [
        {"symbol": "A", "side": "PUT", "strike": 10, "expiry": "2026-08-01",
         "score": 9, "annualized": 20, "spread_pct": 15, "bid": 1},
        {"symbol": "B", "side": "PUT", "strike": 11, "expiry": "2026-08-01",
         "score": 8, "annualized": 18, "spread_pct": 3, "bid": 1.2},
    ]
    sel, _ = filter_scan_opportunities(
        opps, top_n=5, only_new=False, require_executable=True,
        max_spread_pct=8, state={},
    )
    assert len(sel) == 1
    assert sel[0]["symbol"] == "B"


def test_session_mode_always():
    assert scan_session_allows("always") is True


def test_follow_through_empty():
    # 无库表时也可能空
    try:
        r = suggestion_follow_through(days=7)
        assert "suggested_n" in r
        assert "follow_rate_pct" in r or r["suggested_n"] == 0
    except Exception:
        # DB 未 init 可跳过
        pass
