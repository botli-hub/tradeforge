"""历史K线管理API"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.data.history_backfill import backfill_kline_range, get_history_jobs, resolve_history_source
from app.data.history_repository import (
    get_kline_coverage,
    list_scheduler_runs,
    list_subscriptions,
    set_subscription_enabled,
    upsert_subscription,
)
from app.data.history_scheduler import get_history_scheduler

router = APIRouter()


class BackfillRequest(BaseModel):
    symbol: str
    timeframe: str = '1d'
    start_date: str
    end_date: str
    host: str = '127.0.0.1'
    port: int = 11111
    source: Optional[str] = None


class SubscriptionRequest(BaseModel):
    symbol: str
    name: Optional[str] = None
    source_hint: Optional[str] = None
    enabled: bool = True


@router.get('/coverage')
async def history_coverage(
    symbol: str = Query(...),
    timeframe: str = Query('1d'),
    source: Optional[str] = Query(None),
):
    return get_kline_coverage(symbol.upper(), timeframe, source)


@router.get('/jobs')
async def history_jobs(limit: int = Query(50, ge=1, le=200)):
    return get_history_jobs(limit=limit)


@router.get('/subscriptions')
async def history_subscriptions(enabled_only: bool = Query(False)):
    return list_subscriptions(enabled_only=enabled_only)


@router.post('/subscriptions')
async def add_history_subscription(req: SubscriptionRequest):
    upsert_subscription(req.symbol.upper(), name=req.name, source_hint=req.source_hint, enabled=req.enabled)
    return {'status': 'ok', 'symbol': req.symbol.upper()}


@router.post('/subscriptions/{symbol}/enable')
async def enable_history_subscription(symbol: str, enabled: bool = Query(True)):
    set_subscription_enabled(symbol.upper(), enabled)
    return {'status': 'ok', 'symbol': symbol.upper(), 'enabled': enabled}


@router.post('/backfill')
async def history_backfill(req: BackfillRequest):
    try:
        return backfill_kline_range(
            symbol=req.symbol.upper(),
            timeframe=req.timeframe,
            start_date=req.start_date,
            end_date=req.end_date,
            host=req.host,
            port=req.port,
            source=req.source,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/preview-source')
async def history_preview_source(
    symbol: str = Query(...),
    adapter: Optional[str] = Query(None),
):
    return {
        'symbol': symbol.upper(),
        'source': resolve_history_source(symbol.upper(), adapter),
    }


@router.get('/scheduler/status')
async def history_scheduler_status():
    scheduler = get_history_scheduler()
    return scheduler.status()


@router.post('/scheduler/run')
async def history_scheduler_run(
    host: str = Query('127.0.0.1'),
    port: int = Query(11111),
):
    scheduler = get_history_scheduler()
    return scheduler.run_once(trigger_type='manual', host=host, port=port)


@router.get('/scheduler/runs')
async def history_scheduler_runs(limit: int = Query(20, ge=1, le=100)):
    return list_scheduler_runs(limit=limit)
