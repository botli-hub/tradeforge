"""TradeForge API 入口"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import strategies, backtest, market, formula, trading, options, history, runtime
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
    get_history_scheduler().start()


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

@app.get("/health")
async def health():
    return {"status": "ok"}
