"""持仓期事件日(财报/除息覆盖存续)强制分叉.

对齐 #19 dispose_fork:只逼表态(落袋 vs 续拿),不自动下单,不改止盈百分比.
开仓扫描的 covers_earnings / Roll 硬过滤不在本模块范围.
缺财报数据(部分标的 / 港股)安静降级:不误报、不崩溃.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT_KIND = "event_dispose"
TYPE_EARNINGS = "earnings"
TYPE_DIVIDEND = "dividend"
TYPE_LABEL = {TYPE_EARNINGS: "财报", TYPE_DIVIDEND: "除息"}

DEFAULT_EVENT_CFG: Dict[str, Any] = {
    "enabled": True,
    "window_days": 7,   # T-7,含事件当日
    "urgent_days": 2,   # T-2,含事件当日
}

PUT_BAG = "事件前落袋"
PUT_HOLD = "明知跳空/接货风险续拿"
CALL_BAG = "事件前落袋/Roll"
CALL_HOLD = "明知跳空被 call 或除息提前行权风险续拿"


def _f_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_event_cfg(
    pos_cfg: Optional[Dict[str, Any]] = None,
    full_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """window_days=T-7, urgent_days=T-2, enable/disable. 事件当日算在窗口内."""
    pos: Dict[str, Any] = dict(pos_cfg or {})
    if full_cfg and not pos:
        pos = dict((full_cfg.get("wheel_position") or {}))
    nested = pos.get("event_dispose") if isinstance(pos.get("event_dispose"), dict) else {}
    enabled = pos.get("event_dispose_enabled")
    if enabled is None:
        enabled = nested.get("enabled", DEFAULT_EVENT_CFG["enabled"])
    window = pos.get("event_window_days")
    if window is None:
        window = nested.get("window_days", DEFAULT_EVENT_CFG["window_days"])
    urgent = pos.get("event_urgent_days")
    if urgent is None:
        urgent = nested.get("urgent_days", DEFAULT_EVENT_CFG["urgent_days"])
    return {
        "enabled": bool(enabled),
        "window_days": max(0, _f_int(window, 7)),
        "urgent_days": max(0, _f_int(urgent, 2)),
    }


def _parse_day(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def expiry_date_of(item: Dict[str, Any], today: Optional[date] = None) -> Optional[date]:
    d = _parse_day(item.get("expiry") or item.get("open_expiry"))
    if d is not None:
        return d
    try:
        dte = item.get("dte")
        if dte is None:
            return None
        return (today or date.today()) + timedelta(days=int(dte))
    except (TypeError, ValueError):
        return None


def event_covers_expiry(event_day: Any, expiry_day: Any) -> bool:
    """事件日严格早于到期日才算覆盖存续."""
    ev = _parse_day(event_day)
    exp = _parse_day(expiry_day)
    if ev is None or exp is None:
        return False
    return ev < exp


def in_event_window(days_to_event: Any, window_days: int = 7) -> bool:
    """含事件当日: 0 <= days_to_event <= window_days."""
    try:
        n = int(days_to_event)
    except (TypeError, ValueError):
        return False
    return 0 <= n <= max(0, int(window_days))


def is_urgent_event(days_to_event: Any, urgent_days: int = 2) -> bool:
    try:
        n = int(days_to_event)
    except (TypeError, ValueError):
        return False
    return 0 <= n <= max(0, int(urgent_days))


def _days_between(later: date, earlier: date) -> int:
    return (later - earlier).days


def _safe_earnings(symbol: str, fn: Optional[Callable[[str], Any]] = None) -> Optional[str]:
    try:
        getter = fn
        if getter is None:
            from app.core.earnings import get_next_earnings
            getter = get_next_earnings
        d = getter(symbol)
        parsed = _parse_day(d)
        return parsed.isoformat() if parsed else None
    except Exception as e:
        logger.info("event_dispose earnings(%s) skip: %s", symbol, e)
        return None


def _safe_dividend(symbol: str, fn: Optional[Callable[[str], Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        getter = fn
        if getter is None:
            from app.core.dividends import get_next_dividend
            getter = get_next_dividend
        d = getter(symbol)
        if not d:
            return None
        if isinstance(d, str):
            parsed = _parse_day(d)
            return {"date": parsed.isoformat()} if parsed else None
        parsed = _parse_day((d or {}).get("date") or (d or {}).get("exDate") or (d or {}).get("ex_date"))
        if parsed is None:
            return None
        out = dict(d)
        out["date"] = parsed.isoformat()
        return out
    except Exception as e:
        logger.info("event_dispose dividend(%s) skip: %s", symbol, e)
        return None


def lookup_holding_events(
    item: Dict[str, Any],
    *,
    today: Optional[date] = None,
    earnings_fn: Optional[Callable[[str], Any]] = None,
    dividend_fn: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    """对一条空头腿列出覆盖存续的下一事件.缺数据返回空,不抛."""
    try:
        today = today or date.today()
        symbol = str(item.get("symbol") or "").strip().upper()
        side = str(item.get("side") or "").upper()
        expiry = expiry_date_of(item, today)
        if not symbol or expiry is None:
            return []
        events: List[Dict[str, Any]] = []

        earn = item.get("earnings_date")
        if earn in (None, ""):
            earn = _safe_earnings(symbol, earnings_fn)
        earn_d = _parse_day(earn)
        if earn_d is not None and event_covers_expiry(earn_d, expiry):
            events.append({
                "type": TYPE_EARNINGS,
                "type_label": TYPE_LABEL[TYPE_EARNINGS],
                "date": earn_d.isoformat(),
                "days_to_event": _days_between(earn_d, today),
                "days_to_expiry": _days_between(expiry, today),
                "covering": True,
            })

        # 除息:沿用现有日历;提前行权风险主要在 Call
        if side == "CALL":
            div_raw = item.get("dividend") or item.get("dividend_warn")
            if not div_raw:
                div_raw = _safe_dividend(symbol, dividend_fn)
            div_d = None
            if isinstance(div_raw, dict):
                div_d = _parse_day(div_raw.get("date") or div_raw.get("exDate") or div_raw.get("ex_date"))
            else:
                div_d = _parse_day(div_raw)
            if div_d is None:
                ex = item.get("days_to_ex_div")
                try:
                    if ex is not None:
                        div_d = today + timedelta(days=int(ex))
                except (TypeError, ValueError):
                    div_d = None
            if div_d is not None and event_covers_expiry(div_d, expiry):
                events.append({
                    "type": TYPE_DIVIDEND,
                    "type_label": TYPE_LABEL[TYPE_DIVIDEND],
                    "date": div_d.isoformat(),
                    "days_to_event": _days_between(div_d, today),
                    "days_to_expiry": _days_between(expiry, today),
                    "covering": True,
                })

        events.sort(key=lambda x: (x.get("days_to_event") if x.get("days_to_event") is not None else 99, x.get("type") or ""))
        return events
    except Exception as e:
        logger.info("event_dispose lookup skip: %s", e)
        return []


def pick_window_event(
    events: List[Dict[str, Any]],
    *,
    window_days: int = 7,
    urgent_days: int = 2,
) -> Optional[Dict[str, Any]]:
    """窗口内最近事件.事件当日算入窗.窗外 covering 不触发."""
    chosen = None
    for ev in events or []:
        if not ev.get("covering"):
            continue
        if not in_event_window(ev.get("days_to_event"), window_days):
            continue
        row = dict(ev)
        row["in_window"] = True
        row["urgent"] = is_urgent_event(ev.get("days_to_event"), urgent_days)
        chosen = row
        break
    return chosen


def stance_copy(side: str) -> Dict[str, str]:
    if str(side or "").upper() == "CALL":
        return {"bag": CALL_BAG, "hold": CALL_HOLD}
    return {"bag": PUT_BAG, "hold": PUT_HOLD}


def event_reason_label(event: Dict[str, Any], side: str = "") -> str:
    label = event.get("type_label") or TYPE_LABEL.get(event.get("type"), "事件")
    day = event.get("date") or "?"
    dte = event.get("days_to_expiry")
    dte_s = f"DTE{dte}" if dte is not None else "DTE?"
    tminus = event.get("days_to_event")
    t_s = f"T-{tminus}" if tminus not in (None, "") else "T-?"
    if tminus == 0:
        t_s = "事件当日"
    copy = stance_copy(side)
    return f"{label} {day} 覆盖存续 · {dte_s} · {t_s} 须处理: {copy['bag']} vs {copy['hold']}"


def build_event_dispose_fork(
    item: Dict[str, Any],
    event: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """落袋 vs 续拿.续拿文案按 Put/Call 分叉.不改止盈数字."""
    result = result or {}
    side = str(item.get("side") or result.get("side") or "").upper()
    copy = stance_copy(side)
    label = event.get("type_label") or TYPE_LABEL.get(event.get("type"), "事件")
    day = event.get("date") or "?"
    dte = event.get("days_to_expiry")
    dte_bit = f"距到期 {dte} 天" if dte is not None else "距到期未知"
    tminus = event.get("days_to_event")
    t_bit = "事件当日" if tminus == 0 else f"T-{tminus}"
    tgt = None
    try:
        from app.core.wheel_dispose import profit_target_of
        tgt = profit_target_of(result)
    except Exception:
        q = result.get("quant_thresholds") or {}
        try:
            tgt = float(q.get("profit_target_pct")) if q.get("profit_target_pct") is not None else None
        except (TypeError, ValueError):
            tgt = None
    bag_copy = (
        f"{label} {day} 覆盖存续({dte_bit} · {t_bit})。{copy['bag']}。"
        "决策辅助,不自动下单。"
    )
    hold_copy = (
        f"{copy['hold']}。续拿是方向赌注,不是默认吃θ。"
        "只有你愿意承担跳空/行权风险时才续拿。"
    )
    return {
        "kind": EVENT_KIND,
        "must_manage": True,
        "past_line": False,
        "profit_target_pct": tgt,
        "event": dict(event),
        "bag": {
            "label": "落袋",
            "action": "close",
            "copy": bag_copy,
        },
        "hold": {
            "label": "续拿",
            "action": "hold",
            "directional_bet": True,
            "copy": hold_copy,
        },
        "note": "持仓期事件覆盖存续;强制表态;不自动下单;不改止盈线",
    }


def apply_event_dispose(
    result: Dict[str, Any],
    item: Dict[str, Any],
    pos_cfg: Optional[Dict[str, Any]] = None,
    *,
    today: Optional[date] = None,
    earnings_fn: Optional[Callable[[str], Any]] = None,
    dividend_fn: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """决策树后处理:覆盖存续且落入 T-7/T-2 窗口 → must_manage + dispose_fork.

    不改 action_code 为下单,不改 profit_target_pct.
    已有过线分叉时只附加事件信息,不覆盖落袋/续拿文案.
    """
    try:
        result = dict(result or {})
        cfg = get_event_cfg(pos_cfg)
        if not cfg.get("enabled", True):
            return result
        events = lookup_holding_events(
            item, today=today, earnings_fn=earnings_fn, dividend_fn=dividend_fn,
        )
        chosen = pick_window_event(
            events, window_days=cfg["window_days"], urgent_days=cfg["urgent_days"],
        )
        if not chosen:
            return result

        result["event_cover"] = chosen
        result["must_manage"] = True
        reason = event_reason_label(chosen, str(item.get("side") or result.get("side") or ""))
        reasons = list(result.get("reasons") or [])
        if reason not in reasons:
            reasons.append(reason)
        result["reasons"] = reasons

        existing = result.get("dispose_fork")
        if isinstance(existing, dict) and existing.get("kind") == "dispose_fork":
            merged = dict(existing)
            merged["event"] = chosen
            result["dispose_fork"] = merged
        else:
            result["dispose_fork"] = build_event_dispose_fork(item, chosen, result)

        hint = result.get("secondary_hint") or ""
        if "事件前落袋" not in hint and "覆盖存续" not in (result.get("action_hint") or ""):
            result["secondary_hint"] = reason
        return result
    except Exception as e:
        logger.info("apply_event_dispose skip: %s", e)
        return result


def event_window_fingerprint(item: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> str:
    """首次入窗只推一次:合约 + 事件类型 + 事件日."""
    ev = event or (item.get("event_cover") or {})
    if not ev and isinstance(item.get("dispose_fork"), dict):
        ev = (item.get("dispose_fork") or {}).get("event") or {}
    code = item.get("contract_code") or item.get("cycle_id") or item.get("symbol") or "?"
    raw = "|".join([
        str(code),
        str(ev.get("type") or "event"),
        str(ev.get("date") or ""),
    ])
    return "event:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def format_event_alert(item: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> str:
    ev = event or item.get("event_cover") or {}
    if not ev and isinstance(item.get("dispose_fork"), dict):
        ev = (item.get("dispose_fork") or {}).get("event") or {}
    side = str(item.get("side") or "").upper()
    role = "CSP" if side == "PUT" else ("CC" if side == "CALL" else side or "?")
    copy = stance_copy(side)
    sym = item.get("symbol") or "?"
    try:
        strike_s = f"{float(item.get('strike')):g}" if item.get("strike") is not None else "?"
    except (TypeError, ValueError):
        strike_s = str(item.get("strike") or "?")
    dte = item.get("dte")
    if dte is None:
        dte = ev.get("days_to_expiry")
    label = ev.get("type_label") or TYPE_LABEL.get(ev.get("type"), "事件")
    tminus = ev.get("days_to_event")
    t_s = "事件当日" if tminus == 0 else f"T-{tminus}"
    head = f"⚠ 事件日|{role} {sym} {'卖Put' if side == 'PUT' else '卖Call' if side == 'CALL' else side} ${strike_s}"
    if dte is not None:
        head += f" · DTE{dte}"
    lines = [
        head,
        f"{label} {ev.get('date') or '?'} 覆盖存续 · {t_s}",
        f"须表态: {copy['bag']} vs {copy['hold']}",
        "不自动下单",
        "→ Wheel · 今日必须处理",
    ]
    return "\n".join(lines)


def position_alerts_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """与 _position_alert_loop 同一开关: alert_push_minutes>0 才算持仓告警已开."""
    cfg = cfg or {}
    try:
        from app.services.alert_engine import get_alert_cfg
        acfg = get_alert_cfg(cfg)
        minutes = float(acfg.get("alert_push_minutes") or 0)
        if minutes > 0:
            return True
    except Exception:
        pass
    pos = cfg.get("wheel_position") or {}
    try:
        return float(pos.get("alert_push_minutes") or 0) > 0
    except (TypeError, ValueError):
        return False


def process_event_window_alerts(
    items: List[Dict[str, Any]],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    force: bool = False,
    dry_run: bool = False,
    send_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    state: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """首次入窗推一次 Telegram.告警关闭则跳过.不发真实单."""
    cfg = cfg or {}
    skipped = {"disabled": 0, "alerts_off": 0, "dup": 0, "quiet": 0, "no_event": 0}
    ev_cfg = get_event_cfg(full_cfg=cfg, pos_cfg=cfg.get("wheel_position"))
    if not ev_cfg.get("enabled", True) and not force:
        skipped["disabled"] = len(items or [])
        return {"sent_count": 0, "skipped": skipped, "reason": "disabled", "would_send": []}
    if not force and not position_alerts_enabled(cfg):
        skipped["alerts_off"] = len(items or [])
        return {"sent_count": 0, "skipped": skipped, "reason": "alerts_off", "would_send": []}

    try:
        from app.services.alert_engine import (
            get_alert_cfg,
            in_quiet_hours,
            log_push,
            mark_sent,
            send_and_log,
        )
    except Exception as e:
        logger.info("event alerts: alert_engine unavailable: %s", e)
        return {"sent_count": 0, "skipped": skipped, "reason": "no_pipeline", "would_send": []}

    acfg = get_alert_cfg(cfg)
    now = now or datetime.now()
    quiet = (not force) and in_quiet_hours(
        now,
        int(acfg.get("quiet_hours_start") or 22),
        int(acfg.get("quiet_hours_end") or 7),
    )
    if state is not None:
        st = state
    elif dry_run:
        st = {}
    else:
        try:
            from app.services.alert_engine import _load_dedupe
            st = dict(_load_dedupe() or {})
        except Exception:
            st = {}

    pending: List[Dict[str, Any]] = []
    for it in items or []:
        ev = it.get("event_cover")
        if not ev and isinstance(it.get("dispose_fork"), dict):
            fork = it.get("dispose_fork") or {}
            if fork.get("kind") == EVENT_KIND or fork.get("event"):
                ev = fork.get("event") or ev
        if not ev or not ev.get("in_window"):
            skipped["no_event"] += 1
            continue
        fp = event_window_fingerprint(it, ev)
        if fp in st:
            skipped["dup"] += 1
            continue
        body = format_event_alert(it, ev)
        row = {"item": it, "event": ev, "fingerprint": fp, "body": body}
        if quiet:
            skipped["quiet"] += 1
            if not dry_run:
                try:
                    log_push(
                        category="event",
                        body=body,
                        status="skipped",
                        reason="quiet_hours",
                        fingerprint=fp,
                        title=f"{it.get('symbol')} {ev.get('type')}",
                    )
                except Exception:
                    pass
            continue
        pending.append(row)

    sent_fps: List[str] = []
    would_send: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for row in pending:
        fp = row["fingerprint"]
        body = row["body"]
        it = row["item"]
        ev = row["event"]
        if send_fn is not None:
            r = send_fn(body, row)
        elif dry_run:
            r = {"ok": True, "reason": "dry_run", "sent": False}
        else:
            r = send_and_log(
                body,
                category="event",
                fingerprint=fp,
                title=f"{it.get('symbol')} {ev.get('type')} {ev.get('date')}",
                meta={
                    "symbol": it.get("symbol"),
                    "side": it.get("side"),
                    "event_type": ev.get("type"),
                    "event_date": ev.get("date"),
                    "days_to_event": ev.get("days_to_event"),
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
        try:
            mark_sent(sent_fps, st)
        except Exception as e:
            logger.info("event alerts mark_sent skip: %s", e)

    return {
        "sent_count": len(sent_fps),
        "skipped": skipped,
        "would_send": would_send,
        "quiet": quiet,
        "results": results,
        "reason": "ok" if sent_fps else "no_new",
    }
