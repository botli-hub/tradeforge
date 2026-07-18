"""Wheel 推送引擎 — 事件驱动、去重冷却、静默时段、短模板、组合闸门。

N1 持仓: 状态指纹变化才推, 同指纹冷却; 紧急可破静默。
N2 模板: CSP/CC 短文案 + 可行动作。
N3 开仓: TopN + 分数/年化门槛 + 合约级去重。
N5 闸门: portfolio_put_blocked 时不推 Put 机会(或标注)。
推送一律写 wheel_push_log。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 默认(可被 wheel_position / wheel_scan / wheel_alerts 覆盖) ───────────────

DEFAULT_ALERTS: Dict[str, Any] = {
    # 持仓
    "position_priority_max": 3,
    "position_cooldown_hours": 6.0,
    "position_urgent_cooldown_hours": 2.0,
    "quiet_hours_start": 22,  # 本地时,含
    "quiet_hours_end": 7,  # 本地时,不含; 可跨午夜
    "quiet_hours_allow_urgent": True,
    "digest_hour": 9,
    # 开仓扫描
    "scan_min_score": 0.0,
    "scan_min_annualized": 0.0,
    "scan_dedupe_hours": 12.0,
    "scan_skip_blocked_puts": True,
    "scan_only_new": True,
    # 可执行阈值
    "scan_max_spread_pct": 8.0,
    "scan_require_executable": True,
    # 会话: always | rth | eod — 机会推送时机
    "scan_session_mode": "eod",  # eod=收盘后窗口推; rth=仅盘中; always=不限
}


def get_alert_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """合并 wheel_alerts + 相关旧键(notify_mode / alert_push / telegram_top_n)。"""
    if cfg is None:
        try:
            from app.api.leaps import _load_config
            cfg = _load_config()
        except Exception:
            cfg = {}
    pos = cfg.get("wheel_position") or {}
    scan = cfg.get("wheel_scan") or {}
    alerts = cfg.get("wheel_alerts") or {}
    merged = dict(DEFAULT_ALERTS)
    merged.update(alerts)
    # 兼容旧设置落在 wheel_position / wheel_scan
    if "notify_mode" not in merged:
        merged["notify_mode"] = pos.get("notify_mode") or "realtime"
    if "alert_push_minutes" not in merged:
        merged["alert_push_minutes"] = float(pos.get("alert_push_minutes") or 0)
    if "telegram_top_n" not in merged:
        merged["telegram_top_n"] = int(
            scan.get("telegram_top_n") or scan.get("top_overall") or 5
        )
    if "auto_push_minutes" not in merged:
        merged["auto_push_minutes"] = float(scan.get("auto_push_minutes") or 0)
    return merged


# ── 时间工具 ─────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def in_quiet_hours(
    now: Optional[datetime] = None,
    start: int = 22,
    end: int = 7,
) -> bool:
    """判断是否在静默时段。start==end 表示关闭静默。"""
    try:
        start_i = int(start)
        end_i = int(end)
    except (TypeError, ValueError):
        return False
    if start_i == end_i:
        return False
    h = (now or _now()).hour
    if start_i < end_i:
        return start_i <= h < end_i
    # 跨午夜: 如 22–7
    return h >= start_i or h < end_i


def dte_bucket(dte: Optional[int]) -> str:
    if dte is None:
        return "dte?"
    try:
        d = int(dte)
    except (TypeError, ValueError):
        return "dte?"
    if d <= 3:
        return "dte0-3"
    if d <= 7:
        return "dte4-7"
    if d <= 14:
        return "dte8-14"
    if d <= 21:
        return "dte15-21"
    return "dte22+"


def is_urgent_item(item: Dict[str, Any]) -> bool:
    """紧急: 深度 ITM / 临期接货 / priority≤1 / PREPARE_ASSIGN。"""
    pri = item.get("action_priority")
    try:
        p = int(pri) if pri is not None else 9
    except (TypeError, ValueError):
        p = 9
    code = (item.get("action_code") or "").upper()
    if p <= 1:
        return True
    if code == "PREPARE_ASSIGN":
        return True
    if item.get("deep_itm"):
        return True
    if item.get("itm") and item.get("expiring"):
        return True
    return False


def position_fingerprint(item: Dict[str, Any]) -> str:
    """状态指纹: 同合约同动作同关键状态 → 去重; DTE 桶变化会重新提醒。"""
    code = item.get("contract_code") or item.get("cycle_id") or "?"
    action = item.get("action_code") or "NONE"
    pri = item.get("action_priority")
    try:
        p = int(pri) if pri is not None else 9
    except (TypeError, ValueError):
        p = 9
    # priority 分档,避免 2↔3 抖动刷屏
    pri_band = "p1" if p <= 1 else ("p2-3" if p <= 3 else "p4+")
    flags = []
    if item.get("deep_itm"):
        flags.append("deep")
    elif item.get("itm"):
        flags.append("itm")
    else:
        flags.append("otm")
    if item.get("capital_tight"):
        flags.append("tight")
    if item.get("would_open_today") == "no":
        flags.append("wopen_no")
    raw = "|".join([
        str(code),
        str(action),
        pri_band,
        dte_bucket(item.get("dte")),
        ",".join(flags),
    ])
    return "pos:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def opportunity_fingerprint(opp: Dict[str, Any]) -> str:
    sym = opp.get("symbol") or "?"
    side = opp.get("side") or "?"
    strike = opp.get("strike")
    expiry = str(opp.get("expiry") or "")[:10]
    raw = f"{sym}|{side}|{strike}|{expiry}"
    return "opp:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── 短模板 ───────────────────────────────────────────────────────────────────

def _side_label(side: str) -> str:
    s = (side or "").upper()
    if s in ("PUT", "P"):
        return "卖Put"
    if s in ("CALL", "C"):
        return "卖Call"
    return side or "?"


def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "--"


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):.0f}%"
    except (TypeError, ValueError):
        return "--"


def format_position_alert(item: Dict[str, Any], style: str = "short") -> str:
    """单条持仓告警短文案(CSP/CC 分叉)。"""
    sym = item.get("symbol") or "?"
    side = (item.get("side") or "").upper()
    strike = item.get("strike")
    try:
        strike_s = f"{float(strike):g}" if strike is not None else "?"
    except (TypeError, ValueError):
        strike_s = str(strike)
    dte = item.get("dte")
    profit = item.get("profit_pct")
    hint = item.get("action_hint") or "关注"
    code = item.get("action_code") or ""
    urgent = is_urgent_item(item)
    icon = "🚨" if urgent else "💰"
    role = "CSP" if side in ("PUT", "P") else ("CC" if side in ("CALL", "C") else side)

    head = f"{icon} 管仓|{role} {sym} {_side_label(side)} ${strike_s}"
    if dte is not None:
        head += f" · DTE{dte}"

    state_bits = []
    if item.get("deep_itm"):
        state_bits.append("深ITM")
    elif item.get("itm"):
        state_bits.append("ITM")
    else:
        state_bits.append("OTM")
    if profit is not None:
        state_bits.append(f"浮盈{_fmt_pct(profit)}")
    rem = item.get("remaining_annualized")
    if rem is not None and not item.get("itm"):
        state_bits.append(f"剩余年化{_fmt_pct(rem)}")
    if item.get("capital_tight"):
        state_bits.append("资金紧")

    lines = [head, f"状态: {' · '.join(state_bits)}", f"动作: {hint}"]
    if code and code != "NONE":
        lines[-1] += f" ({code})"

    # CSP 接货清单要点
    cl = item.get("assign_checklist") or {}
    if side in ("PUT", "P") and (item.get("itm") or code == "PREPARE_ASSIGN") and cl:
        bits = []
        if cl.get("assign_notional") is not None:
            bits.append(f"名义{_fmt_money(cl['assign_notional'])}")
        if cl.get("floor_ok") is True:
            bits.append("floor✓")
        elif cl.get("floor_ok") is False:
            bits.append("floor超")
        if cl.get("over_symbol_cap"):
            bits.append("超标的上限")
        next_h = cl.get("next_step_hint") or "接货后可卖CC"
        if bits:
            lines.append("清单: " + " · ".join(bits) + f" · {next_h}")
        else:
            lines.append(f"清单: {next_h}")
    # CC 交货
    if side in ("CALL", "C") and (item.get("itm") or code == "PREPARE_ASSIGN"):
        lines.append("提示: 放任=按strike卖出持股 · 不愿卖则Roll/买回")

    if item.get("would_open_today") == "no":
        lines.append("⚠ 以今日纪律不会新开此腿")

    lines.append("→ Wheel · 管理")

    if style == "detailed":
        for r in (item.get("reasons") or [])[:4]:
            lines.append(f"· {r}")
    return "\n".join(lines)


def format_position_digest(items: List[Dict[str, Any]], title: Optional[str] = None) -> str:
    today = _now().strftime("%Y-%m-%d")
    lines = [title or f"📋 Wheel 今日管仓 · {today}"]
    for i in items[:15]:
        sym = i.get("symbol") or "?"
        side = _side_label(i.get("side") or "")
        hint = i.get("action_hint") or "?"
        dte = i.get("dte")
        profit = i.get("profit_pct")
        bit = f"· {sym} {side}"
        if dte is not None:
            bit += f" DTE{dte}"
        if profit is not None:
            bit += f" {_fmt_pct(profit)}"
        bit += f" → {hint}"
        lines.append(bit)
    if not items:
        lines.append("· 暂无待办")
    return "\n".join(lines)


def format_scan_alerts(
    opps: List[Dict[str, Any]],
    *,
    scanned_at: str = "",
    put_blocked: bool = False,
    capital_tight: bool = False,
) -> str:
    ts = (scanned_at or _now_iso())[:16]
    lines = [f"🎯 开仓机会 Top · {ts}"]
    if put_blocked:
        lines.append("⛔ 组合闸门: Put 暂不可新开(仅列 Call/已标注)")
    elif capital_tight:
        lines.append("⚠ 资金偏紧,优先高分/低占用")
    if not opps:
        lines.append("本轮无可推机会")
        return "\n".join(lines)
    for o in opps:
        icon = "🟢" if (o.get("side") or "").upper() == "PUT" else "🔵"
        tags = []
        if o.get("covers_earnings"):
            tags.append("财报")
        if o.get("exceeds_capital") or o.get("portfolio_blocked"):
            tags.append("占满")
        if o.get("trend") == "DOWN":
            tags.append("趋势弱")
        tag_s = (" · " + "/".join(tags)) if tags else ""
        ann = o.get("annualized")
        score = o.get("score")
        dte = o.get("dte")
        delta = o.get("delta")
        bid = o.get("bid")
        try:
            strike_s = f"{float(o.get('strike')):g}"
        except (TypeError, ValueError):
            strike_s = str(o.get("strike"))
        exp = str(o.get("expiry") or "")[:10]
        line = (
            f"{icon} {o.get('symbol')} {_side_label(o.get('side') or '')} "
            f"{exp} ${strike_s}"
        )
        meta = []
        if delta is not None:
            try:
                meta.append(f"Δ{float(delta):.2f}")
            except (TypeError, ValueError):
                pass
        if dte is not None:
            meta.append(f"{dte}天")
        if bid is not None:
            try:
                meta.append(f"prem {float(bid):g}")
            except (TypeError, ValueError):
                pass
        if ann is not None:
            try:
                meta.append(f"年化{float(ann):.0f}%")
            except (TypeError, ValueError):
                pass
        if score is not None:
            try:
                meta.append(f"分{float(score):.1f}")
            except (TypeError, ValueError):
                pass
        lines.append(line)
        if meta or tag_s:
            lines.append("   " + " · ".join(meta) + tag_s)
    lines.append("→ Wheel · 机会")
    return "\n".join(lines)


def sample_position_message() -> str:
    return format_position_alert({
        "symbol": "ARM",
        "side": "PUT",
        "strike": 95,
        "dte": 5,
        "profit_pct": -12,
        "action_hint": "准备接货",
        "action_code": "PREPARE_ASSIGN",
        "action_priority": 1,
        "deep_itm": True,
        "itm": True,
        "expiring": True,
        "assign_checklist": {
            "assign_notional": 9500,
            "floor_ok": True,
            "next_step_hint": "接货后可卖CC",
        },
    })


def sample_scan_message() -> str:
    return format_scan_alerts([
        {
            "symbol": "AAPL", "side": "PUT", "expiry": "2026-08-15",
            "strike": 180, "delta": 0.25, "dte": 28, "bid": 2.4,
            "annualized": 22, "score": 8.2,
        },
        {
            "symbol": "NVDA", "side": "CALL", "expiry": "2026-08-08",
            "strike": 140, "delta": 0.28, "dte": 21, "bid": 3.1,
            "annualized": 18, "score": 7.5,
        },
    ], scanned_at=_now_iso())


# ── 去重状态(KV JSON) ────────────────────────────────────────────────────────

_DEDUP_KEY = "alert_dedupe_state"


def _load_dedupe() -> Dict[str, str]:
    """fingerprint -> last_sent_iso"""
    try:
        from app.data.wheel_repository import get_kv
        raw = get_kv(_DEDUP_KEY)
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_dedupe(state: Dict[str, str]) -> None:
    try:
        from app.data.wheel_repository import set_kv
        # 只保留最近 500 条,防膨胀
        if len(state) > 500:
            items = sorted(state.items(), key=lambda x: x[1], reverse=True)[:500]
            state = dict(items)
        set_kv(_DEDUP_KEY, json.dumps(state, ensure_ascii=False))
    except Exception as e:
        logger.warning("save dedupe failed: %s", e)


def is_cooled(
    fingerprint: str,
    cooldown_hours: float,
    state: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
) -> bool:
    if cooldown_hours <= 0:
        return False
    st = state if state is not None else _load_dedupe()
    last = st.get(fingerprint)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(str(last)[:19])
    except Exception:
        return False
    return (now or _now()) < last_dt + timedelta(hours=float(cooldown_hours))


def mark_sent(fingerprints: List[str], state: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    st = dict(state if state is not None else _load_dedupe())
    iso = _now_iso()
    for fp in fingerprints:
        if fp:
            st[fp] = iso
    _save_dedupe(st)
    return st


# ── 推送日志 ─────────────────────────────────────────────────────────────────

def log_push(
    *,
    category: str,
    body: str,
    status: str,
    reason: str = "",
    fingerprint: str = "",
    title: str = "",
    meta: Optional[Dict[str, Any]] = None,
    channel: str = "telegram",
) -> None:
    try:
        from app.data.wheel_repository import add_push_log
        add_push_log(
            channel=channel,
            category=category,
            fingerprint=fingerprint or None,
            title=title or None,
            body=body,
            meta=meta,
            status=status,
            reason=reason or None,
        )
    except Exception as e:
        logger.warning("push log failed: %s", e)


def send_and_log(
    text: str,
    *,
    category: str,
    fingerprint: str = "",
    title: str = "",
    meta: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """真正发 TG 并记日志。返回 {ok, reason, sent}。"""
    if dry_run:
        return {"ok": True, "reason": "dry_run", "sent": False}

    from app.services.notifier import TelegramNotifier
    if cfg is None:
        try:
            from app.api.leaps import _load_config
            cfg = _load_config()
        except Exception:
            cfg = {}
    notifier = TelegramNotifier.from_config(cfg)
    if not notifier._enabled:
        log_push(
            category=category, body=text, status="skipped", reason="not_configured",
            fingerprint=fingerprint, title=title, meta=meta,
        )
        return {"ok": False, "reason": "not_configured", "sent": False}

    detail = notifier.send_detailed(text)
    ok = bool(detail.get("ok"))
    log_push(
        category=category,
        body=text,
        status="sent" if ok else "failed",
        reason=detail.get("reason") or ("ok" if ok else "fail"),
        fingerprint=fingerprint,
        title=title,
        meta=meta,
    )
    return {"ok": ok, "reason": detail.get("reason"), "sent": ok}


# ── 持仓推送主流程 ───────────────────────────────────────────────────────────

def select_position_items(
    items: List[Dict[str, Any]],
    priority_max: int = 3,
) -> List[Dict[str, Any]]:
    out = []
    for i in items or []:
        pri = i.get("action_priority")
        try:
            p = int(pri) if pri is not None else 9
        except (TypeError, ValueError):
            p = 9
        if p <= int(priority_max) or is_urgent_item(i):
            # 无动作且非紧急可跳过
            if (i.get("action_code") or "NONE") == "NONE" and not is_urgent_item(i) and p > priority_max:
                continue
            out.append(i)
    out.sort(key=lambda x: (x.get("action_priority") or 9, x.get("dte") if x.get("dte") is not None else 999))
    return out


def process_position_alerts(
    items: List[Dict[str, Any]],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    force: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """事件驱动持仓推送。

    force=True: 忽略冷却与静默(手动推送)。
    """
    full_cfg = cfg
    if full_cfg is None:
        try:
            from app.api.leaps import _load_config
            full_cfg = _load_config()
        except Exception:
            full_cfg = {}
    acfg = get_alert_cfg(full_cfg)
    notify_mode = (acfg.get("notify_mode") or "realtime").lower()
    pri_max = int(acfg.get("position_priority_max") or 3)
    cd_h = float(acfg.get("position_cooldown_hours") or 6)
    urgent_cd_h = float(acfg.get("position_urgent_cooldown_hours") or 2)
    q_start = int(acfg.get("quiet_hours_start") or 22)
    q_end = int(acfg.get("quiet_hours_end") or 7)
    allow_urgent = bool(acfg.get("quiet_hours_allow_urgent", True))

    candidates = select_position_items(items, pri_max)
    state = _load_dedupe()
    now = _now()
    quiet = (not force) and in_quiet_hours(now, q_start, q_end)

    to_send: List[Tuple[Dict[str, Any], str]] = []  # (item, fingerprint)
    digest_items: List[Dict[str, Any]] = []
    skipped = {"cooldown": 0, "quiet": 0, "digest_defer": 0}

    for item in candidates:
        fp = position_fingerprint(item)
        urgent = is_urgent_item(item)
        cooldown = urgent_cd_h if urgent else cd_h
        if not force and is_cooled(fp, cooldown, state, now):
            skipped["cooldown"] += 1
            continue

        # digest: 非紧急并入日汇总
        if notify_mode == "digest" and not urgent and not force:
            digest_items.append(item)
            skipped["digest_defer"] += 1
            continue

        if quiet and not (urgent and allow_urgent):
            skipped["quiet"] += 1
            log_push(
                category="position",
                body=format_position_alert(item),
                status="skipped",
                reason="quiet_hours",
                fingerprint=fp,
                title=f"{item.get('symbol')} {item.get('action_code')}",
            )
            continue

        to_send.append((item, fp))

    sent_fps: List[str] = []
    messages: List[str] = []
    results: List[Dict[str, Any]] = []

    # 批量: ≤3 条合并一条消息, 否则分条(防刷 + 可读)
    if to_send:
        if len(to_send) <= 3:
            body = "\n\n".join(format_position_alert(it) for it, _ in to_send)
            fps = [fp for _, fp in to_send]
            r = send_and_log(
                body, category="position", fingerprint=",".join(fps),
                title=f"position×{len(to_send)}",
                meta={"count": len(to_send), "symbols": [it.get("symbol") for it, _ in to_send]},
                dry_run=dry_run, cfg=full_cfg,
            )
            if r.get("sent") or dry_run:
                sent_fps.extend(fps)
            messages.append(body)
            results.append(r)
        else:
            # 紧急优先逐条/分批发前 8 条
            for it, fp in to_send[:8]:
                body = format_position_alert(it)
                r = send_and_log(
                    body, category="position", fingerprint=fp,
                    title=f"{it.get('symbol')} {it.get('action_code')}",
                    meta={"symbol": it.get("symbol"), "code": it.get("action_code")},
                    dry_run=dry_run, cfg=full_cfg,
                )
                if r.get("sent") or dry_run:
                    sent_fps.append(fp)
                messages.append(body)
                results.append(r)

    # digest 日汇总(仅 digest 模式, 且到点或 force)
    digest_sent = False
    if digest_items and notify_mode == "digest":
        try:
            from app.data.wheel_repository import get_kv, set_kv
            today = now.date().isoformat()
            already = get_kv("position_digest_sent") == today
            hour_ok = now.hour >= int(acfg.get("digest_hour") or 9)
            if force or (hour_ok and not already and not quiet):
                body = format_position_digest(digest_items)
                r = send_and_log(
                    body, category="digest", fingerprint=f"digest:{today}",
                    title="position_digest", meta={"count": len(digest_items)},
                    dry_run=dry_run, cfg=full_cfg,
                )
                if r.get("sent") or dry_run:
                    set_kv("position_digest_sent", today)
                    digest_sent = True
                    for it in digest_items:
                        sent_fps.append(position_fingerprint(it))
                results.append(r)
                messages.append(body)
        except Exception as e:
            logger.warning("digest send failed: %s", e)

    if sent_fps and not dry_run:
        mark_sent(sent_fps, state)

    return {
        "candidates": len(candidates),
        "sent_count": len(sent_fps),
        "messages": len(messages),
        "skipped": skipped,
        "digest_sent": digest_sent,
        "quiet": quiet,
        "notify_mode": notify_mode,
        "results": results,
        "preview": messages[:3],
        "items": [
            {
                "symbol": it.get("symbol"),
                "action_code": it.get("action_code"),
                "action_priority": it.get("action_priority"),
                "fingerprint": fp,
                "urgent": is_urgent_item(it),
            }
            for it, fp in to_send
        ],
    }


# ── 开仓机会推送 ─────────────────────────────────────────────────────────────

def scan_session_allows(mode: str = "eod", now: Optional[datetime] = None) -> bool:
    """机会推送是否在允许会话窗口。

    always: 总是; rth: 美股盘中; eod: 收盘后约 2h 窗口(UTC 21–23 宽松)
    """
    mode = (mode or "always").lower()
    if mode == "always":
        return True
    try:
        from app.core.wheel_today import us_session_phase
        phase = us_session_phase(now)
    except Exception:
        phase = "open"
    if mode == "rth":
        return phase == "open"
    if mode == "eod":
        return phase in ("after", "closed")  # 收盘后+周末可推 digest
    return True


def _opp_executable(o: Dict[str, Any], max_spread: float) -> bool:
    if o.get("exceeds_capital"):
        return False
    if o.get("portfolio_blocked") or o.get("actionable") is False:
        return False
    sp = o.get("spread_pct")
    if sp is not None and max_spread > 0:
        try:
            if float(sp) > float(max_spread):
                return False
        except (TypeError, ValueError):
            pass
    bid = o.get("bid")
    if bid is not None:
        try:
            if float(bid) <= 0:
                return False
        except (TypeError, ValueError):
            pass
    return True


def filter_scan_opportunities(
    opps: List[Dict[str, Any]],
    *,
    top_n: int = 5,
    min_score: float = 0.0,
    min_annualized: float = 0.0,
    put_blocked: bool = False,
    skip_blocked_puts: bool = True,
    only_new: bool = True,
    dedupe_hours: float = 12.0,
    max_spread_pct: float = 8.0,
    require_executable: bool = True,
    state: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """过滤并排序机会,返回 (selected, fingerprints)。"""
    st = state if state is not None else _load_dedupe()
    now = now or _now()
    filtered: List[Dict[str, Any]] = []
    for o in opps or []:
        side = (o.get("side") or "").upper()
        if put_blocked and side == "PUT" and skip_blocked_puts:
            continue
        try:
            score = float(o.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            ann = float(o.get("annualized") or 0)
        except (TypeError, ValueError):
            ann = 0.0
        if min_score > 0 and score < min_score:
            continue
        if min_annualized > 0 and ann < min_annualized:
            continue
        if o.get("exceeds_capital") and side == "PUT":
            continue
        if require_executable and not _opp_executable(o, max_spread_pct):
            continue
        filtered.append(o)

    filtered.sort(
        key=lambda x: (
            float(x.get("score") or 0),
            float(x.get("annualized") or 0),
        ),
        reverse=True,
    )

    selected: List[Dict[str, Any]] = []
    fps: List[str] = []
    for o in filtered:
        if len(selected) >= max(1, int(top_n)):
            break
        fp = opportunity_fingerprint(o)
        if only_new and is_cooled(fp, dedupe_hours, st, now):
            continue
        selected.append(o)
        fps.append(fp)
    return selected, fps


def process_scan_push(
    result: Dict[str, Any],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    force: bool = False,
    dry_run: bool = False,
    portfolio_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """处理全池扫描结果推送。"""
    full_cfg = cfg
    if full_cfg is None:
        try:
            from app.api.leaps import _load_config
            full_cfg = _load_config()
        except Exception:
            full_cfg = {}
    acfg = get_alert_cfg(full_cfg)
    top_n = int(acfg.get("telegram_top_n") or 5)
    min_score = float(acfg.get("scan_min_score") or 0)
    min_ann = float(acfg.get("scan_min_annualized") or 0)
    dedupe_h = float(acfg.get("scan_dedupe_hours") or 12)
    skip_puts = bool(acfg.get("scan_skip_blocked_puts", True))
    only_new = bool(acfg.get("scan_only_new", True)) and not force
    max_spread = float(acfg.get("scan_max_spread_pct") or 8)
    require_exec = bool(acfg.get("scan_require_executable", True))
    session_mode = str(acfg.get("scan_session_mode") or "eod")

    put_blocked = False
    capital_tight = False
    if portfolio_ctx:
        put_blocked = bool(portfolio_ctx.get("portfolio_put_blocked"))
        capital_tight = bool(portfolio_ctx.get("capital_tight"))
    else:
        summary = (result or {}).get("summary") or {}
        put_blocked = bool(summary.get("portfolio_put_blocked"))
        capital_tight = bool(summary.get("capital_tight"))

    if not force and not scan_session_allows(session_mode):
        log_push(category="scan", body="(session skip)", status="skipped", reason=f"session:{session_mode}")
        return {
            "sent": False,
            "reason": f"session_{session_mode}",
            "selected": 0,
            "put_blocked": put_blocked,
        }

    q_start = int(acfg.get("quiet_hours_start") or 22)
    q_end = int(acfg.get("quiet_hours_end") or 7)
    quiet = (not force) and in_quiet_hours(_now(), q_start, q_end)
    if quiet:
        log_push(category="scan", body="(quiet skip)", status="skipped", reason="quiet_hours")
        return {
            "sent": False,
            "reason": "quiet_hours",
            "selected": 0,
            "put_blocked": put_blocked,
        }

    opps = (result or {}).get("opportunities") or []
    selected, fps = filter_scan_opportunities(
        opps,
        top_n=top_n,
        min_score=min_score,
        min_annualized=min_ann,
        put_blocked=put_blocked,
        skip_blocked_puts=skip_puts,
        only_new=only_new,
        dedupe_hours=dedupe_h,
        max_spread_pct=max_spread,
        require_executable=require_exec,
    )

    if not selected:
        log_push(
            category="scan",
            body="无可推新机会",
            status="skipped",
            reason="no_new_or_empty",
            meta={"raw": len(opps), "put_blocked": put_blocked},
        )
        return {
            "sent": False,
            "reason": "no_new_or_empty",
            "selected": 0,
            "raw": len(opps),
            "put_blocked": put_blocked,
        }

    body = format_scan_alerts(
        selected,
        scanned_at=result.get("scanned_at") or "",
        put_blocked=put_blocked,
        capital_tight=capital_tight,
    )
    r = send_and_log(
        body,
        category="scan",
        fingerprint=",".join(fps),
        title=f"scan×{len(selected)}",
        meta={
            "count": len(selected),
            "put_blocked": put_blocked,
            "symbols": [o.get("symbol") for o in selected],
        },
        dry_run=dry_run,
        cfg=full_cfg,
    )
    if r.get("sent") or dry_run:
        mark_sent(fps)
    return {
        "sent": bool(r.get("sent")),
        "reason": r.get("reason"),
        "selected": len(selected),
        "raw": len(opps),
        "put_blocked": put_blocked,
        "preview": body,
        "items": [
            {"symbol": o.get("symbol"), "side": o.get("side"), "strike": o.get("strike"),
             "score": o.get("score"), "fingerprint": fp}
            for o, fp in zip(selected, fps)
        ],
    }


def run_position_alert_cycle(
    host: str = "127.0.0.1",
    port: int = 11111,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """拉体检 → 处理推送。供 loop 与 API 共用。"""
    from app.api.wheel import check_open_positions_core
    from app.api.leaps import _load_config

    cfg = _load_config()
    data = check_open_positions_core(host, port)
    items = data.get("items") or []
    out = process_position_alerts(items, cfg=cfg, force=force, dry_run=dry_run)
    out["checked"] = len(items)
    out["profit_target_pct"] = data.get("profit_target_pct")
    return out
