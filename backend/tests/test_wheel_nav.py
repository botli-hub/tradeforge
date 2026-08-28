"""账户权益 = 起始现金 + 持股市值 + 期权盯市。不含交易建议。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.wheel_nav import nav_from_books, trade_cashflow  # noqa: E402


def test_cashflow_types():
    assert trade_cashflow({"trade_type": "SELL_PUT", "qty": 1, "price": 2, "contract_size": 100, "fee": 1}) == 199
    assert trade_cashflow({"trade_type": "BUY_PUT_CLOSE", "qty": 1, "price": 0.8, "contract_size": 100, "fee": 0}) == -80
    assert trade_cashflow({"trade_type": "BUY_SHARES", "qty": 100, "price": 50, "fee": 0}) == -5000
    assert trade_cashflow({"trade_type": "ASSIGNED", "qty": 1, "strike": 100, "contract_size": 100, "fee": 0}) == -10000
    assert trade_cashflow({"trade_type": "CALLED_AWAY", "qty": 1, "strike": 55, "contract_size": 100, "fee": 0}) == 5500
    assert trade_cashflow({"trade_type": "EXPIRE", "fee": 0}) == 0


def test_seed_only_is_starting_cash():
    nav = nav_from_books(120000, [], [], spots={}, option_marks={})
    assert nav["cash"] == 120000
    assert nav["stock_mv"] == 0
    assert nav["option_mtm"] == 0
    assert nav["equity"] == 120000


def test_sell_put_premium_nets_with_open_mark():
    trades = [{"trade_type": "SELL_PUT", "qty": 1, "price": 2.0, "contract_size": 100, "fee": 0}]
    cycles = [{
        "symbol": "AAA", "status": "CSP_OPEN", "shares": 0,
        "open_qty": 1, "open_price": 2.0, "open_contract_size": 100,
        "open_contract_code": "AAA_P", "open_option_type": "PUT",
    }]
    nav = nav_from_books(120000, trades, cycles, option_marks={"AAA_P": 2.0})
    assert nav["cash"] == 120200
    assert nav["option_mtm"] == -200
    assert nav["equity"] == 120000
    assert nav["idle_cash"] == 120200  # 无 strike 则无 CSP 占用



def test_option_mark_move_changes_equity():
    trades = [{"trade_type": "SELL_PUT", "qty": 1, "price": 2.0, "contract_size": 100, "fee": 0}]
    cycles = [{
        "symbol": "AAA", "status": "CSP_OPEN", "shares": 0,
        "open_qty": 1, "open_price": 2.0, "open_contract_size": 100,
        "open_contract_code": "AAA_P", "open_option_type": "PUT",
    }]
    nav = nav_from_books(120000, trades, cycles, option_marks={"AAA_P": 0.5})
    assert nav["cash"] == 120200
    assert nav["option_mtm"] == -50
    assert nav["equity"] == 120150  # 权利金已收,买回变便宜


def test_csp_idle_is_cash_minus_collateral():
    trades = [{"trade_type": "SELL_PUT", "qty": 1, "price": 2.0, "contract_size": 100, "fee": 0}]
    cycles = [{
        "symbol": "AAA", "status": "CSP_OPEN", "shares": 0, "open_strike": 100,
        "open_qty": 1, "open_price": 2.0, "open_contract_size": 100,
        "open_contract_code": "AAA_P", "open_option_type": "PUT",
    }]
    nav = nav_from_books(120000, trades, cycles, option_marks={"AAA_P": 2.0})
    assert nav["csp_collateral"] == 10000
    assert nav["idle_cash"] == 110200
    assert nav["total_committed"] == 10000
    assert nav["equity"] == 120000


def test_stock_mark_and_register_buy():
    trades = [{"trade_type": "BUY_SHARES", "qty": 100, "price": 100, "fee": 0}]
    cycles = [{"symbol": "BBB", "status": "HOLDING", "shares": 100, "share_cost": 100}]
    up = nav_from_books(20000, trades, cycles, spots={"BBB": 110})
    assert up["cash"] == 10000  # 20000-10000
    assert up["stock_mv"] == 11000
    assert up["equity"] == 21000
    down = nav_from_books(20000, trades, cycles, spots={"BBB": 90})
    assert down["stock_mv"] == 9000
    assert down["equity"] == 19000


def test_called_away_realizes_to_cash():
    trades = [
        {"trade_type": "BUY_SHARES", "qty": 100, "price": 50, "fee": 0},
        {"trade_type": "SELL_CALL", "qty": 1, "price": 1.5, "contract_size": 100, "fee": 0},
        {"trade_type": "CALLED_AWAY", "qty": 1, "strike": 55, "contract_size": 100, "fee": 0},
    ]
    nav = nav_from_books(10000, trades, [], spots={}, option_marks={})
    # 起始 10000 - 买股 5000 + 权利金 150 + 交货 5500 = 10650
    assert nav["cash"] == 10650
    assert nav["stock_mv"] == 0
    assert nav["option_mtm"] == 0
    assert nav["equity"] == 10650


def test_idle_cash_excludes_csp_collateral():
    trades = [{"trade_type": "SELL_PUT", "qty": 1, "price": 2.0, "contract_size": 100, "fee": 0}]
    cycles = [{
        "symbol": "AAA", "status": "CSP_OPEN", "shares": 0,
        "open_qty": 1, "open_price": 2.0, "open_strike": 100,
        "open_contract_size": 100, "open_contract_code": "AAA_P",
    }]
    nav = nav_from_books(120000, trades, cycles, option_marks={"AAA_P": 2.0})
    assert nav["cash"] == 120200
    assert nav["csp_collateral"] == 10000
    assert nav["idle_cash"] == 110200


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(fails)
