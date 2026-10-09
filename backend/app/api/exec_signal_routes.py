"""席位可执行卖期权信号: pending pull + ACK。无 webhook / 无 FirstTrade。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.data import exec_signal_repository as repo

router = APIRouter()


def _parse_symbols(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None or str(raw).strip() == "":
        return None  # 默认 TSLL,SPCH
    parts = [p.strip().upper() for p in str(raw).split(",") if p.strip()]
    return parts or None


class AckBody(BaseModel):
    consumer: str = Field(..., min_length=1)
    status: str = Field(..., description="consumed|ignored")
    note: Optional[str] = None


@router.get("/exec-signals/pending")
def get_pending_exec_signals(
    symbols: Optional[str] = Query(
        None,
        description="逗号分隔,默认 TSLL,SPCH;仅允许这两票",
    ),
    side: Optional[str] = Query(None, description="Put|Call|PUT|CALL"),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """拉取未终态 ACK(consumed/ignored)的可执行卖期权信号。"""
    syms = _parse_symbols(symbols)
    if side is not None and side.strip() and repo.normalize_side(side) is None:
        raise HTTPException(status_code=400, detail="side 须为 Put|Call")
    items = repo.list_pending(symbols=syms, side=side, limit=limit)
    return {
        "items": items,
        "count": len(items),
        "symbols": sorted(syms) if syms is not None else sorted(repo.ALLOWED_SYMBOLS),
        "filter_side": repo.normalize_side(side) if side else None,
    }


@router.post("/exec-signals/{signal_id}/ack")
def ack_exec_signal(signal_id: str, body: AckBody) -> Dict[str, Any]:
    """ACK 消费。重复相同终态幂等成功;已 consumed 再 ack 亦幂等。"""
    try:
        return repo.ack_signal(
            signal_id,
            consumer=body.consumer,
            status=body.status,
            note=body.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="signal 不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
