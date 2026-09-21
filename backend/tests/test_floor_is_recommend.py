"""愿接=推荐价:取消独立可手改 floor。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_floor import (  # noqa: E402
    suggest_floor,
    resolve_willing_price,
    apply_willing_floor,
)
from app.core.wheel_decision import decide_position  # noqa: E402


def test_suggest_ignores_hand_edited_current_floor():
    """手改 floor=150 不得压过市场结构推荐价。"""
    with patch("app.core.wheel_floor._closes", return_value=[]):
        r = suggest_floor("SPCX", spot=200.0, current_floor=150.0, iv_rank=40)
    assert r["suggested_floor"] == 180.0  # spot*0.9
    assert r["suggested_floor"] != 150.0


def test_resolve_willing_uses_recommend_not_hand_floor():
    with patch("app.core.wheel_floor._closes", return_value=[]):
        w = resolve_willing_price("SPCX", 200.0, 40, 150.0)
    assert w == 180.0


def test_apply_willing_floor_overwrites_hand_edit():
    t = {"symbol": "SPCX", "floor_price": 150.0}
    with patch("app.core.wheel_floor._closes", return_value=[]):
        apply_willing_floor(t, spot=200.0, iv_rank=40, sync_db=False)
    assert t["floor_price"] == 180.0
    assert t["suggested_floor"] == 180.0
    assert t["suggested_floor_delta"] == 0.0


def test_strike_above_floor_filter_uses_recommend_price():
    """strike_above_floor 以传入的 floor_price(应为推荐价)为准。"""
    item = dict(
        side="PUT", strike=100.0, spot=95.0, dte=20, current_price=2.0,
        buyback_ask=2.0, open_price=3.0, profit_pct=-10.0, itm=True,
        delta=0.55, expiring=False, qty=1, contract_size=100,
        floor_price=90.0, stance="acquire",
    )
    r = decide_position(item, 15, 50)
    assert r["strike_above_floor"] is True

    item2 = dict(item, strike=85.0, floor_price=90.0, itm=False, spot=100.0, delta=0.2, profit_pct=40.0)
    r2 = decide_position(item2, 15, 50)
    assert r2["strike_above_floor"] is False


def test_hand_floor_must_be_replaced_before_decision():
    """回归 SPCX:手改 150 若未替换, strike=140 假阴性;替换为推荐价后正确。"""
    bad = dict(
        side="PUT", strike=140.0, spot=160.0, dte=25, current_price=1.5,
        buyback_ask=1.5, open_price=3.0, profit_pct=50.0, itm=False,
        delta=0.25, expiring=False, qty=1, contract_size=100,
        floor_price=150.0, stance="acquire",
    )
    assert decide_position(bad, 15, 50)["strike_above_floor"] is False

    with patch("app.core.wheel_floor._closes", return_value=[]):
        willing = resolve_willing_price("SPCX", 160.0, 40, 150.0)
    assert willing == 144.0  # 160*0.9

    over = dict(bad, strike=150.0, floor_price=willing)
    assert decide_position(over, 15, 50)["strike_above_floor"] is True
