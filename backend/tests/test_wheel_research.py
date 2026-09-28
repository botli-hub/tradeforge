from datetime import date, timedelta
import pytest
import pandas as pd
from app.data import database as db, wheel_research_repository as archive
from app.core.wheel_signal import daily_touch, ema_value
from app.core.wheel_timing_klines import ema_touch
from app.core.wheel_stress_model import stress_nav, risk_gate
from app.core.wheel_research_analytics import execution_metrics, attribution, review_position
from app.core.wheel_research import compare_policies
from app.core.wheel_backtest import option_price

@pytest.fixture
def research_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'research.db');db.init_db()

def test_archive_append_only(research_db):
    payload={'contracts':[{'bid':1,'delta':float('nan')}], 'rejected':['spread']}
    a=archive.append_event('chain',payload,'TQQQ');b=archive.append_event('chain',payload,'TQQQ')
    assert a!=b and archive.get_event(a)['digest']==archive.get_event(b)['digest']
    assert archive.get_event(a)['payload']['contracts'][0]['delta'] is None
    assert len(archive.list_events('chain'))==2
    assert 'payload' not in archive.list_events('chain')[0]

def test_live_replay_same_signal():
    closes=pd.Series([4-i*.01 for i in range(70)]+[5.])
    for period in [50,200]:
        hit=ema_touch(closes,5,ema50_min=50,ema200_min=200,allow_partial_ema=False,
                      level_map={f'EMA{period}':'PRIMARY'},bid=4.9,ask=5.1,require_tradeable_quote=True,confirm_with_bid=True)
        assert bool(hit)==daily_touch(closes,5,4.9,period)
    assert ema_value(closes,50)==pytest.approx(closes.ewm(span=50,adjust=False).mean().iloc[-1])
    assert not daily_touch(closes,8,.1,50)

def nav_with_put():
    mark=option_price(100,90,30/365,.4,'PUT')
    return {'starting_cash':100000,'cash':100000+mark*100,'equity':100000,
            'csp_collateral':9000,'stock_rows':[],'spots_used':{'XYZ':100},
            'option_rows':[dict(symbol='XYZ',contract_code='P',side='PUT',qty=1,contract_size=100,mark=mark,strike=90,expiry='2026-10-27')]}

def test_stress_time_value_and_greeks():
    r=stress_nav(nav_with_put(),'2026-09-27')
    assert r['ok'] and r['worst_loss']>2000
    assert r['greeks']['delta_dollars']>0 and r['greeks']['gamma_1pct_dollars']<0 and r['greeks']['vega_1point_dollars']<0
    assert r['expiry_notional']=={'2026-10-27':9000}

def test_missing_risk_is_not_zero():
    nav=nav_with_put();nav['option_rows'][0]['expiry']=None
    r=stress_nav(nav,'2026-09-27')
    assert not r['ok'] and r['worst_loss'] is None
    assert all(s['equity'] is None for s in r['scenarios'])
    assert not risk_gate(nav,{'wheel_risk_budget':{'enabled':True}},asof='2026-09-27')['ok']

def test_budget_observe_and_enforce():
    cfg={'wheel_risk_budget':{'enabled':True,'max_stress_loss_pct':1}}
    r=risk_gate(nav_with_put(),cfg,asof='2026-09-27')
    assert not r['ok'] and '交易后组合压力损失超预算' in r['violations']
    cfg['wheel_risk_budget']['enabled']=False
    assert risk_gate(nav_with_put(),cfg,asof='2026-09-27')['ok']

def test_candidate_sizing_and_leverage():
    nav=nav_with_put();nav.update(option_rows=[],cash=100000,equity=100000,csp_collateral=0)
    o=dict(symbol='TQQQ',side='PUT',qty=1,contract_size=100,spot=100,strike=90,expiry='2026-10-27',bid=1.5,ask=1.6)
    r=risk_gate(nav,{'wheel_risk_budget':{'enabled':True}},o,asof='2026-09-27')
    assert not r['ok'] and r['suggested_max_qty']==0
    assert '杠杆标的未获风险预算准入' in r['violations']

def test_stale_nav_excluded_from_peak(research_db):
    nav=nav_with_put();archive.observe_nav(nav)
    nav.update(equity=200000,valuation_incomplete=True)
    assert archive.observe_nav(nav)['peak_equity']==100000
    nav.update(equity=80000,valuation_incomplete=False)
    dd=archive.observe_nav(nav)
    assert dd['drawdown_pct']==20
    assert not risk_gate(nav,{'wheel_risk_budget':{'enabled':True}},drawdown=dd,asof='2026-09-27')['ok']

def fill(**kw):
    return dict(side='SELL',status='filled',requested_qty=1,filled_qty=1,contract_size=100,
                reference_price=2,fill_price=1.9,submitted_at='2026-09-01T14:00:00Z',filled_at='2026-09-01T14:00:05Z',
                mark_after=2.1,mark_after_at='2026-09-01T14:01:00Z')|kw

def test_execution_metric_signs():
    r=execution_metrics(fill())
    assert r['shortfall_dollars']==pytest.approx(10) and r['markout_dollars']==pytest.approx(-20)
    assert r['fill_latency_seconds']==5
    b=execution_metrics(fill(side='BUY',fill_price=2.1,mark_after=2))
    assert b['shortfall_dollars']==pytest.approx(10) and b['markout_dollars']==pytest.approx(-10)

@pytest.mark.parametrize('kw',[{'filled_qty':2},{'fill_price':float('nan')},{'submitted_at':'2026-09-01T14:00:00'},
                               {'filled_at':'2026-08-01T00:00:00Z'},{'mark_after_at':'2026-09-01T14:00:01Z'},
                               {'status':'partial'},{'status':'pending'}])
def test_bad_execution_rejected(kw):
    with pytest.raises(ValueError):execution_metrics(fill(**kw))

def test_execution_api_idempotence(research_db):
    from app.api.wheel_research import ExecutionIn,execution
    from fastapi import HTTPException
    body=ExecutionIn(**fill(id='x',symbol='XYZ',contract_code='P'))
    assert execution(body)==execution(body)
    with pytest.raises(HTTPException) as e:execution(body.model_copy(update={'fill_price':1.8}))
    assert e.value.status_code==409

def test_attribution_partial_close():
    from app.core.wheel_ledger import apply_trade
    from app.data.wheel_repository import _new_state
    from app.core.wheel_nav import nav_from_books
    s=_new_state();s['symbol']='XYZ'
    trades=[dict(symbol='XYZ',trade_type='SELL_PUT',contract_code='P',strike=90,expiry='2026-10-27',qty=2,price=2,fee=1.3,contract_size=100),
            dict(symbol='XYZ',trade_type='BUY_PUT_CLOSE',contract_code='P',qty=1,price=1,fee=.65,contract_size=100)]
    for t in trades:apply_trade(s,t)
    r=attribution(nav_from_books(100000,trades,[s],option_marks={'P':1.2}),[s])
    assert r['reconciled'] and r['option_realized_gross']==100 and r['option_unrealized']==80
    assert r['total_pnl']==pytest.approx(178.05)

def history():
    start=date(2025,1,1)
    bars=[{'date':(start+timedelta(days=i)).isoformat(),'close':100} for i in range(110)]
    quotes=[dict(date=b['date'],side='PUT',strike=90,expiry=bars[95]['date'],delta=.25,contract_code='P',
                 bid=(3-i*.02 if i<65 else 4)-.02,ask=(3-i*.02 if i<65 else 4)+.02) for i,b in enumerate(bars)]
    return bars,quotes

def test_four_groups_holdout_causality():
    bars,quotes=history()
    r=compare_policies(bars,quotes,{'min_annualized':0},50,0,bars[70]['date'])
    assert r['ok'] and not r['validated_edge'] and len(r['results'])==4
    assert r['out_of_sample']['start']==bars[70]['date']
    touch=r['results']['score_touch'];first=next(t for t in touch['trades'] if t['type']=='SELL_PUT')
    assert first['date']==bars[66]['date'] and first['signal_date']<first['date']
    assert touch['evidence_status']=='insufficient_sample'
    assert not r['results']['score_touch_regime']['opened_trade_count']
    for result in r['out_of_sample']['results'].values():
        assert all(t['date']>=bars[70]['date'] for t in result['trades'])

def test_review_current_rejection():
    r=review_position({'side':'PUT','strike':90,'buyback_ask':2,'qty':2},
                      {'would_open_today':False,'would_open_reasons':['趋势不符'],'early_assign_risk':True})
    assert r['review_required'] and r['assignment_cash']==18000 and r['remaining_premium_at_risk']==400

def test_execution_revision_preserves_history(research_db):
    from app.api.wheel_research import ExecutionIn, execution
    from fastapi import HTTPException
    pending=ExecutionIn(**fill(id='order',symbol='XYZ',contract_code='P',status='pending',filled_qty=0,filled_at=None,fill_price=None,mark_after=None,mark_after_at=None))
    first=execution(pending)
    updated=ExecutionIn(**fill(id='order',symbol='XYZ',contract_code='P',expected_revision=first['revision']))
    second=execution(updated)
    assert second['revision']==2
    assert execution(updated)==second
    assert len(archive.list_events('execution'))==2
    with pytest.raises(HTTPException) as e:execution(updated.model_copy(update={'fill_price':1.7}))
    assert e.value.status_code==409


def test_research_http_roundtrip_and_settings(research_db,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.wheel_research import router
    from app.core import wheel_nav
    monkeypatch.setattr(wheel_nav, 'compute_account_nav', lambda *a,**k: {
        **nav_with_put(), 'option_rows':[], 'stock_mv':0,'stock_cost':0,'option_mtm':0,
        'cash':100000,'equity':100000})
    app=FastAPI();app.include_router(router,prefix='/research')
    client=TestClient(app)
    assert client.get('/research/overview').status_code==200
    assert client.put('/research/settings',json={'archive_interval_minutes':1}).status_code==400
    assert client.put('/research/settings',json={'enabled':True,'archive_interval_minutes':15}).status_code==200
    assert client.get('/research/overview').json()['risk_budget']['enabled']
    bars,quotes=history()
    r=client.post('/research/compare',json={'bars':bars,'quotes':quotes,'params':{'min_annualized':0}})
    assert r.status_code==200 and r.json()['ok']
    event=client.get('/research/events/'+r.json()['research_event_id']).json()
    assert event['payload']['request']['bars']==bars
    assert len(event['payload']['result']['results'])==4
    quotes[0]['strike']=0
    assert client.post('/research/compare',json={'bars':bars,'quotes':quotes}).status_code==400


def test_chain_snapshot_keeps_rejected_universe(research_db,monkeypatch):
    from app.services import wheel_scanner as scanner
    from app.api import options
    chain={'spot_price':100, 'contracts':[{'option_symbol':'GOOD','bid':1,'ask':1.01},
                                         {'option_symbol':'BAD','bid':0,'ask':5}]}
    monkeypatch.setattr(options,'_load_option_chain',lambda *a:chain)
    scanner.clear_cache()
    result=scanner.cached_chain('XYZ','2026-10-27','unused',1,force=True)
    saved=archive.get_event(result['research_snapshot_id'])
    assert [r['option_symbol'] for r in saved['payload']['contracts']]==['GOOD','BAD']
    scanner.cached_chain('XYZ','2026-10-27','unused',1)
    assert len(archive.list_events('chain'))==1
    scanner.clear_cache()


def test_future_quotes_do_not_rewrite_past_comparison():
    bars,quotes=history();params={'min_annualized':0}
    a=compare_policies(bars,quotes,params,50,0)
    changed=[dict(q,bid=.02,ask=.021) if q['date']>=bars[80]['date'] else q for q in quotes]
    b=compare_policies(bars,changed,params,50,0)
    for key in a['results']:
        earlier=lambda r: [t for t in r['trades'] if t['date']<bars[80]['date']]
        assert earlier(a['results'][key])==earlier(b['results'][key])

def test_roll_credit_does_not_hide_realized_loss():
    from app.core.wheel_research_analytics import roll_review
    r=roll_review(1,{'qty':1,'contract_size':100,'price':3,'fee':.65},
                    {'trade_type':'SELL_PUT','strike':90,'price':4,'fee':.65,'expiry':'2026-12-18'})
    assert r['closed_leg_pnl_before_original_entry_fee']==pytest.approx(-200.65)
    assert r['net_cashflow']==pytest.approx(98.7)
    assert r['new_assignment_notional']==9000


def test_budget_registration_preserves_actual_fills(research_db,monkeypatch):
    from app.data import wheel_repository as repo
    from app.core import wheel_floor
    monkeypatch.setattr(wheel_floor,'resolve_willing_price',lambda *a,**k:100)
    repo.set_kv('backend_config',__import__('json').dumps({'wheel_portfolio':{'total_equity':100000},'wheel_risk_budget':{'enabled':True}}))
    trade=dict(symbol='XYZ',trade_type='SELL_PUT',contract_code='P',strike=90,expiry='2026-12-18',qty=1,price=2)
    with pytest.raises(repo.WheelError):repo.record_trade(**trade,mode='planned',execution_id='plan')
    assert not repo.get_trades()
    result=repo.record_trade(**trade,mode='recorded',execution_id='fill')
    assert result['risk_alerts'] and len(repo.get_trades())==1
    # A reduction can be planned even when existing risk marks are missing.
    repo.record_trade(symbol='XYZ',cycle_id=result['id'],trade_type='BUY_PUT_CLOSE',contract_code='P',qty=1,price=1,mode='planned',execution_id='reduce')
    assert len(repo.get_trades())==2


def test_old_daily_close_is_not_a_current_risk_mark(research_db,monkeypatch):
    from app.core.wheel_nav import load_nav_marks
    from app.core import wheel_today
    from app.data.history_repository import upsert_kline_bars
    monkeypatch.setattr(wheel_today,'load_positions_cache',lambda **kw:None)
    upsert_kline_bars('XYZ','1d',[dict(timestamp='2000-01-01',open=100,high=100,low=100,close=100)],'test')
    assert 'XYZ' not in load_nav_marks(['XYZ'])[0]

def test_contract_after_sample_end_is_liquidated_not_silently_filtered():
    from app.core.wheel_backtest import run_on_bars
    bars,quotes=history()
    # At the first trading day, expiration is 30 days away but outside this window.
    quotes=[dict(q,expiry=bars[90]['date']) for q in quotes]
    result=run_on_bars(bars[:75],{'min_annualized':0},quotes=quotes)
    assert result['ok'] and result['opened_trade_count']>0
    assert result['trades'][-1]['type']=='TERMINAL_BUY_PUT'
    assert result['equity_curve'][-1]['option_liability']==0
