"""Sim Wheel API — 纸面账独立路由。不碰 /api/wheel 实盘台账。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.sim_wheel import SimWheelEngine, get_sim_cfg, sim_on_alert
from app.data import sim_repository as repo

router = APIRouter()


class TickBody(BaseModel):
    spots: Dict[str, float] = Field(default_factory=dict)
    marks: Optional[Dict[str, float]] = None
    as_of: Optional[str] = None  # YYYY-MM-DD


class AlertBody(BaseModel):
    """调试/手工注入信号(与 TG 指纹同路径)。"""
    symbol: str
    signal_level: Optional[str] = None
    kind: Optional[str] = None
    side: Optional[str] = None
    category: Optional[str] = None
    timeframe: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    dte: Optional[int] = None
    premium: Optional[float] = None
    bid: Optional[float] = None
    trigger_price: Optional[float] = None
    underlying_price: Optional[float] = None
    spot: Optional[float] = None
    price: Optional[float] = None
    floor_price: Optional[float] = None
    contract_code: Optional[str] = None
    ema_type: Optional[str] = None
    fingerprint: Optional[str] = None
    ts: Optional[str] = None


def _cfg() -> Dict[str, Any]:
    try:
        from app.core.config import get_effective_config
        return get_effective_config()
    except Exception:
        return {}


@router.get("/cycles")
def get_sim_cycles(status: Optional[str] = None, strategy: Optional[str] = None, symbol: Optional[str] = None):
    """纸面周期列表。明确非实盘。"""
    items = repo.list_cycles(status=status, strategy=strategy, symbol=symbol, include_closed=True)
    return {
        "items": items,
        "count": len(items),
        "label": "纸面/非实盘",
        "paper_only": True,
    }


@router.get("/stats")
def get_sim_stats(strategy: Optional[str] = None, symbol: Optional[str] = None):
    """策略×标的熟悉度统计。"""
    items = repo.list_stats(strategy=strategy, symbol=symbol)
    return {
        "items": items,
        "count": len(items),
        "label": "纸面/非实盘",
        "paper_only": True,
        "open_cycles": repo.count_open(),
    }


@router.post("/tick")
def post_sim_tick(body: TickBody):
    """行情推进:止盈/指派/强挂 CC/到期。"""
    cfg = _cfg()
    eng = SimWheelEngine(repo, cfg)
    as_of = None
    if body.as_of:
        try:
            as_of = date.fromisoformat(body.as_of[:10])
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"as_of 无效: {e}")
    try:
        out = eng.tick(body.spots or {}, marks=body.marks, as_of=as_of)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    out["label"] = "纸面/非实盘"
    return out


@router.post("/alert")
def post_sim_alert(body: AlertBody):
    """手工/调试注入 alert(同 sim_on_alert)。"""
    alert = body.model_dump(exclude_none=True)
    out = sim_on_alert(alert, cfg=_cfg())
    out["label"] = "纸面/非实盘"
    return out


@router.get("/events")
def get_sim_events(limit: int = 50, symbol: Optional[str] = None):
    return {"items": repo.list_events(limit=limit, symbol=symbol), "label": "纸面/非实盘"}


@router.get("/config")
def get_sim_config():
    cfg = get_sim_cfg(_cfg())
    # 不回传任何密钥
    return {"sim_wheel": cfg, "label": "纸面/非实盘", "paper_only": True}
