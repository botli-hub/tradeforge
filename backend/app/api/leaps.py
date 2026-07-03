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
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).parent.parent.parent.parent / "leaps_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("加载 leaps_config.yaml 失败: %s", e)
        _config_cache = {}
    return _config_cache


# ── 监控白名单 ────────────────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist():
    return repo.get_watchlist()


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
def get_signals(symbol: Optional[str] = None, limit: int = 50):
    return repo.get_recent_signals(symbol=symbol, limit=limit)


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
    ok = notifier.send(text)
    return {"sent": ok, "message": text}


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
