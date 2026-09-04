"""缠论 5m/30m/1d 买卖点 Telegram 增量推送.

扫 5m / 30m / 日线(1d);忽略 1m/1h。标的默认启用 Wheel 池。
日线默认 ~每美股会话扫一次(poll_minutes_1d≈390),不几分钟刷屏。
走现有 alerts 管道:fingerprint 去重、quiet_hours、wheel_push_log。
不自动下单,不碰 POSITION_QUANT / wheel_reconcile。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from app.core.chan_engine import TF_LABEL
from app.services.alert_engine import (
    get_alert_cfg,
    in_quiet_hours,
    log_push,
    mark_sent,
    send_and_log,
)

logger = logging.getLogger(__name__)

ALLOWED_TIMEFRAMES = ("5m", "30m", "1d")
_ALLOWED = frozenset(ALLOWED_TIMEFRAMES)
_FALLBACK_SYMBOLS = ["AAPL", "ARM", "SPCX", "TSLA"]
_LAST_RUN_KEY = "chan_alert_last_runs"
_LIMIT_DAYS = {"5m": 30, "30m": 120, "1d": 400}
# 日线默认约一个美股 RTH(~6.5h),配合 session_only ≈ 每日一次
_DEFAULT_POLL = {"5m": 5, "30m": 30, "1d": 390}

DEFAULT_CHAN_ALERTS: Dict[str, Any] = {
    "enabled": True,
    "timeframes": ["5m", "30m", "1d"],
    "symbols": [],  # 空 = 启用 Wheel 标的
    "poll_minutes_5m": 5,
    "poll_minutes_30m": 30,
    "poll_minutes_1d": 390,  # ~美股 RTH 一次; 非几分钟刷屏
    "session_only": True,  # 仅美股 RTH
    "bar_limit": 400,
    "recent_bars": 3,  # 只推近 N 根对应时长内的增量
}


def get_chan_alert_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_CHAN_ALERTS)
    if cfg:
        overlay = cfg.get("chan_alerts") or {}
        if isinstance(overlay, dict):
            merged.update(overlay)
    return merged


def allowed_timeframes(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """配置里出现的级别 ∩ {5m, 30m, 1d}。1m/1h 永远不扫。"""
    requested = get_chan_alert_cfg(cfg).get("timeframes") or list(ALLOWED_TIMEFRAMES)
    if isinstance(requested, str):
        requested = [x.strip() for x in requested.split(",")]
    out: List[str] = []
    seen: Set[str] = set()
    for tf in requested:
        key = str(tf or "").strip()
        if key in _ALLOWED and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def poll_minutes(timeframe: str, cfg: Optional[Dict[str, Any]] = None) -> int:
    ca = get_chan_alert_cfg(cfg)
    key = f"poll_minutes_{timeframe}"
    default = int(_DEFAULT_POLL.get(timeframe, 30))
    try:
        n = int(ca.get(key) if ca.get(key) is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(1, n)


def tick_seconds(cfg: Optional[Dict[str, Any]] = None) -> int:
    tfs = allowed_timeframes(cfg)
    if not tfs:
        return 300
    return min(poll_minutes(tf, cfg) for tf in tfs) * 60


def chan_signal_fingerprint(symbol: str, timeframe: str, kind: str, ts: str) -> str:
    raw = f"{(symbol or '').upper()}|{timeframe}|{kind}|{ts}"
    return "chan:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def format_chan_alert(
    symbol: str,
    timeframe: str,
    signal: Dict[str, Any],
) -> str:
    """短文案:标的 · 级别 · 买/卖点标签 · 价格 + 一行说明。不含交易裁决。"""
    level = TF_LABEL.get(timeframe, timeframe)
    label = (signal.get("label") or signal.get("kind") or "").strip() or "?"
    try:
        price_s = f"${float(signal.get('price')):.2f}"
    except (TypeError, ValueError):
        price_s = "--"
    note = " ".join(str(signal.get("note") or "").split())
    head = f"{(symbol or '').upper()} · {level} · {label} · {price_s}"
    return f"{head}\n{note}" if note else head


def resolve_universe(
    cfg: Optional[Dict[str, Any]] = None,
    targets: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[str]:
    ca = get_chan_alert_cfg(cfg)
    override = ca.get("symbols") or []
    if isinstance(override, str):
        override = [s.strip() for s in override.split(",")]
    override = [str(s).strip().upper() for s in override if str(s).strip()]
    if override:
        return override
    if targets is None:
        try:
            from app.data import wheel_repository as wrepo
            targets = wrepo.get_targets()
        except Exception as e:
            logger.info("chan alerts: load wheel targets failed, fallback: %s", e)
            return list(_FALLBACK_SYMBOLS)
    enabled = [
        str(t.get("symbol") or "").strip().upper()
        for t in (targets or [])
        if t.get("enabled")
    ]
    return [s for s in enabled if s]


def is_us_rth(now: Optional[datetime] = None) -> bool:
    try:
        from app.core.wheel_today import us_session_phase
        return us_session_phase(now) == "open"
    except Exception:
        return True


def due_timeframes(
    last_runs: Optional[Dict[str, str]],
    now: datetime,
    cfg: Optional[Dict[str, Any]] = None,
    session_open: bool = True,
) -> List[str]:
    tfs = allowed_timeframes(cfg)
    ca = get_chan_alert_cfg(cfg)
    if ca.get("session_only", True) and not session_open:
        return []
    last_runs = last_runs or {}
    due: List[str] = []
    for tf in tfs:
        last = last_runs.get(tf)
        if not last:
            due.append(tf)
            continue
        try:
            last_dt = datetime.fromisoformat(str(last)[:19])
        except Exception:
            due.append(tf)
            continue
        if (now - last_dt).total_seconds() >= poll_minutes(tf, cfg) * 60:
            due.append(tf)
    return due


def _parse_ts(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:19].replace("Z", ""))
    except Exception:
        pass
    try:
        n = float(raw)
        if n > 1e12:
            n = n / 1000.0
        return datetime.utcfromtimestamp(n)
    except Exception:
        return None


def is_recent_signal(
    ts: Any,
    now: datetime,
    timeframe: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    dt = _parse_ts(ts)
    if dt is None:
        return True
    try:
        bars = int(get_chan_alert_cfg(cfg).get("recent_bars") or 3)
    except (TypeError, ValueError):
        bars = 3
    # 至少 24h:K 线时间戳常为 UTC,now 为本地,短窗口会把刚出现的点判成过期。
    # 更早的历史点靠 fingerprint 去重 + 首次扫描 prime。
    window_min = max(poll_minutes(timeframe, cfg) * max(bars, 1), 24 * 60)
    return abs((now - dt).total_seconds()) <= window_min * 60


def already_sent(fingerprint: str, state: Optional[Dict[str, str]] = None) -> bool:
    """(symbol, timeframe, kind, ts) 出现过即永久跳过(不靠小时冷却)。"""
    if not fingerprint:
        return False
    if state is not None:
        return fingerprint in state
    try:
        from app.services.alert_engine import _load_dedupe
        return fingerprint in (_load_dedupe() or {})
    except Exception:
        return False


def process_chan_signals(
    items: Sequence[Dict[str, Any]],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    send_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    state: Optional[Dict[str, str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """对一组信号做开关/静默/指纹过滤后推送。不发真实单。"""
    cfg = cfg or {}
    ca = get_chan_alert_cfg(cfg)
    skipped = {"disabled": 0, "dup": 0, "quiet": 0, "tf": 0}
    would_send: List[Dict[str, Any]] = []

    if not ca.get("enabled", True) and not force:
        skipped["disabled"] = len(items)
        return {
            "sent_count": 0,
            "skipped": skipped,
            "would_send": [],
            "reason": "disabled",
        }

    acfg = get_alert_cfg(cfg)
    now = now or datetime.now()
    quiet = (not force) and in_quiet_hours(
        now,
        int(acfg.get("quiet_hours_start") or 22),
        int(acfg.get("quiet_hours_end") or 7),
    )

    if state is not None:
        st = state  # 调用方传入则原地更新,便于测试二次去重
    elif dry_run:
        st = {}
    else:
        try:
            from app.services.alert_engine import _load_dedupe
            st = dict(_load_dedupe() or {})
        except Exception:
            st = {}

    pending: List[Dict[str, Any]] = []
    for it in items:
        tf = str(it.get("timeframe") or "")
        if tf not in _ALLOWED:
            skipped["tf"] += 1
            continue
        fp = chan_signal_fingerprint(
            str(it.get("symbol") or ""),
            tf,
            str(it.get("kind") or ""),
            str(it.get("ts") or ""),
        )
        if already_sent(fp, st):
            skipped["dup"] += 1
            continue
        body = format_chan_alert(str(it.get("symbol") or ""), tf, it)
        row = {**dict(it), "fingerprint": fp, "body": body}
        if quiet:
            skipped["quiet"] += 1
            if not dry_run:
                log_push(
                    category="chan",
                    body=body,
                    status="skipped",
                    reason="quiet_hours",
                    fingerprint=fp,
                    title=f"{it.get('symbol')} {tf} {it.get('kind')}",
                    meta={"symbol": it.get("symbol"), "timeframe": tf, "kind": it.get("kind")},
                )
            continue
        pending.append(row)

    sent_fps: List[str] = []
    results: List[Dict[str, Any]] = []
    for row in pending:
        fp = row["fingerprint"]
        body = row["body"]
        if send_fn is not None:
            r = send_fn(body, row)
        elif dry_run:
            r = {"ok": True, "reason": "dry_run", "sent": False}
        else:
            r = send_and_log(
                body,
                category="chan",
                fingerprint=fp,
                title=f"{row.get('symbol')} {row.get('timeframe')} {row.get('kind')}",
                meta={
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                    "kind": row.get("kind"),
                    "ts": row.get("ts"),
                    "price": row.get("price"),
                },
                dry_run=False,
                cfg=cfg,
            )
        if r.get("sent") or dry_run:
            sent_fps.append(fp)
            st[fp] = now.isoformat(timespec="seconds")
            would_send.append(row)
        results.append(r)

    if sent_fps and not dry_run:
        mark_sent(sent_fps, st)

    return {
        "sent_count": len(sent_fps),
        "skipped": skipped,
        "would_send": would_send,
        "quiet": quiet,
        "results": results,
        "reason": "ok" if sent_fps else "no_new",
    }


def default_analyze(symbol: str, timeframe: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """本地 K 线 + chan_engine.analyze,不走 HTTP,不下单。"""
    from datetime import timedelta

    from app.core.chan_engine import analyze
    from app.data.history_backfill import ensure_local_kline_range
    from app.data.source_router import normalize_symbol, resolve_kline_source

    cfg = cfg or {}
    futu = cfg.get("futu") or {}
    limit = int(get_chan_alert_cfg(cfg).get("bar_limit") or 400)
    end_date = datetime.now().isoformat()
    days = _LIMIT_DAYS.get(timeframe, 30)
    start_date = (datetime.now() - timedelta(days=days)).isoformat()
    normalized = normalize_symbol(symbol)
    source = resolve_kline_source(normalized, "finnhub")
    result = ensure_local_kline_range(
        symbol=normalized,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        host=futu.get("host", "127.0.0.1"),
        port=int(futu.get("port") or 11111),
        preferred_adapter=source,
        force=False,
    )
    rows = result.get("bars") or []
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    payload = [
        {
            "timestamp": row.get("ts") or row.get("timestamp"),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
        }
        for row in rows
        if row.get("open") is not None
    ]
    if not payload:
        return {"signals": [], "symbol": normalized, "timeframe": timeframe}
    out = analyze(payload, timeframe)
    out["symbol"] = normalized
    return out


def _load_last_runs() -> Dict[str, str]:
    try:
        from app.data.wheel_repository import get_kv
        raw = get_kv(_LAST_RUN_KEY)
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_last_runs(state: Dict[str, str]) -> None:
    try:
        from app.data.wheel_repository import set_kv
        set_kv(_LAST_RUN_KEY, json.dumps(state, ensure_ascii=False))
    except Exception as e:
        logger.warning("chan alerts save last_runs failed: %s", e)


def run_chan_alert_cycle(
    *,
    cfg: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    analyze_fn: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
    send_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    dry_run: bool = False,
    last_runs: Optional[Dict[str, str]] = None,
    persist: bool = True,
    session_open: Optional[bool] = None,
    prime_on_empty: bool = True,
    force: bool = False,
    state: Optional[Dict[str, str]] = None,
    targets: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """一轮增量扫描。due 的级别才跑 analyze;同指纹最多推一次。"""
    if cfg is None:
        try:
            from app.api.leaps import _load_config
            cfg = _load_config()
        except Exception:
            cfg = {}
    ca = get_chan_alert_cfg(cfg)
    now = now or datetime.now()
    if not ca.get("enabled", True) and not force:
        return {"sent_count": 0, "reason": "disabled", "scanned": []}

    if session_open is None:
        session_open = (not ca.get("session_only", True)) or is_us_rth(now)

    persist_runs = last_runs is None
    runs = dict(last_runs if last_runs is not None else (_load_last_runs() if persist else {}))
    due = due_timeframes(runs, now, cfg, session_open=bool(session_open) or force)
    if not due:
        return {
            "sent_count": 0,
            "reason": "not_due",
            "scanned": [],
            "session_open": session_open,
        }

    universe = resolve_universe(cfg, targets=targets)
    analyze_fn = analyze_fn or default_analyze
    scanned: List[Dict[str, str]] = []
    collected: List[Dict[str, Any]] = []
    bootstrap_tfs = {tf for tf in due if prime_on_empty and tf not in runs}

    for symbol in universe:
        for tf in due:
            scanned.append({"symbol": symbol, "timeframe": tf})
            try:
                result = analyze_fn(symbol, tf, cfg)
            except Exception as e:
                logger.info("chan analyze skip %s %s: %s", symbol, tf, e)
                continue
            for s in (result or {}).get("signals") or []:
                kind = str(s.get("kind") or "")
                if kind not in {"B1", "B2", "B3", "S1", "S2", "S3"}:
                    continue
                collected.append({
                    "symbol": symbol,
                    "timeframe": tf,
                    "kind": kind,
                    "label": s.get("label") or kind,
                    "ts": s.get("ts"),
                    "price": s.get("price"),
                    "note": s.get("note") or "",
                    "bootstrap": tf in bootstrap_tfs,
                })

    primed_fps: List[str] = []
    live_items: List[Dict[str, Any]] = []
    for it in collected:
        if it.get("bootstrap"):
            primed_fps.append(chan_signal_fingerprint(
                it["symbol"], it["timeframe"], it["kind"], str(it.get("ts") or ""),
            ))
            continue
        if not is_recent_signal(it.get("ts"), now, it["timeframe"], cfg):
            continue
        live_items.append(it)

    if primed_fps and not dry_run and persist:
        mark_sent(primed_fps, state)

    out = process_chan_signals(
        live_items,
        cfg=cfg,
        dry_run=dry_run,
        send_fn=send_fn,
        now=now,
        state=state,
        force=force,
    )
    iso = now.isoformat(timespec="seconds")
    for tf in due:
        runs[tf] = iso
    if persist and persist_runs and not dry_run:
        _save_last_runs(runs)

    out["scanned"] = scanned
    out["due"] = due
    out["universe"] = universe
    out["primed"] = len(primed_fps)
    out["session_open"] = session_open
    out["last_runs"] = runs
    return out
