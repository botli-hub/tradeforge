"""2032 Plan 持仓 REST API

GET  /api/plan2032/holdings   读取全部持仓(按 sort_order)
PUT  /api/plan2032/holdings   整表替换保存,body: { "holdings": [...] },返回保存后的列表
"""
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.data import plan2032_repository as repo

router = APIRouter()


class HoldingIn(BaseModel):
    symbol: str
    name: Optional[str] = None
    shares: float = 0
    target2032: float = 0
    dividend_yield: float = 0
    category: Optional[str] = None
    currency: Optional[str] = None
    pe: Optional[float] = None
    moat: Optional[str] = ""
    risk: Optional[int] = 3
    note: Optional[str] = ""
    sort_order: Optional[int] = None


class HoldingsBody(BaseModel):
    holdings: List[HoldingIn] = []


@router.get("/holdings")
def list_holdings():
    return repo.get_holdings()


@router.put("/holdings")
def save_holdings(body: HoldingsBody):
    return repo.replace_holdings([h.model_dump() for h in body.holdings])
