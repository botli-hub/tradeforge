"""Telegram 状态摘要(status snapshot):在场持仓 / 最近触线 / Sim 纸面.

与 alert_engine.format_position_digest(管仓待办, notify_mode=digest) 不同 ——
本模块是看板式状态快照,不依赖决策树/冷却/静默,发到 legacy/position 频道
(顶层 telegram.bot_token + chat_id)。

数据口径复用 notion_sync / google_sheets_sync 的 map_*。
失败只打日志;消息按 TG 4096 上限分片。
默认 enabled=false;手动 API 始终可强制推一次。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
TG_MAX_LEN = 4096
KV_SENT_DATE = "status_digest_sent_date"

DEFAULT_STATUS_DIGEST: Dict[str, Any] = {
    "enabled": False,  # 安全默认关;手动 API 不受限
    "status_digest_hour": 8,  # Asia/Shanghai 本地钟点;每天最多一次
    "status_digest_minutes": 0,  # >0 时改用间隔推送,忽略 hour
    "touch_limit": 10,
}


def get_status_digest_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_STATUS_DIGEST)
    if cfg:
        overlay = cfg.get("status_digest") or {}
        if isinstance(overlay, dict):
            merged.update({k: v for k, v in overlay.items() if v is not None})
    return merged


def _fmt_num(val: Any, *, prefix: str = "", digits: int = 2) -> str:
    if val is None or val == "":
        return ""
    try:
        n = float(val)
    except (TypeError, ValueError):
        return str(val)
    if abs(n - round(n)) < 1e-9:
        return f"{prefix}{int(round(n))}"
    return f"{prefix}{n:.{digits}f}"


def _fmt_side_contract(fields: Dict[str, Any]) -> str:
    side = str(fields.get("side") or "").upper()
    strike = fields.get("strike")
    expiry = str(fields.get("expiry") or "")[:10]
    bits = []
    if side:
        bits.append(side)
    if strike is not None and strike != "":
        bits.append(_fmt_num(strike, prefix="$"))
    if expiry:
        bits.append(expiry)
    return " · ".join(bits) if bits else ""


def _pnl_or_hint(fields: Dict[str, Any]) -> str:
    """浮盈优先;否则权利金/已实现作 hint。无行情时不装 OpenD。"""
    pnl = fields.get("pnl")
    if pnl is not None and pnl != "":
        try:
            n = float(pnl)
            sign = "+" if n > 0 else ""
            return f"PnL {sign}{_fmt_num(n, prefix='$')}"
        except (TypeError, ValueError):
            pass
    prem = fields.get("premium")
    if prem is not None and prem != "":
        try:
            return f"权利金 ${_fmt_num(prem)}"
        except Exception:
            return f"权利金 {prem}"
    note = fields.get("note")
    if note and not str(note).startswith("dte="):
        return str(note)
    return "—"


def _join_line(head: str, *segments: str) -> str:
    bits = [head] + [s for s in segments if s]
    return " · ".join(bits)


def _fmt_premium_usd(val: Any) -> str:
    """权利金金额展示:始终 $x.xx(含 0 → $0.00)。"""
    try:
        n = float(val if val is not None else 0)
    except (TypeError, ValueError):
        n = 0.0
    return f"${n:,.2f}"


def format_premium_totals_lines(
    premium_month: Optional[float] = None,
    premium_total: Optional[float] = None,
) -> List[str]:
    """本月 / 累计权利金两行(与 get_stats.premium_month/total 同口径)。"""
    return [
        f"本月权利金 {_fmt_premium_usd(premium_month)}",
        f"累计权利金 {_fmt_premium_usd(premium_total)}",
    ]


def fetch_premium_totals(
    get_stats_fn: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, float]:
    """读取实盘权利金汇总;失败则归零。不含 Sim。"""
    try:
        if get_stats_fn is None:
            from app.data.wheel_repository import get_stats
            get_stats_fn = get_stats
        stats = get_stats_fn() or {}
        return {
            "premium_month": float(stats.get("premium_month") or 0),
            "premium_total": float(stats.get("premium_total") or 0),
        }
    except Exception as e:
        logger.warning("status digest premium totals failed: %s", e)
        return {"premium_month": 0.0, "premium_total": 0.0}


def format_position_line(title: str, fields: Dict[str, Any]) -> str:
    fields = enrich_position_fields(fields)
    symbol = fields.get("symbol") or "?"
    status = str(fields.get("status") or "")
    head = f"· {symbol}" + (f" {status}" if status else "")
    contract = _fmt_side_contract(fields)
    dte = fields.get("dte")
    dte_s = f"DTE{dte}" if dte is not None else ""
    return _join_line(head, contract, dte_s, _pnl_or_hint(fields))


def format_touch_line(title: str, fields: Dict[str, Any]) -> str:
    symbol = fields.get("symbol") or "?"
    side = str(fields.get("side") or "").upper()
    strike = fields.get("strike")
    tf = str(fields.get("timeframe") or "1d")
    code = str(fields.get("contract") or "")
    mid = " ".join(
        x for x in (
            side,
            _fmt_num(strike, prefix="$") if strike is not None and strike != "" else "",
            tf,
        ) if x
    )
    return _join_line(f"· {symbol}", mid, code)


def format_sim_line(title: str, fields: Dict[str, Any]) -> str:
    symbol = fields.get("symbol") or "?"
    status = str(fields.get("status") or "")
    strategy = str(fields.get("strategy") or "")
    level = str(fields.get("level") or "")
    head = f"· {symbol}" + (f" {status}" if status else "")
    strat = "/".join(x for x in (strategy, level) if x)
    contract = _fmt_side_contract(fields)
    return _join_line(head, strat, contract, _pnl_or_hint(fields))


def enrich_position_fields(fields: Dict[str, Any], cycle: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """补 DTE:mapper note=dte=N 或 cycle.open_dte。"""
    out = dict(fields)
    if out.get("dte") is None and cycle and cycle.get("open_dte") is not None:
        out["dte"] = cycle.get("open_dte")
    if out.get("dte") is None:
        note = str(out.get("note") or "")
        if note.startswith("dte="):
            try:
                out["dte"] = int(float(note.split("=", 1)[1]))
            except (TypeError, ValueError):
                pass
    return out


def build_status_digest_text(
    rows: Dict[str, List[Tuple[str, str, Dict[str, Any]]]],
    *,
    now: Optional[datetime] = None,
    touch_limit: int = 10,
    premium_month: Optional[float] = None,
    premium_total: Optional[float] = None,
) -> str:
    """组装完整摘要文本(可能超过 4096;调用方再 chunk)。

    premium_month / premium_total: 实盘台账净权利金(与 get_stats 同口径);
    未传入时按 $0.00 展示,保证摘要结构稳定。
    """
    now = now or datetime.now(SHANGHAI)
    ts = now.strftime("%Y-%m-%d %H:%M CST")
    lines: List[str] = [f"📊 TradeForge 状态摘要 · {ts}", ""]
    lines.extend(format_premium_totals_lines(premium_month, premium_total))
    lines.append("")

    positions = rows.get("positions") or []
    lines.append(f"📍 在场持仓 ({len(positions)})")
    if not positions:
        lines.append("· 暂无")
    else:
        for _sk, title, fields in positions:
            lines.append(format_position_line(title, enrich_position_fields(fields)))

    lines.append("")
    touches = (rows.get("touches") or [])[: max(0, int(touch_limit))]
    lines.append(f"📌 最近触线 ({len(touches)})")
    if not touches:
        lines.append("· 暂无")
    else:
        for _sk, title, fields in touches:
            lines.append(format_touch_line(title, fields))

    lines.append("")
    sims = rows.get("sim") or []
    lines.append(f"🧪 Sim纸面 ({len(sims)})")
    if not sims:
        lines.append("· 暂无")
    else:
        for _sk, title, fields in sims:
            lines.append(format_sim_line(title, fields))

    return "\n".join(lines)


def chunk_telegram_text(text: str, limit: int = TG_MAX_LEN) -> List[str]:
    """按行拆分,尽量不截断中间行;单行超限则硬切。"""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for line in text.split("\n"):
        # +1 for newline except first in buf
        add = len(line) + (1 if buf else 0)
        if buf and buf_len + add > limit:
            parts.append("\n".join(buf))
            buf = []
            buf_len = 0
            add = len(line)
        if len(line) > limit:
            if buf:
                parts.append("\n".join(buf))
                buf = []
                buf_len = 0
            for i in range(0, len(line), limit):
                parts.append(line[i : i + limit])
            continue
        if buf:
            buf.append(line)
            buf_len += add
        else:
            buf = [line]
            buf_len = len(line)
    if buf:
        parts.append("\n".join(buf))
    # 分片标题后缀
    if len(parts) > 1:
        n = len(parts)
        labeled = []
        for i, p in enumerate(parts, 1):
            tag = f"\n\n…({i}/{n})"
            if len(p) + len(tag) <= limit:
                labeled.append(p + tag)
            else:
                labeled.append(p[: max(0, limit - len(tag))] + tag)
        return labeled
    return parts


def collect_status_rows(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    get_cycles_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    get_timing_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    list_sim_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Tuple[str, str, Dict[str, Any]]]]:
    """复用 notion_sync map_*;touch_limit 取自 status_digest。可注入 repo 便于单测。"""
    from app.services.notion_sync import map_position_row, map_sim_row, map_touch_row

    sd = get_status_digest_cfg(cfg)
    touch_limit = int(sd.get("touch_limit") or 10)

    if get_cycles_fn is None:
        from app.data import wheel_repository as wrepo
        get_cycles_fn = wrepo.get_cycles
    if get_timing_fn is None:
        from app.data import leaps_repository as lrepo
        get_timing_fn = lrepo.get_timing_history
    if list_sim_fn is None:
        from app.data import sim_repository as srepo
        list_sim_fn = srepo.list_cycles

    positions: List[Tuple[str, str, Dict[str, Any]]] = []
    for c in get_cycles_fn(include_closed=False):
        sk, title, fields = map_position_row(c)
        positions.append((sk, title, enrich_position_fields(fields, c)))

    hist = get_timing_fn(page=1, page_size=min(max(touch_limit, 1), 100))
    touches = [map_touch_row(r) for r in (hist.get("items") or [])]
    sims = [map_sim_row(c) for c in list_sim_fn(include_closed=False, limit=200)]
    return {"positions": positions, "touches": touches, "sim": sims}


def run_status_digest(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
    dry_run: bool = False,
    send_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """生成并推送状态摘要。

    force=True:忽略 enabled / 日去重(手动 API)。
    dry_run=True:不发 TG,只返回 preview。
    失败只记日志,返回 ok=False。
    """
    out: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "reason": "",
        "sent_count": 0,
        "chunks": 0,
        "preview": "",
        "messages": [],
    }
    try:
        if cfg is None:
            try:
                from app.api.leaps import _load_config
                cfg = _load_config()
            except Exception:
                cfg = {}

        sd = get_status_digest_cfg(cfg)
        if not force and not sd.get("enabled"):
            out["skipped"] = True
            out["reason"] = "disabled"
            out["ok"] = True
            return out

        rows = collect_status_rows(cfg)
        touch_limit = int(sd.get("touch_limit") or 10)
        premiums = fetch_premium_totals()
        text = build_status_digest_text(
            rows,
            touch_limit=touch_limit,
            premium_month=premiums["premium_month"],
            premium_total=premiums["premium_total"],
        )
        chunks = chunk_telegram_text(text)
        out["preview"] = chunks[0] if chunks else text
        out["messages"] = chunks
        out["chunks"] = len(chunks)
        out["counts"] = {
            "positions": len(rows.get("positions") or []),
            "touches": min(len(rows.get("touches") or []), touch_limit),
            "sim": len(rows.get("sim") or []),
        }
        out["premium"] = premiums

        if dry_run:
            out["ok"] = True
            out["reason"] = "dry_run"
            return out

        if send_fn is None:
            from app.services.alert_engine import send_and_log

            def send_fn(body: str, **kwargs: Any) -> Dict[str, Any]:
                return send_and_log(body, **kwargs)

        sent = 0
        results = []
        for i, body in enumerate(chunks):
            r = send_fn(
                body,
                category="status_digest",
                fingerprint=f"status_digest:{datetime.now(SHANGHAI).date().isoformat()}:{i}",
                title="status_digest",
                meta={
                    "chunk": i + 1,
                    "chunks": len(chunks),
                    "counts": out.get("counts"),
                },
                dry_run=False,
                cfg=cfg,
            )
            results.append(r)
            if r.get("sent"):
                sent += 1
        out["sent_count"] = sent
        out["results"] = results
        out["ok"] = sent > 0 or (not chunks)
        if sent == 0:
            out["reason"] = (results[0].get("reason") if results else "send_failed") or "send_failed"
        else:
            out["reason"] = "ok"
        return out
    except Exception as e:
        logger.warning("status digest failed: %s", e)
        out["reason"] = str(e)
        out["ok"] = False
        return out


def _should_run_daily(sd: Dict[str, Any], now_local: datetime) -> bool:
    """Asia/Shanghai 本地 hour 到达且今日未推。"""
    try:
        hour = int(sd.get("status_digest_hour") if sd.get("status_digest_hour") is not None else 8)
    except (TypeError, ValueError):
        hour = 8
    if now_local.hour < hour:
        return False
    today = now_local.date().isoformat()
    try:
        from app.data.wheel_repository import get_kv
        return get_kv(KV_SENT_DATE) != today
    except Exception:
        return True


def mark_daily_sent(now_local: Optional[datetime] = None) -> None:
    now_local = now_local or datetime.now(SHANGHAI)
    try:
        from app.data.wheel_repository import set_kv
        set_kv(KV_SENT_DATE, now_local.date().isoformat())
    except Exception as e:
        logger.warning("status_digest mark sent failed: %s", e)


def status_digest_loop() -> None:
    """后台循环:
    - status_digest_minutes > 0 → 按分钟间隔推(不受日去重,但仍需 enabled)
    - 否则 → 每天 Asia/Shanghai status_digest_hour 推一次
    enabled=false 时休眠等待;失败只记日志。
    """
    time.sleep(160)  # 错开 Notion/Sheets/告警
    while True:
        sleep_s = 300
        try:
            from app.api.leaps import _load_config
            cfg = _load_config()
            sd = get_status_digest_cfg(cfg)
            try:
                minutes = float(sd.get("status_digest_minutes") or 0)
            except (TypeError, ValueError):
                minutes = 0.0

            if not sd.get("enabled"):
                time.sleep(300)
                continue

            if minutes > 0:
                sleep_s = max(60, int(minutes * 60))
                out = run_status_digest(cfg, force=False)
                if out.get("sent_count"):
                    logger.info(
                        "status digest interval sent=%s chunks=%s counts=%s",
                        out.get("sent_count"), out.get("chunks"), out.get("counts"),
                    )
                time.sleep(sleep_s)
                continue

            # 日推模式:每分钟检查一次钟点
            sleep_s = 60
            now_local = datetime.now(SHANGHAI)
            if _should_run_daily(sd, now_local):
                out = run_status_digest(cfg, force=False)
                if out.get("sent_count"):
                    mark_daily_sent(now_local)
                    logger.info(
                        "status digest daily sent=%s chunks=%s counts=%s",
                        out.get("sent_count"), out.get("chunks"), out.get("counts"),
                    )
                elif out.get("ok") and out.get("reason") in ("not_configured", "channel_silent"):
                    # 未配置 TG 也记已尝试,避免刷日志;手动可再 force
                    mark_daily_sent(now_local)
                elif out.get("skipped"):
                    pass
                else:
                    # 发送失败不 mark,下一分钟重试
                    logger.warning("status digest daily push incomplete: %s", out.get("reason"))
        except Exception as e:
            logger.warning("status digest loop failed: %s", e)
            sleep_s = 300
        time.sleep(sleep_s)
