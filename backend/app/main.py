"""TradeForge API 入口"""
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import strategies, backtest, market, formula, trading, options, history, runtime, stocks
from app.data.database import init_db
from app.data.history_scheduler import get_history_scheduler

app = FastAPI(title="TradeForge API", version="1.0.0")

# CORS — 仅允许本地前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据库
@app.on_event("startup")
async def startup():
    init_db()
    scheduler = get_history_scheduler()
    scheduler.start()
    # 启动时在后台检查数据缺失并补数（若今天尚未跑过调度任务）
    threading.Thread(target=_startup_backfill_check, args=(scheduler,), daemon=True).start()


def _startup_backfill_check(scheduler):
    import time
    from app.data.history_repository import has_successful_scheduler_run
    from datetime import datetime
    time.sleep(3)  # 等待 uvicorn 完全就绪
    today = datetime.now().date().isoformat()
    if has_successful_scheduler_run(today):
        return  # 今天已成功跑过，跳过
    # 今天还没有成功的调度记录，触发启动补数（覆盖股票池所有启用股票的近期数据）
    try:
        scheduler.run_once(trigger_type='startup')
    except Exception:
        pass


@app.on_event("shutdown")
async def shutdown():
    get_history_scheduler().stop()

# 注册路由
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(formula.router, prefix="/api/formula", tags=["formula"])
app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
app.include_router(options.router, prefix="/api/options", tags=["options"])
app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(runtime.router, prefix="/api/runtime", tags=["runtime"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])

@app.get("/health")
async def health():
    return {"status": "ok"}
