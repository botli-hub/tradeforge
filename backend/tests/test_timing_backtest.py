"""Historical timing regressions: causality, selection and auditability."""
from datetime import date, timedelta

import pytest

from app.core.wheel_backtest import compare_timing, run_on_bars


def sample(cross_at=59):
    start = date(2025, 1, 1)
    bars = [{'date': (start + timedelta(days=i)).isoformat(), 'close': 100} for i in range(110)]
    quotes = [dict(date=b['date'], side='PUT', strike=90, expiry=bars[90]['date'],
                   delta=.25, contract_code='P', bid=(3-i*.02 if i < cross_at else 4)-.02,
                   ask=(3-i*.02 if i < cross_at else 4)+.02) for i, b in enumerate(bars)]
    return bars, quotes


def test_warmup_signal_executes_on_first_eligible_day():
    bars, quotes = sample()
    result = compare_timing(bars, quotes, {'min_annualized': 0})
    assert result['ok']
    timing = result['timing']
    first = next(t for t in timing['trades'] if t['type'] == 'SELL_PUT')
    assert first['date'] == bars[60]['date']
    assert first['signal_date'] == bars[59]['date']
    assert first['contract_code'] == 'P'
    assert first['contracts'] == 1 and first['contract_size'] == 100
    assert timing['signal_count'] >= 1 and timing['opened_trade_count'] == 1
    assert timing['signal_definition'] == 'ema_touch_v1'
    assert all(t['contract_code'] == 'P' for t in timing['trades'])


def test_low_yield_nearest_delta_does_not_hide_eligible_contract():
    bars, quotes = sample()
    low_yield = [dict(q, contract_code='LOW', bid=.001, ask=.002, delta=.25) for q in quotes]
    eligible = [dict(q, delta=.26) for q in quotes]
    result = run_on_bars(
        bars, {'min_annualized': 15}, quotes=low_yield + eligible)
    first = next(t for t in result['trades'] if t['type'] == 'SELL_PUT')
    assert first['contract_code'] == 'P'


@pytest.mark.parametrize('change', [{'strike': 91}, {'side': 'CALL'}, {'expiry': '2025-06-01'}])
def test_contract_identity_cannot_change(change):
    bars, quotes = sample()
    quotes[70].update(change)
    result = compare_timing(bars, quotes)
    assert not result['ok'] and '不一致' in result['error']


def test_no_signal_is_reported_and_missing_data_error_is_top_level():
    bars, quotes = sample(cross_at=110)
    result = compare_timing(bars, quotes)
    assert result['ok'] and result['warnings']
    assert result['timing']['opened_trade_count'] == 0
    result = compare_timing(bars, quotes[:61] + quotes[62:])
    assert not result['ok'] and '缺少' in result['error']


def test_future_quotes_do_not_change_earlier_trades():
    bars, quotes = sample()
    params = {'min_annualized': 0}
    before = run_on_bars(bars, params, quotes=quotes, timing_only=True)
    changed = [dict(q, bid=.01, ask=.02) if q['date'] >= bars[75]['date'] else q for q in quotes]
    after = run_on_bars(bars, params, quotes=changed, timing_only=True)
    earlier = lambda result: [t for t in result['trades'] if t['date'] < bars[75]['date']]
    assert earlier(before) == earlier(after)
    for result in (before, after):
        assert result['cash'] == pytest.approx(100000 + sum(t['cashflow'] for t in result['trades']))
        for point in result['equity_curve']:
            assert point['equity'] == pytest.approx(point['cash'] + point['stock_mv'] - point['option_liability'])
