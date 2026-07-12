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


def get_last_result() -> Optional[Dict[str, Any]]:
    return _LAST_RESULT


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

        opportunities: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        errors: List[Dict[str, str]] = []

        from app.api.wheel import _suggest  # 延迟导入避免循环依赖

        for idx, t in enumerate(targets):
            symbol = t["symbol"]
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
                    opportunities.append({
                        "symbol": symbol, "name": t.get("name"), "side": side,
                        "cycle_id": cycle_id,
                        "spot_price": res.get("spot_price"),
                        "trend": trend,
                        "iv_rank": (res.get("volatility") or {}).get("iv_rank"),
                        "earnings_warn": res.get("earnings_warn"),
                        "exceeds_capital": bool(headroom is not None and side == "PUT"
                                                and collateral > headroom),
                        **s,
                    })
            if idx < len(targets) - 1 and interval > 0:
                time.sleep(interval)  # 限频缓冲(缓存命中时基本不产生请求)

        opportunities.sort(key=lambda x: x.get("score") or 0, reverse=True)
        result = {
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "targets_scanned": len(targets),
            "opportunities": opportunities[:top_overall],
            "total_found": len(opportunities),
            "skipped": skipped,
            "errors": errors,
        }
        _LAST_RESULT = result
        return result
    finally:
        _SCAN_LOCK.release()


# ── Telegram 推送 ─────────────────────────────────────────────────────────────

def _opp_efficiency(o: Dict[str, Any]) -> float:
    """资金效率:年化×流动性近似 / 占用(万美元) × 风险折扣"""
    ann = float(o.get("annualized") or 0)
    factors = o.get("score_factors") or {}
    liq = float(factors.get("liquidity") or 1)
    strike = float(o.get("strike") or 0)
    size = float(o.get("contract_size") or 100)
    cap = max(strike * size, 1)
    mult = 1.0
    if o.get("exceeds_capital"):
        mult *= 0.2
    if o.get("trend") == "DOWN":
        mult *= 0.7
    if o.get("covers_earnings"):
        mult *= 0.85
    ivr = o.get("iv_rank")
    if ivr is not None and ivr >= 70:
        mult *= 1.15
    elif ivr is not None and ivr >= 50:
        mult *= 1.08
    return (ann * liq / (cap / 10000.0)) * mult


def format_scan_report(result: Dict[str, Any], limit: int = 3) -> str:
    lines = [f"🎯 Wheel 可下单 Top{limit}(按资金效率 · {result['scanned_at'][:16]})"]
    opps = list(result.get("opportunities") or [])
    # 过滤明显不可做
    opps = [o for o in opps if not o.get("exceeds_capital")]
    opps.sort(key=_opp_efficiency, reverse=True)
    if not opps:
        lines.append("没有满足条件的机会")
    for o in opps[:limit]:
        icon = "🟢" if o["side"] == "PUT" else "🔵"
        tags = []
        if o.get("trend") == "DOWN":
            tags.append("⚠趋势弱")
        if o.get("covers_earnings"):
            tags.append("⚠财报")
        if (o.get("iv_rank") or 0) >= 70:
            tags.append("IV高")
        tag_s = (" " + " ".join(tags)) if tags else ""
        eff = _opp_efficiency(o)
        cap = (o.get("strike") or 0) * (o.get("contract_size") or 100)
        lines.append(
            f"{icon} {o['symbol']} {'卖Put' if o['side'] == 'PUT' else '卖Call'} "
            f"{str(o.get('expiry') or '')[:10]} {o['strike']:g} · Δ{o['delta']:.2f} · {o['dte']}天\n"
            f"   权利金 {o['bid']:g} · 年化 {o['annualized']:.1f}% · 评分 {o.get('score', 0):.1f}"
            f" · 占用 ${cap:.0f} · 效率 {eff:.1f}{tag_s}"
        )
    errs = result.get("errors") or []
    if errs:
        lines.append(f"({len(errs)} 个标的获取失败)")
    return "\n".join(lines)


def push_scan(host: str = "127.0.0.1", port: int = 11111,
              force_refresh: bool = False) -> Dict[str, Any]:
    from app.api.leaps import _load_config
    from app.core.wheel_score import get_scan_cfg
    from app.services.notifier import TelegramNotifier

    result = run_scan(host, port, force_refresh=force_refresh)
    cfg = _load_config()
    top_n = int(get_scan_cfg(cfg).get("telegram_top_n", 3) or 3)
    notifier = TelegramNotifier.from_config(cfg)
    sent = False
    if notifier._enabled:
        sent = notifier.send(format_scan_report(result, limit=top_n))
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
