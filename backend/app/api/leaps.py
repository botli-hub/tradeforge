"""LEAPS 信号监控 REST API"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.data import leaps_repository as repo
from app.services.notifier import TelegramNotifier, format_leaps_signal_from_dict

logger = logging.getLogger(__name__)
router = APIRouter()

_config_cache: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """全项目唯一配置入口:代码内默认值 ← 数据库覆盖(设置页保存)。
    不再读取任何 yaml/ini 配置文件 —— 所有可调参数、密钥、行情源地址均落库。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        from app.core.config import get_effective_config
        _config_cache = get_effective_config()
    except Exception as e:
        logger.warning("加载后端配置失败: %s", e)
        _config_cache = {}
    return _config_cache


# ── 监控白名单 ────────────────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist():
    return repo.get_watchlist()


@router.get("/watchlist/candidates")
def get_watchlist_candidates():
    """股票池中的美股/港股(启用状态),排除已在白名单中的,作为添加候选"""
    from app.data.database import get_db
    existing = {w["symbol"] for w in repo.get_watchlist()}
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT symbol, name, market FROM stocks
            WHERE market IN ('US', 'HK') AND enabled = 1
            ORDER BY market, symbol
            """
        ).fetchall()
        return [dict(r) for r in rows if r["symbol"] not in existing]
    finally:
        conn.close()


class WatchlistAdd(BaseModel):
    symbol: str
    name: Optional[str] = None
    floor_price: float
    enabled: bool = True


@router.post("/watchlist")
def add_watchlist(body: WatchlistAdd):
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    if body.floor_price <= 0:
        raise HTTPException(status_code=400, detail="接货底线价必须大于 0")
    name = (body.name or "").strip()
    if not name:
        # 尝试从股票池取名称
        from app.data.database import get_db
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT name FROM stocks WHERE symbol = ?", (symbol,)
            ).fetchone()
            name = row["name"] if row else symbol
        finally:
            conn.close()
    repo.upsert_watchlist_item(symbol, name, body.floor_price, body.enabled)
    return repo.get_watchlist_item(symbol)


@router.delete("/watchlist/{symbol}")
def delete_watchlist(symbol: str):
    if not repo.delete_watchlist_item(symbol):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在白名单中")
    return {"ok": True, "symbol": symbol}


class WatchlistUpdate(BaseModel):
    floor_price: Optional[float] = None
    enabled: Optional[bool] = None
    name: Optional[str] = None


@router.put("/watchlist/{symbol}")
def update_watchlist(symbol: str, body: WatchlistUpdate):
    item = repo.get_watchlist_item(symbol)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{symbol} 不在白名单中")
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    repo.update_watchlist_item(symbol, **kwargs)
    return repo.get_watchlist_item(symbol)


# ── 信号历史 ──────────────────────────────────────────────────────────────────

@router.get("/signals")
def get_signals(symbol: Optional[str] = None, limit: int = 50, levels: Optional[str] = None):
    """levels: 逗号分隔,如 WHEEL_PUT,WHEEL_CALL 或 PRIMARY,SECONDARY"""
    level_list = [x.strip() for x in levels.split(",") if x.strip()] if levels else None
    return repo.get_recent_signals(symbol=symbol, limit=limit, levels=level_list)


# ── Wheel 开仓时机扫描 ─────────────────────────────────────────────────────────

class WheelScanRequest(BaseModel):
    symbol: Optional[str] = None


# 触线扫描状态/进度:独立模块 wheel_timing_progress(避免循环导入丢进度)
from app.core import wheel_timing_progress as _timing_prog


def update_wheel_scan_progress(**kwargs: Any) -> None:
    """兼容旧调用名。"""
    _timing_prog.update(**kwargs)


@router.post("/wheel-scan")
def trigger_wheel_scan(body: WheelScanRequest, background_tasks: BackgroundTasks):
    """扫描 Wheel 开仓时机(卖Put/持股卖Call),结果入信号库并推 Telegram"""
    st = _timing_prog.get_state()
    if st.get("running"):
        return {"status": "already_running"}
    # 立刻置 running,避免前端首几次轮询仍见 idle
    _timing_prog.reset_for_start(telegram_configured=False)
    background_tasks.add_task(_run_wheel_scan, body.symbol)
    return {"status": "started", "symbol": body.symbol}


@router.get("/wheel-scan/status")
def wheel_scan_status():
    return _timing_prog.get_state()


@router.get("/wheel-timing/history")
def wheel_timing_history(page: int = 1, page_size: int = 20, symbol: Optional[str] = None):
    """历史时机(按合约代码去重合并,发现时间倒序,分页)"""
    return repo.get_timing_history(page=page, page_size=page_size, symbol=symbol)


def _run_wheel_scan(symbol: Optional[str] = None):
    from datetime import datetime
    from app.core.leaps_monitor import WheelTimingMonitor, format_wheel_signal, signal_strength
    cfg = _load_config()
    monitor = WheelTimingMonitor(cfg)
    notifier = TelegramNotifier.from_config(cfg)
    timing_cfg = cfg.get("wheel_timing", {}) or {}
    min_iv = float(timing_cfg.get("push_min_iv_rank", 50) or 0)
    strong_only = bool(timing_cfg.get("push_strong_only", True))
    _timing_prog.reset_for_start(telegram_configured=bool(notifier._enabled))
    _timing_prog.update(message="触线 · 初始化监控器…", phase="timing")
    report: list = []
    try:
        signals = monitor.scan_all(symbol=symbol, report=report)
        logger.info("wheel 时机扫描完成,触发 %d 条", len(signals))
        sent = 0
        for sig in signals:
            level = signal_strength(sig, min_iv)
            # 默认只推可做/强,避免观察级刷屏;push_strong_only=False 时全推
            if strong_only and level == "WATCH":
                continue
            try:
                if notifier.send(format_wheel_signal(sig, min_iv)):
                    sent += 1
            except Exception as e:
                logger.warning("wheel 信号推送失败: %s", e)
        _timing_prog.mark_done(signals_found=len(signals), telegram_sent=sent, report=report)

        # 在场合约体检推送 → 统一走 alert_engine(事件去重/静默/短模板)
        try:
            from app.api.wheel import check_open_positions_core
            from app.data import wheel_repository as wrepo
            from app.services.alert_engine import process_position_alerts, send_and_log

            futu_cfg = cfg.get("futu", {}) or {}
            check = check_open_positions_core(
                futu_cfg.get("host", "127.0.0.1"), futu_cfg.get("port", 11111),
            )
            process_position_alerts(check.get("items") or [], cfg=cfg, force=False)

            # 持股裸奔:HOLDING 且无在场 CC ≥3 天(独立指纹冷却)
            for cyc in wrepo.get_cycles(include_closed=False):
                if cyc["status"] != "HOLDING" or (cyc.get("uncovered_days") or 0) < 3:
                    continue
                key = f"UNCOV.{cyc['id']}"
                if repo.is_contract_in_cooldown(key):
                    continue
                cb = cyc.get("cost_basis")
                line = (
                    f"🪑 管仓|裸奔 {cyc['symbol']} 持股 {cyc['shares']:g} 股 "
                    f"已 {cyc['uncovered_days']} 天未挂 Call"
                    + (f"(CB ${cb:.2f})" if cb is not None else "")
                    + "\n→ Wheel · 找 Call"
                )
                if send_and_log(line, category="uncovered", fingerprint=key,
                                title=f"uncovered {cyc['symbol']}", cfg=cfg).get("sent"):
                    repo.set_contract_cooldown(key, cyc["symbol"], 1)
        except Exception as e:
            logger.info("在场合约体检跳过: %s", e)
    except Exception as e:
        logger.error("_run_wheel_scan 异常: %s", e)
        _timing_prog.mark_error(str(e))
        _timing_prog.update(report=report)
    finally:
        st = _timing_prog.get_state()
        if st.get("running"):
            # 异常路径未 mark_done/error 时兜底
            _timing_prog.update(
                running=False,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                report=report,
                phase=st.get("phase") if st.get("phase") in ("done", "error") else "idle",
            )


@router.get("/signals/{signal_id}/notify")
def resend_signal_notification(signal_id: str):
    """重新推送某条历史信号到 Telegram"""
    signals = repo.get_recent_signals(limit=200)
    sig = next((s for s in signals if s["id"] == signal_id), None)
    if sig is None:
        raise HTTPException(status_code=404, detail="信号不存在")
    cfg = _load_config()
    notifier = TelegramNotifier.from_config(cfg)
    text = format_leaps_signal_from_dict(sig)
    result = notifier.send_detailed(text)
    return {"sent": result["ok"], "reason": result["reason"], "message": text}


# ── 冷却状态 ──────────────────────────────────────────────────────────────────

@router.get("/cooldowns")
def get_cooldowns():
    return repo.get_all_cooldowns()


# ── 手动扫描 ──────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    symbol: Optional[str] = None
    is_intraday: bool = False


@router.post("/scan")
def trigger_scan(body: ScanRequest, background_tasks: BackgroundTasks):
    """触发后台扫描，立即返回。结果通过 Telegram 推送并写入信号库。"""
    background_tasks.add_task(_run_scan, body.symbol, body.is_intraday)
    return {"status": "started", "symbol": body.symbol, "is_intraday": body.is_intraday}


def _run_scan(symbol: Optional[str], is_intraday: bool):
    from app.core.leaps_monitor import LeapsMonitor
    cfg = _load_config()
    monitor = LeapsMonitor(cfg)
    notifier = TelegramNotifier.from_config(cfg)

    try:
        if symbol:
            item = repo.get_watchlist_item(symbol)
            if not item:
                logger.warning("scan: %s 不在白名单", symbol)
                return
            signals = monitor.scan_symbol(symbol, item["floor_price"], is_intraday=is_intraday)
        else:
            signals = monitor.scan_all(is_intraday=is_intraday)

        logger.info("扫描完成，触发信号 %d 条", len(signals))
        for sig in signals:
            notifier.send_signal(sig)

    except Exception as e:
        logger.error("_run_scan 异常: %s", e)


# ── 状态概览 ──────────────────────────────────────────────────────────────────

@router.get("/status")
def get_status():
    watchlist = repo.get_watchlist()
    enabled = [w for w in watchlist if w.get("enabled")]
    recent = repo.get_recent_signals(limit=5)
    cooldowns = repo.get_all_cooldowns()
    return {
        "watchlist_total": len(watchlist),
        "watchlist_enabled": len(enabled),
        "recent_signals": recent,
        "active_cooldowns": len(cooldowns),
    }
