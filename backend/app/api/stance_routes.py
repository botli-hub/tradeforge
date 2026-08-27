"""标的立场:只收租 / 允许接货. 独立路由,避免改 88KB wheel.py."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.wheel_stance import normalize_stance, set_target_stance, ensure_stance_column, STANCE_ACQUIRE

router = APIRouter()


class StanceIn(BaseModel):
    stance: str


@router.patch("/targets/{symbol}/stance")
def patch_stance(symbol: str, body: StanceIn):
    ensure_stance_column()
    st = normalize_stance(body.stance)
    if st not in ("income", "acquire"):
        st = STANCE_ACQUIRE
    try:
        set_target_stance(symbol, st)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"symbol": symbol, "stance": st}
