"""Cross-module accounting/risk regressions from the Wheel review."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import math
import pytest
from app.data import database as db, wheel_repository as repo
from app.core.wheel_nav import nav_from_books, trade_cashflow
from app.core.wheel_execute import apply_draft, register_roll_draft, draft_from_manage
from app.core.wheel_quotes import executable_quote
from app.core.wheel_score import premium_from_quote, score_contract, DEFAULT_SCAN_CFG
from app.core.wheel_risk import evaluate_books, candidate_risk
from app.core.wheel_backtest import option_price, strike_for_delta, run_on_bars, compare_timing


def trade(kind, **kw):
    return dict(trade_type=kind, symbol='XYZ', qty=1, price=0, fee=0,
                contract_size=100, **{}) | kw


def state(ts):
    s = repo._new_state()
    s['symbol'] = 'XYZ'
    for t in ts:
        repo._apply(s,t)
    return s


def put(**kw):
    return trade('SELL_PUT', strike=100, expiry='2026-10-16', contract_code='P', price=2) | kw


@pytest.fixture
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path/'wheel.db')
    db.init_db()
    return tmp_path


def test_partial_put_close_preserves_collateral_and_cash():
    ts=[put(qty=3),trade('BUY_PUT_CLOSE',qty=1,price=1,contract_code='P')]
    s=state(ts)
    nav=nav_from_books(50000,ts,[s],option_marks={'P':1})
    assert s['open_qty']==2 and s['status']=='CSP_OPEN'
    assert nav['csp_collateral']==20000 and nav['equity']==50300
    assert s['realized_pnl']==100


def test_mixed_assignment_stock_call_and_put_are_independent():
    ts=[put(qty=3),trade('ASSIGNED',qty=1,contract_code='P'),
        trade('SELL_CALL',strike=110,expiry='2026-11-20',contract_code='C',price=3)]
    s=state(ts)
    assert s['shares']==100 and s['open_qty']==2 and len(s['open_cc_legs'])==1
    from app.core.wheel_cc_legs import expand_open_option_rows
    assert len(expand_open_option_rows([s]))==2
    repo._apply(s,trade('BUY_CALL_CLOSE',price=1,contract_code='C'))
    assert s['status']=='CSP_OPEN' and s['open_qty']==2
    repo._apply(s,trade('ASSIGNED',qty=2,contract_code='P'))
    assert s['status']=='HOLDING' and s['shares']==300


def calls():
    return [trade('BUY_SHARES',qty=200,price=100),
        trade('SELL_CALL',price=2,strike=110,expiry='2026-10-16',contract_code='C1'),
        trade('SELL_CALL',price=3,strike=120,expiry='2026-11-20',contract_code='C2')]


def test_all_cc_legs_are_marked_in_nav():
    ts=calls();s=state(ts)
    nav=nav_from_books(30000,ts,[s],spots={'XYZ':100},option_marks={'C1':2,'C2':3})
    assert nav['option_mtm']==-500 and nav['equity']==30000
    assert not nav['valuation_incomplete']
    zero=nav_from_books(30000,ts,[s],spots={'XYZ':100},option_marks={'C1':0,'C2':0})
    assert zero['option_mtm']==0


def test_sequential_call_assignment_matches_cash_pnl_including_fees():
    ts=calls()+[trade('CALLED_AWAY',strike=110,contract_code='C1',fee=1),trade('CALLED_AWAY',strike=120,contract_code='C2',fee=2)]
    s=state(ts);nav=nav_from_books(30000,ts,[s])
    assert s['status']=='CLOSED' and s['shares']==0
    assert s['realized_pnl']==3497==nav['equity']-30000
    assert s['stock_realized']==3000 and s['option_realized']==500


def test_partial_stock_sale_preserves_stock_and_net_asset():
    ts=[trade('BUY_SHARES',qty=200,price=100,fee=2),trade('SELL_SHARES',qty=100,price=110,fee=1)]
    s=state(ts);nav=nav_from_books(30000,ts,[s],spots={'XYZ':110})
    assert s['shares']==100 and s['status']=='HOLDING'
    assert s['realized_pnl']==997 and nav['equity']==31997


def test_close_fees_reduce_premium_and_realized_once():
    s=state([put(fee=1),trade('BUY_PUT_CLOSE',price=1,fee=5)])
    assert s['total_premium']==94 and s['realized_pnl']==94 and s['total_fees']==6


@pytest.mark.parametrize('changes',[{'qty':0},{'qty':-1},{'qty':1.5},{'price':float('nan')},{'fee':-1},{'contract_size':0},{'strike':float('inf')}])
def test_invalid_trade_never_mutates_state(changes):
    s=repo._new_state();before=dict(s)
    with pytest.raises(repo.WheelError):repo._apply(s,put(**changes))
    assert s==before


@pytest.mark.parametrize('kind,kwargs',[('BUY_PUT_CLOSE',{'qty':2}),('ASSIGNED',{'qty':2}),('BUY_PUT_CLOSE',{'contract_code':'WRONG'}),('ASSIGNED',{'strike':101})])
def test_overclose_and_wrong_identity_rejected(kind,kwargs):
    s=state([put()]);before=dict(s)
    with pytest.raises(repo.WheelError):repo._apply(s,trade(kind,**kwargs))
    assert s==before


def test_cc_overclose_rejected_without_cash_drift():
    s=state(calls());before=s['cash_balance']
    with pytest.raises(repo.WheelError):repo._apply(s,trade('BUY_CALL_CLOSE',qty=2,contract_code='C1',price=2))
    assert s['cash_balance']==before and len(s['open_cc_legs'])==2


def test_atomic_batch_rollbacks_first_leg(books):
    c=repo.record_trade(**put(),execution_id='open')
    with pytest.raises(repo.WheelError):
        repo.record_trades([trade('BUY_PUT_CLOSE',cycle_id=c['id'],price=1),trade('SELL_PUT',cycle_id=c['id'],strike=100)],execution_id='bad')
    assert repo.get_cycle(c['id'])['status']=='CSP_OPEN'
    assert len(repo.get_trades(cycle_id=c['id']))==1


def test_concurrent_retry_has_exactly_one_effect(books):
    def submit(_):return repo.record_trade(**put(new_cycle=True),execution_id='fill-123')
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(submit,range(8)))
    assert len({r['id'] for r in results})==1
    assert len(repo.get_trades())==1
    with pytest.raises(repo.WheelError):repo.record_trade(**put(price=3),execution_id='fill-123')


def test_inferred_assignment_fields_persist_for_cashflow(books):
    c=repo.record_trade(**put(),execution_id='open')
    repo.record_trade(**trade('ASSIGNED',cycle_id=c['id']),execution_id='assign')
    ts=repo.get_trades(cycle_id=c['id'])
    assigned=next(t for t in ts if t['trade_type']=='ASSIGNED')
    assert assigned['strike']==100
    assert trade_cashflow(assigned)==-10000


def test_partial_roll_retains_original_obligation_and_is_idempotent(books):
    c=repo.record_trade(**put(qty=3),execution_id='open')
    body=dict(execution_id='roll',cycle_id=c['id'],close_contract_code='P',buyback_price=1,
        sell_contract_code='P2',sell_strike=95,sell_expiry='2026-11-20',sell_price=2,qty=1)
    r=register_roll_draft(body)
    assert repo.get_cycle(c['id'])['open_qty']==2
    assert r['cycle']['open_qty']==1 and r['cycle']['id']!=c['id']
    assert register_roll_draft(body)==r
    assert len(repo.get_trades())==3


def test_explicit_cash_config_no_guess_from_caps(books):
    from app.core.wheel_portfolio import portfolio_overview
    repo.upsert_target(dict(symbol='XYZ',name='XYZ',floor_price=100,max_capital=100000))
    r=portfolio_overview()
    assert not r['ok'] and r['starting_cash']==0


def test_recorded_fill_truth_vs_planned_limit(books):
    repo.set_kv('backend_config',json.dumps({'wheel_portfolio':{'total_equity':5000}}))
    with pytest.raises(repo.WheelError):repo.record_trade(**put(),mode='planned',execution_id='plan')
    assert not repo.get_trades()
    c=repo.record_trade(**put(),mode='recorded',execution_id='real')
    assert c['risk_alerts'] and len(repo.get_trades())==1


def test_migration_replays_old_summary_and_flags_invalid_history(books):
    c=repo.record_trade(**put(qty=3),execution_id='open')
    repo.record_trade(**trade('BUY_PUT_CLOSE',qty=1,price=1,cycle_id=c['id']))
    con=db.get_db();con.execute("UPDATE wheel_cycles SET open_qty=0,status='IDLE',accounting_json=NULL");con.commit();con.close()
    db.migrate_wheel_books()
    assert repo.get_cycle(c['id'])['open_qty']==2
    con=db.get_db();con.execute("UPDATE wheel_trades SET qty=-1 WHERE trade_type='SELL_PUT'");con.execute('UPDATE wheel_cycles SET accounting_json=NULL');con.commit();con.close()
    db.migrate_wheel_books()
    assert repo.get_cycle(c['id'])['reconciliation_required']


def fresh(**kw):
    return dict(bid=1,ask=1.04,quote_asof=datetime.now(timezone.utc).isoformat())|kw


def test_quote_quality_fails_closed():
    assert executable_quote(fresh())
    for kw in ({'bid':0},{'ask':0},{'ask':.9},{'quote_asof':None},{'quote_asof':'2000-01-01T00:00:00Z'},{'stale':True},{'ask':float('nan')}):
        assert not executable_quote(fresh(**kw))
    assert premium_from_quote(0,5)==0
    assert premium_from_quote(1,1.04,'bid')==1


def test_confirmed_assignment_and_expiry_do_not_require_buyback_quote():
    item = dict(symbol='XYZ', side='PUT', qty=1, strike=100,
                expiry='2026-10-16', contract_code='P')
    for action in ('assign', 'expire'):
        draft = draft_from_manage(item, action=action)
        assert draft['ok'] and not draft['requires_fill_price']
    assert draft_from_manage(item, action='close')['requires_fill_price']


def test_assignment_cash_gap_is_independent_of_utilization():
    nav=nav_from_books(5000,[put()], [state([put()])],option_marks={'P':2})
    r=evaluate_books(nav,[],{'wheel_portfolio':{'max_portfolio_pct':10,'max_symbol_pct':10}})
    assert r['assignment_cash_shortfall']==4800 and not r['ok']


def test_candidate_checks_post_trade_not_pre_trade():
    nav=nav_from_books(10000,[],[])
    cfg={'wheel_portfolio':{'max_portfolio_pct':.8,'max_symbol_pct':1}}
    r=candidate_risk(nav,dict(fresh(),symbol='XYZ',side='PUT',strike=90),[{'symbol':'XYZ','floor_price':100}],cfg)
    assert not r['ok'] and '交易后组合占用超限' in r['violations']


def test_stale_marks_exposed_as_incomplete():
    nav=nav_from_books(30000,calls(),[state(calls())])
    assert nav['valuation_incomplete']


def daily(n=110):
    start=date(2025,1,1)
    return [{'date':(start+timedelta(days=i)).isoformat(),'close':100+math.sin(i/8)*3} for i in range(n)]


def test_pricing_strike_and_delta_consistent():
    assert option_price(100,80,30/365,.3,'PUT')<option_price(100,95,30/365,.3,'PUT')
    assert strike_for_delta(100,.3,30,.3,'PUT')>strike_for_delta(100,.3,30,.15,'PUT')


def test_scenario_every_equity_point_reconciles_and_terminal_is_paid():
    r=run_on_bars(daily(),{'floor_pct':1,'min_annualized':0})
    assert r['ok'] and r['trade_count']>0 and not r['validated_edge']
    for pt in r['equity_curve']:
        assert pt['equity']==pytest.approx(pt['cash']+pt['stock_mv']-pt['option_liability'])
    assert r['equity_curve'][-1]['option_liability']==0
    assert r['cash']==pytest.approx(100000+sum(t['cashflow'] for t in r['trades']))
    assert any(t['type'].startswith('TERMINAL') for t in r['trades'])


def test_calendar_dte_not_bar_count():
    rows=[b for b in daily(160) if date.fromisoformat(b['date']).weekday()<5]
    r=run_on_bars(rows,{'floor_pct':1,'min_annualized':0,'dte':10})
    opened=next(t for t in r['trades'] if t['type'].startswith('SELL'))
    days=(date.fromisoformat(opened['expiry'])-date.fromisoformat(opened['date'])).days
    assert 10<=days<=12


def test_no_fake_timing_or_earnings_evidence():
    assert not compare_timing(daily(),[])['ok']
    assert not run_on_bars(daily(),{'skip_earnings':True})['ok']


def test_historical_timing_comparison_uses_next_day_and_identical_costs():
    bars=daily();exp=bars[95]['date']
    quotes=[]
    for i,b in enumerate(bars):
        mid=3-i*.02 if i<65 else 4
        quotes.append(dict(date=b['date'],side='PUT',strike=90,expiry=exp,delta=.25,
            contract_code='P',bid=mid-.02,ask=mid+.02))
    r=compare_timing(bars,quotes,{'min_annualized':0,'floor_pct':1},50)
    assert r['ok'] and not r['validated_edge']
    assert r['baseline']['params']==r['timing']['params']
    opens=[t for t in r['timing']['trades'] if t['type']=='SELL_PUT']
    assert opens and opens[0]['date']==bars[66]['date']
    assert not compare_timing(bars,quotes[1:60]+quotes[61:],{'min_annualized':0,'floor_pct':1})['ok']
