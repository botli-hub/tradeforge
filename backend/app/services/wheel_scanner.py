"""Wheel 全池扫描器

- 遍历所有启用的 wheel 标的,按周期状态自动决定扫 PUT(空仓/有资金余量)还是 CALL(持股)
- 复用 _suggest 的筛选 + 综合打分,跨标的统一排序,输出全池 Top 机会
- 期权链/到期日带 TTL 缓存,重复扫描不重复打 Futu(限频友好)
- 支持 Telegram 推送(手动触发 + main.py 定时循环)
"""
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 期权链 TTL 缓存 ────────────────────────────────────────────────────────────

_CACHE_LOCK = threading.Lock()
_EXP_CACHE: Dict[str, Tuple[float, List[str]]] = {}            # symbol -> (ts, expirations)
_CHAIN_CACHE: Dict[Tuple[str, str], Tuple[float, Dict]] = {}   # (symbol, expiry) -> (ts, chain)


def _ttl() -> float:
    from app.core.wheel_score import get_scan_cfg
    from app.api.leaps import _load_config
    return float(get_scan_cfg(_load_config()).get("chain_cache_ttl_sec", 900))


def cached_expirations(symbol: str, host: str, port: int, force: bool = False) -> List[str]:
    ttl = _ttl()
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _EXP_CACHE.get(symbol)
        if hit and not force and now - hit[0] < ttl:
            return hit[1]
    from app.api.options import _load_option_expirations
    data = _load_option_expirations(symbol, host, port)
    with _CACHE_LOCK:
        _EXP_CACHE[symbol] = (now, data)
    return data


def cached_chain(symbol: str, expiry: str, host: str, port: int, force: bool = False) -> Dict[str, Any]:
    ttl = _ttl()
    now = time.monotonic()
    key = (symbol, expiry)
    with _CACHE_LOCK:
        hit = _CHAIN_CACHE.get(key)
        if hit and not force and now - hit[0] < ttl:
            return hit[1]
    from app.api.options import _load_option_chain
    data = _load_option_chain(symbol, expiry, host, port)
    with _CACHE_LOCK:
        _CHAIN_CACHE[key] = (now, data)
    return data


def clear_cache():
    with _CACHE_LOCK:
        _EXP_CACHE.clear()
        _CHAIN_CACHE.clear()


# ── 全池扫描 ──────────────────────────────────────────────────────────────────

_LAST_RESULT: Optional[Dict[str, Any]] = None
_SCAN_LOCK = threading.Lock()
_PROGRESS_LOCK = threading.Lock()
_SCAN_PROGRESS: Dict[str, Any] = {
    "running": False,
    "phase": "idle",  # idle | pool | done | error
    "symbol": None,
    "side": None,
    "expiry": None,
    "contract_i": 0,
    "contract_n": 0,
    "target_i": 0,
    "target_n": 0,
    "message": "",
    "updated_at": None,
}


def get_last_result() -> Optional[Dict[str, Any]]:
    return _LAST_RESULT


def get_scan_progress() -> Dict[str, Any]:
    with _PROGRESS_LOCK:
        return dict(_SCAN_PROGRESS)


def update_scan_progress(**kwargs: Any) -> None:
    """线程安全更新扫描进度(供 _suggest / run_scan 回调)。"""
    with _PROGRESS_LOCK:
        _SCAN_PROGRESS.update(kwargs)
        _SCAN_PROGRESS["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _reset_progress(target_n: int = 0) -> None:
    update_scan_progress(
        running=True,
        phase="pool",
        symbol=None,
        side=None,
        expiry=None,
        contract_i=0,
        contract_n=0,
        target_i=0,
        target_n=target_n,
        message="准备扫描…",
    )


def run_scan(host: str = "127.0.0.1", port: int = 11111,
             force_refresh: bool = False) -> Dict[str, Any]:
    """扫描全部启用标的,返回跨标的排序的 Top 机会"""
    global _LAST_RESULT
    from fastapi import HTTPException
    from app.api.leaps import _load_config
    from app.core.wheel_score import get_scan_cfg
    from app.data import wheel_repository as repo

    if not _SCAN_LOCK.acquire(blocking=False):
        raise RuntimeError("扫描已在进行中,请稍候")
    try:
        cfg = _load_config()
        scan_cfg = get_scan_cfg(cfg)
        top_per_symbol = int(scan_cfg.get("top_per_symbol", 3))
        top_overall = int(scan_cfg.get("top_overall", 20))
        interval = float(scan_cfg.get("symbol_interval_sec", 2))

        if force_refresh:
            clear_cache()

        usage = repo.get_capital_usage()["per_symbol"]
        targets = [t for t in repo.get_targets() if t.get("enabled")]
        _reset_progress(len(targets))

        opportunities: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        errors: List[Dict[str, str]] = []

        from app.api.wheel import _suggest  # 延迟导入避免循环依赖

        for idx, t in enumerate(targets):
            symbol = t["symbol"]
            update_scan_progress(
                target_i=idx + 1,
                target_n=len(targets),
                symbol=symbol,
                side=None,
                expiry=None,
                contract_i=0,
                contract_n=0,
                message=f"标的 {idx + 1}/{len(targets)} · {symbol}",
            )
            u = usage.get(symbol, {})
            committed = (u.get("csp_collateral") or 0) + (u.get("holding_cost") or 0)
            headroom = (t["max_capital"] - committed) if (t.get("max_capital") or 0) > 0 else None

            cycles = repo.get_active_cycles(symbol)
            holding = [c for c in cycles if c["status"] == "HOLDING"]

            sides: List[Tuple[str, Optional[str]]] = []  # (side, cycle_id)
            if holding:
                sides.append(("CALL", holding[0]["id"]))
            if headroom is None or headroom > 0:
                sides.append(("PUT", None))
            else:
                skipped.append({"symbol": symbol, "reason": f"资金上限已用满(占用 {committed:.0f}/{t['max_capital']:.0f})"})

            for side, cycle_id in sides:
                update_scan_progress(
                    symbol=symbol,
                    side=side,
                    message=f"正在扫描 {symbol} {side} · 标的 {idx + 1}/{len(targets)}",
                )
                try:
                    res = _suggest(symbol, side, host, port, cycle_id)
                except HTTPException as e:
                    errors.append({"symbol": symbol, "side": side, "error": str(e.detail)})
                    continue
                except Exception as e:
                    errors.append({"symbol": symbol, "side": side, "error": str(e)})
                    continue
                trend = (res.get("trend") or {}).get("trend")
                for s in res.get("suggestions", [])[:top_per_symbol]:
                    collateral = (s.get("strike") or 0) * (s.get("contract_size") or 100)
                    exceeds = bool(headroom is not None and side == "PUT" and collateral > headroom)
                    # 有余量时保留 score;超资金上限的机会降权到排序末尾但仍展示
                    adj_score = s.get("score") or 0
                    if exceeds:
                        adj_score *= 0.3
                    elif headroom is not None and t.get("max_capital"):
                        # 余量越大略加分(与 score 内 headroom_factor 叠加,扫描层再偏资金效率)
                        hr = max(0.0, headroom / t["max_capital"])
                        adj_score *= (1.0 + 0.1 * hr)
                    opportunities.append({
                        "symbol": symbol, "name": t.get("name"), "side": side,
                        "cycle_id": cycle_id,
                        "spot_price": res.get("spot_price"),
                        "trend": trend,
                        "iv_rank": (res.get("volatility") or {}).get("iv_rank"),
                        "earnings_warn": res.get("earnings_warn"),
                        "exceeds_capital": exceeds,
                        "headroom": headroom,
                        "score_adjusted": round(adj_score, 2),
                        **s,
                        "score": round(adj_score, 2),
                    })
            if idx < len(targets) - 1 and interval > 0:
                time.sleep(interval)  # 限频缓冲(缓存命中时基本不产生请求)

        sort_mode = scan_cfg.get("sort_mode", "score")
        if sort_mode == "robust":
            opportunities.sort(
                key=lambda x: x.get("robust_score") or x.get("score") or 0, reverse=True
            )
        else:
            opportunities.sort(key=lambda x: x.get("score") or 0, reverse=True)
        result = {
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "targets_scanned": len(targets),
            "opportunities": opportunities[:top_overall],
            "total_found": len(opportunities),
            "skipped": skipped,
            "errors": errors,
            "sort_mode": sort_mode,
        }
        # 落库供归因
        if scan_cfg.get("log_suggestions", True):
            try:
                from app.core.wheel_attribution import log_suggestion_snapshot
                slim = {
                    "scanned_at": result["scanned_at"],
                    "opportunities": [
                        {
                            "symbol": o.get("symbol"), "side": o.get("side"),
                            "strike": o.get("strike"), "expiry": o.get("expiry"),
                            "score": o.get("score"), "annualized": o.get("annualized"),
                            "pop": o.get("pop"), "delta": o.get("delta"),
                        }
                        for o in result["opportunities"][:20]
                    ],
                }
                log_suggestion_snapshot(slim)
            except Exception as e:
                logger.warning("log suggestions 失败: %s", e)
        _LAST_RESULT = result
        update_scan_progress(
            running=False,
            phase="done",
            message=f"完成 · 找到 {len(opportunities)} 条机会",
            contract_i=0,
            contract_n=0,
        )
        return result
    except Exception as e:
        update_scan_progress(running=False, phase="error", message=f"扫描失败: {e}")
        raise
    finally:
        _SCAN_LOCK.release()
        # 若异常未写 done/error，标记 idle
        with _PROGRESS_LOCK:
            if _SCAN_PROGRESS.get("running"):
                _SCAN_PROGRESS["running"] = False
                if _SCAN_PROGRESS.get("phase") == "pool":
                    _SCAN_PROGRESS["phase"] = "idle"


# ── Telegram 推送 ─────────────────────────────────────────────────────────────

def format_scan_report(result: Dict[str, Any], limit: int = 8) -> str:
    lines = [f"🎯 Wheel 全池扫描 Top 机会({result['scanned_at'][:16]})"]
    opps = result.get("opportunities", [])
    if not opps:
        lines.append("没有满足条件的机会")
    for o in opps[:limit]:
        icon = "🟢" if o["side"] == "PUT" else "🔵"
        tags = []
        if o.get("trend") == "DOWN":
            tags.append("⚠趋势弱")
        if o.get("covers_earnings"):
            tags.append("⚠财报")
        if o.get("exceeds_capital"):
            tags.append("⚠超资金上限")
        tag_s = (" " + " ".join(tags)) if tags else ""
        lines.append(
            f"{icon} {o['symbol']} {'卖Put' if o['side'] == 'PUT' else '卖Call'} "
            f"{o['expiry'][:10]} {o['strike']:g} · Δ{o['delta']:.2f} · {o['dte']}天\n"
            f"   权利金 {o['bid']:g} · 年化 {o['annualized']:.1f}% · 评分 {o.get('score', 0):.1f}{tag_s}"
        )
    errs = result.get("errors") or []
    if errs:
        lines.append(f"({len(errs)} 个标的获取失败)")
    return "\n".join(lines)


def push_scan(host: str = "127.0.0.1", port: int = 11111,
              force_refresh: bool = False) -> Dict[str, Any]:
    from app.api.leaps import _load_config
    from app.services.notifier import TelegramNotifier

    result = run_scan(host, port, force_refresh=force_refresh)
    notifier = TelegramNotifier.from_config(_load_config())
    sent = False
    if notifier._enabled:
        sent = notifier.send(format_scan_report(result))
    result["telegram_sent"] = sent
    return result


def auto_push_loop():
    """按设置页 wheel_scan.auto_push_minutes 周期推送 Top 机会;0=关闭。
    启动后先等满一个间隔再首跑,避开限频高峰(与 wheel_timing 循环同策略)。"""
    from app.api.leaps import _load_config
    from app.core.wheel_score import get_scan_cfg
    while True:
        try:
            cfg = _load_config()
            scan_cfg = get_scan_cfg(cfg)
            minutes = float(scan_cfg.get("auto_push_minutes", 0) or 0)
        except Exception:
            minutes = 0
        if minutes <= 0:
            time.sleep(300)
            continue
        time.sleep(minutes * 60)
        try:
            futu_cfg = _load_config().get("futu", {}) or {}
            push_scan(futu_cfg.get("host", "127.0.0.1"), futu_cfg.get("port", 11111))
        except Exception as e:
            logger.warning("wheel 全池扫描定时推送失败: %s", e)
