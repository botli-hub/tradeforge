"""台账权利金回填. 独立路由,避免改 88KB wheel.py.

只读/写本地 wheel_trades → cycle.open_price. 不拉富途、不自动下单.
"""
from fastapi import APIRouter, HTTPException

from app.core.premium_ledger import backfill_cycle_premium, resolve_premium

router = APIRouter()


@router.post("/cycles/{cycle_id}/premium-backfill")
def post_premium_backfill(cycle_id: str):
    try:
        out = backfill_cycle_premium(cycle_id, persist=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


@router.get("/cycles/{cycle_id}/premium")
def get_premium_status(cycle_id: str):
    try:
        from app.data.wheel_repository import get_cycle, get_trades
        cycle = get_cycle(cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail="周期不存在")
        trades = get_trades(cycle_id=cycle_id, limit=200)
        item = {
            "cycle_id": cycle_id,
            "side": cycle.get("open_option_type"),
            "ledger_trades": trades,
            "open_price": cycle.get("open_price"),
        }
        prem = resolve_premium(item)
        prem["cycle_open_price"] = cycle.get("open_price")
        prem["cycle_id"] = cycle_id
        return prem
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
