"""标的愿卖价:CC strike 锚. 独立路由,避免改 88KB wheel.py."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.wheel_call_timing import (
    ensure_sell_above_column,
    get_target_sell_above,
    set_target_sell_above,
)

router = APIRouter()


class SellAboveIn(BaseModel):
    sell_above: Optional[float] = None


@router.patch("/targets/{symbol}/sell-above")
def patch_sell_above(symbol: str, body: SellAboveIn):
    ensure_sell_above_column()
    try:
        v = set_target_sell_above(symbol, body.sell_above)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"symbol": symbol, "sell_above": v, "current": get_target_sell_above(symbol)}
