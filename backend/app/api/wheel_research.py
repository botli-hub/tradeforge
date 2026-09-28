"""Research and risk workbench. No brokerage or notification side effects."""
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.data.database import get_db
from app.data import wheel_research_repository as store
from app.core.wheel_stress_model import settings, risk_gate

router=APIRouter()


def config():
    from app.core.config import get_effective_config
    return get_effective_config()


@router.get('/overview')
def overview():
    from app.core.wheel_nav import compute_account_nav
    from app.data import wheel_repository as repo
    from app.core.wheel_research_analytics import attribution
    cfg=config(); nav=compute_account_nav(float((cfg.get('wheel_portfolio') or {}).get('total_equity') or 0))
    drawdown=store.observe_nav(nav)
    conn=get_db()
    try:
        store.ensure_tables(conn)
        rows=[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM wheel_execution_observations ORDER BY created_at DESC LIMIT 1000')]
    finally:conn.close()
    completed=[r for r in rows if r['status'] in ('filled','cancelled')]
    fills=[r for r in rows if r['filled_qty']>0]
    known=[r for r in fills if r['shortfall_dollars'] is not None]
    return {'nav':nav,'risk_budget':risk_gate(nav,cfg,drawdown=drawdown),'attribution':attribution(nav,repo.get_cycles(include_closed=True)),
            'events':store.list_events(limit=15),
            'execution':{'records':len(rows),'completed_orders':len(completed),
                         'unfilled_rate':sum(r['filled_qty']==0 for r in completed)/len(completed) if completed else None,
                         'shortfall_dollars':sum(r['shortfall_dollars'] for r in known) if known else None,
                         'measured_fills':len(known),'items':rows[:50]},
            'archive_interval_minutes':cfg.get('wheel_research',{}).get('archive_interval_minutes',0)}


@router.get('/events')
def events(kind: str|None=None, before: str|None=None, limit:int=Query(50,ge=1,le=500)):
    return {'items':store.list_events(kind,limit,before)}


@router.get('/events/{event_id}')
def event(event_id:str):
    result=store.get_event(event_id)
    if result is None:raise HTTPException(404,'归档不存在')
    return result


class ResearchIn(BaseModel):
    bars:list[dict[str,Any]] = Field(max_length=10000)
    quotes:list[dict[str,Any]] = Field(max_length=200000)
    params:dict[str,Any] = Field(default_factory=dict)
    ema_period:int=50
    score_threshold:float=15
    test_start:str|None=None


@router.post('/compare')
def compare(body:ResearchIn):
    from app.core.wheel_research import compare_policies
    try:
        result=compare_policies(**body.model_dump())
        event_id=store.append_event('experiment',{'request':body.model_dump(),'result':result})
        return {**result,'research_event_id':event_id}
    except (ValueError,TypeError,KeyError,OverflowError) as e:raise HTTPException(400,str(e))


class SettingsIn(BaseModel):
    enabled:bool=False
    max_stress_loss_pct:float=Field(20,gt=0,le=100)
    per_trade_loss_pct:float=Field(2,gt=0,le=100)
    leveraged_trade_loss_pct:float=Field(1,gt=0,le=100)
    drawdown_stop_pct:float=Field(15,gt=0,le=100)
    leveraged_allowed:bool=False
    archive_interval_minutes:int=Field(0,ge=0,le=1440)


@router.put('/settings')
def save_settings(body:SettingsIn):
    from app.core.config import get_db_overrides
    from app.data.wheel_repository import set_kv
    cfg=get_db_overrides();data=body.model_dump();interval=data.pop('archive_interval_minutes')
    if 0<interval<15:raise HTTPException(400,'归档间隔至少 15 分钟；0 表示关闭')
    cfg['wheel_risk_budget']={**(cfg.get('wheel_risk_budget') or {}),**data}
    cfg['wheel_research']={**(cfg.get('wheel_research') or {}),'archive_interval_minutes':interval}
    settings(cfg)
    set_kv('backend_config',json.dumps(cfg,ensure_ascii=False))
    store.append_event('risk_config',{'risk_budget':cfg['wheel_risk_budget'],'archive_interval_minutes':interval})
    return {'ok':True}


class ExecutionIn(BaseModel):
    id:str|None=None
    expected_revision:int|None=Field(None,ge=1)
    symbol:str=Field(min_length=1)
    contract_code:str=Field(min_length=1)
    side:str
    status:str
    requested_qty:int=Field(gt=0)
    filled_qty:int=Field(0,ge=0)
    contract_size:int=Field(100,gt=0)
    reference_price:float|None=Field(None,ge=0)
    limit_price:float|None=Field(None,ge=0)
    fill_price:float|None=Field(None,ge=0)
    submitted_at:str
    filled_at:str|None=None
    mark_after:float|None=Field(None,ge=0)
    mark_after_at:str|None=None
    research_event_id:str|None=None
    execution_id:str|None=None


@router.post('/executions')
def execution(body:ExecutionIn):
    from app.core.wheel_research_analytics import execution_metrics
    try:payload=execution_metrics(body.model_dump(exclude={"expected_revision"}))
    except (ValueError,TypeError) as e:raise HTTPException(400,str(e))
    if body.research_event_id and not store.get_event(body.research_event_id):raise HTTPException(400,'关联研究归档不存在')
    identifier=body.id or str(uuid.uuid4());payload['id']=identifier
    conn=get_db()
    try:
        store.ensure_tables(conn)
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT payload FROM wheel_execution_observations WHERE id=?',(identifier,)).fetchone()
        old = json.loads(existing['payload']) if existing else None
        if old:
            comparable = {k:v for k,v in old.items() if k != 'revision'}
            if store.encode(comparable) == store.encode(payload):
                return old
            if body.expected_revision != old.get('revision', 1):
                raise HTTPException(409,'记录已更新，请刷新后再修改')
            if (old['symbol'], old['contract_code'], old['side']) != (body.symbol, body.contract_code, body.side):
                raise HTTPException(400,'更新不能改变标的、合约或方向')
        elif body.expected_revision is not None:
            raise HTTPException(409,'待更新记录不存在')
        payload['revision'] = (old.get('revision', 1) + 1) if old else 1
        if body.execution_id and not conn.execute('SELECT id FROM wheel_executions WHERE id=?',(body.execution_id,)).fetchone():raise HTTPException(400,'关联台账执行不存在')
        conn.execute('INSERT INTO wheel_execution_observations VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(identifier,store.encode(payload),datetime.now(timezone.utc).isoformat()))
        store.insert_event(conn, 'execution', payload, body.symbol)
        conn.commit()
    finally:conn.close()
    return payload


@router.post('/capture')
def capture():
    from app.services.wheel_research_capture import start_capture
    try:return start_capture()
    except ValueError as e:raise HTTPException(409,str(e))


@router.get('/capture/status')
def capture_state():
    from app.services.wheel_research_capture import capture_status
    return capture_status()
