"""TradeForge API 入口"""
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import strategies, backtest, market, formula, trading, options, history, runtime, stocks, leaps, wheel, settings_api, plan2032
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
    # Wheel 开仓时机后台扫描
    threading.Thread(target=_wheel_timing_loop, daemon=True).start()
    # 每日 IV 快照(加速 IV Rank 历史积累)
    threading.Thread(target=_iv_snapshot_loop, daemon=True).start()
    # 每周一 Telegram 周报
    threading.Thread(target=_weekly_report_loop, daemon=True).start()
    # Wheel 全池扫描定时推送(wheel_scan.auto_push_minutes,0=关闭)
    from app.services.wheel_scanner import auto_push_loop
    threading.Thread(target=auto_push_loop, daemon=True).start()
    # 存量卖出交易的合约代码补全(幂等,美股无需 OpenD)
    threading.Thread(target=_backfill_codes_once, daemon=True).start()


def _backfill_codes_once():
    import time
    time.sleep(5)
    try:
        from app.api.wheel import backfill_missing_contract_codes, ensure_target_subscriptions
        n = ensure_target_subscriptions()
        if n:
            import logging
            logging.getLogger(__name__).info("wheel 标的补订阅历史日K: %d 个", n)
        r = backfill_missing_contract_codes()
        if r["updated"] or r["failed"]:
            import logging
            logging.getLogger(__name__).info("存量合约代码补全: %s", r)
    except Exception:
        pass


def _iv_snapshot_loop():
    """每天为所有启用的 wheel 标的存一次 ATM IV 快照(缺今天的才补)"""
    import time
    from datetime import date
    time.sleep(180)  # 启动 3 分钟后开始(错开启动补数即可),尽快积累 IV 档案
    while True:
        try:
            from app.api.leaps import _load_config
            from app.api.options import _load_option_expirations, _load_option_chain
            from app.core.volatility import build_profile
            from app.data import wheel_repository as wrepo
            from app.data.database import get_db

            cfg = _load_config().get("futu", {}) or {}
            host, port = cfg.get("host", "127.0.0.1"), cfg.get("port", 11111)
            today = date.today().isoformat()
            conn = get_db()
            try:
                have = {r["symbol"] for r in conn.execute(
                    "SELECT symbol FROM underlying_iv_history WHERE date = ?", (today,)).fetchall()}
            finally:
                conn.close()
            for t in wrepo.get_targets():
                if not t.get("enabled") or t["symbol"] in have:
                    continue
                try:
                    exps = _load_option_expirations(t["symbol"], host, port)
                    exp = next((e for e in exps
                                if (date.fromisoformat(e[:10]) - date.today()).days >= 20), None)
                    if not exp:
                        continue
                    chain = _load_option_chain(t["symbol"], exp, host, port)
                    build_profile(t["symbol"], chain["spot_price"], chain_contracts=chain["contracts"])
                except Exception:
                    pass
                time.sleep(8)  # 限频间隔
        except Exception:
            pass
        time.sleep(6 * 3600)  # 每 6 小时检查一次是否缺今天的快照


def _weekly_report_loop():
    """每周一发送 Telegram 周报(权利金/年化/空转/待处理)"""
    import time
    from datetime import datetime, timedelta
    time.sleep(120)
    while True:
        try:
            from app.api.leaps import _load_config
            from app.data import wheel_repository as wrepo
            from app.services.notifier import TelegramNotifier

            cfg = _load_config()
            enabled = (cfg.get("wheel_position", {}) or {}).get("weekly_report", True)
            now = datetime.now()
            week_key = now.strftime("%G-W%V")
            if enabled and now.weekday() == 0 and wrepo.get_kv("weekly_report_sent") != week_key:
                stats = wrepo.get_stats()
                week_ago = (now - timedelta(days=7)).isoformat()
                trades = [t for t in wrepo.get_trades(limit=500) if t["traded_at"] >= week_ago]
                premium_week = sum(
                    (t["qty"] * t["price"] * t["contract_size"] - t["fee"]) if t["trade_type"] in ("SELL_PUT", "SELL_CALL")
                    else -(t["qty"] * t["price"] * t["contract_size"] + t["fee"]) if t["trade_type"] in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE")
                    else 0 for t in trades)
                cap = stats.get("capital", {})
                lines = [
                    "📊 Wheel 周报",
                    f"本周净权利金 ${premium_week:.0f} · 本月 ${stats['premium_month']:.0f} · 累计 ${stats['premium_total']:.0f}",
                    f"活跃轮子 {stats['active_cycles']} · 已完成 {stats['closed_cycles']} · 已实现盈亏 ${stats['realized_pnl_total']:.0f}",
                    f"担保占用 ${cap.get('csp_collateral', 0):.0f} + 持股 ${cap.get('holding_cost', 0):.0f} = 总占用 ${cap.get('total_committed', 0):.0f}",
                ]
                if stats.get("expiring_soon"):
                    lines.append("⚠ 临期: " + "、".join(
                        f"{e['symbol']} {e['open_option_type']} {e['dte']}天" for e in stats["expiring_soon"]))
                conv = stats.get("conversion") or {}
                if conv.get("signal_count_30d"):
                    lines.append(
                        f"触线转化(30d) {conv.get('converted_30d', 0)}/{conv['signal_count_30d']}"
                        f"({conv.get('rate_pct', 0)}%)"
                        + (f" · 均延迟 {conv['avg_signal_to_trade_hours']}h"
                           if conv.get("avg_signal_to_trade_hours") is not None else ""))
                notifier = TelegramNotifier.from_config(cfg)
                if notifier.send("\n".join(lines)):
                    wrepo.set_kv("weekly_report_sent", week_key)
        except Exception:
            pass
        time.sleep(3600)


def _wheel_timing_loop():
    """按设置页保存的 wheel_timing.auto_scan_minutes(存本地数据库)周期扫描开仓时机。
    启动后先等满一个间隔再首跑,避免与手动扫描/前端请求争抢富途限频额度。"""
    import time
    from app.api.leaps import _load_config, _run_wheel_scan
    while True:
        try:
            cfg = _load_config()
            minutes = (cfg.get("wheel_timing", {}) or {}).get("auto_scan_minutes", 30)
        except Exception:
            minutes = 30
        if not minutes or minutes <= 0:
            time.sleep(300)
            continue
        time.sleep(minutes * 60)
        try:
            _run_wheel_scan()
        except Exception:
            pass  # OpenD 未启动等情况静默跳过


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
app.include_router(leaps.router, prefix="/api/leaps", tags=["leaps"])
app.include_router(wheel.router, prefix="/api/wheel", tags=["wheel"])
app.include_router(plan2032.router, prefix="/api/plan2032", tags=["plan2032"])
app.include_router(settings_api.router, prefix="/api/config", tags=["config"])

@app.get("/health")
async def health():
    return {"status": "ok"}
