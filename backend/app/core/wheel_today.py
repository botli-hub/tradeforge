"""今日一页聚合:必须处理 / 可做机会 / 资金 / 指派后 / 事件 / 相关。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHECK_CACHE_KEY = "open_positions_cache_v1"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_positions_cache(payload: Dict[str, Any]) -> None:
    try:
        from app.data.wheel_repository import set_kv
        blob = {
            "saved_at": _now_iso(),
            "data": payload,
        }
        set_kv(_CHECK_CACHE_KEY, json.dumps(blob, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning("save positions cache: %s", e)


def load_positions_cache(max_age_min: float = 120) -> Optional[Dict[str, Any]]:
    try:
        from app.data.wheel_repository import get_kv
        raw = get_kv(_CHECK_CACHE_KEY)
        if not raw:
            return None
        blob = json.loads(raw)
        saved = blob.get("saved_at")
        if not saved:
            return None
        age = (datetime.now() - datetime.fromisoformat(str(saved)[:19])).total_seconds() / 60
        if age > max_age_min:
            blob["stale"] = True
            blob["age_minutes"] = round(age, 1)
        else:
            blob["stale"] = age > 15  # 15 分钟以上标「可能旧」
            blob["age_minutes"] = round(age, 1)
        return blob
    except Exception:
        return None


def us_session_phase(now: Optional[datetime] = None) -> str:
    """粗分: pre | open | after | closed。按美东近似用本地−不足时用本地钟点启发式。

    简化:本地 21:30–04:00 当 open(夏令常见),否则用配置或 closed。
    更稳妥:用 weekday + UTC 小时判断美股 RTH。
    """
    now = now or datetime.now()
    # 用 UTC 判美东 RTH 约 14:30–21:00 UTC(夏) / 15:30–22:00(冬) — 取宽松 14–21
    try:
        utc = datetime.utcnow()
    except Exception:
        utc = now
    if utc.weekday() >= 5:
        return "closed"
    h, m = utc.hour, utc.minute
    mins = h * 60 + m
    # 预:13:00–14:30 UTC; 盘中 14:30–21:00; 盘后 21:00–01:00
    if 13 * 60 <= mins < 14 * 60 + 30:
        return "pre"
    if 14 * 60 + 30 <= mins < 21 * 60:
        return "open"
    if 21 * 60 <= mins or mins < 2 * 60:
        return "after"
    return "closed"


def event_calendar(days: int = 21) -> List[Dict[str, Any]]:
    """财报近似 + 手动 event_blocks。"""
    from app.data import wheel_repository as repo
    from app.data.database import get_db

    events: List[Dict[str, Any]] = []
    today = date.today()
    # event blocks
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM wheel_event_blocks ORDER BY event_date"
            ).fetchall()
            for r in rows:
                d = dict(r)
                ed = str(d.get("event_date") or "")[:10]
                try:
                    ed_d = date.fromisoformat(ed)
                except Exception:
                    continue
                delta = (ed_d - today).days
                if 0 <= delta <= days:
                    events.append({
                        "date": ed,
                        "symbol": d.get("symbol") or "*",
                        "label": d.get("label") or "事件封锁",
                        "kind": "block",
                        "days": delta,
                    })
        finally:
            conn.close()
    except Exception:
        pass

    # earnings (Finnhub, 有 key 才有数据)
    try:
        from app.core.earnings import get_next_earnings
        for t in repo.get_targets():
            if not t.get("enabled"):
                continue
            sym = t["symbol"]
            ed = get_next_earnings(sym)
            if not ed:
                continue
            try:
                ed_d = date.fromisoformat(str(ed)[:10])
            except Exception:
                continue
            delta = (ed_d - today).days
            if 0 <= delta <= days:
                events.append({
                    "date": str(ed)[:10],
                    "symbol": sym,
                    "label": "财报",
                    "kind": "earnings",
                    "days": delta,
                })
    except Exception:
        pass

    events.sort(key=lambda x: (x.get("days", 99), x.get("symbol") or ""))
    return events


def concentration_warnings(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """高相关 + 同 sector 集中 Put。"""
    from app.data import wheel_repository as repo
    from app.core.wheel_portfolio import correlation_matrix, portfolio_overview

    cfg = cfg or {}
    pcfg = cfg.get("wheel_portfolio") or {}
    thr = float(pcfg.get("high_corr_threshold") or 0.70)
    overview = portfolio_overview(
        total_equity=float(pcfg["total_equity"]) if pcfg.get("total_equity") else None,
        max_portfolio_pct=float(pcfg.get("max_portfolio_pct", 0.80)),
        max_symbol_pct=float(pcfg.get("max_symbol_pct", 0.25)),
    )
    corr = correlation_matrix()
    high = [p for p in (corr.get("high_corr") or []) if (p.get("corr") or 0) >= thr]

    # 在场 CSP 的相关叠加
    csp_syms = {
        c["symbol"]
        for c in repo.get_cycles(include_closed=False)
        if c["status"] == "CSP_OPEN"
    }
    stacked = []
    for p in high:
        if p["a"] in csp_syms and p["b"] in csp_syms:
            stacked.append({**p, "both_csp": True})
        elif p["a"] in csp_syms or p["b"] in csp_syms:
            stacked.append({**p, "both_csp": False})

    # sector
    by_sector: Dict[str, List[str]] = {}
    for t in repo.get_targets():
        if not t.get("enabled"):
            continue
        sec = (t.get("sector") or "未分类").strip() or "未分类"
        by_sector.setdefault(sec, []).append(t["symbol"])
    sector_heavy = [
        {"sector": s, "symbols": syms, "n": len(syms)}
        for s, syms in by_sector.items()
        if len(syms) >= 3 and s != "未分类"
    ]

    warns: List[str] = []
    for p in stacked:
        if p.get("both_csp"):
            warns.append(f"双 Put 高相关 {p['a']}/{p['b']} ρ={p['corr']}")
    for s in sector_heavy:
        warns.append(f"板块集中 {s['sector']}: {','.join(s['symbols'][:5])}")

    return {
        "high_corr": high[:15],
        "csp_corr_stack": stacked[:10],
        "sector_heavy": sector_heavy,
        "warnings": warns[:12],
        "utilization_pct": overview.get("utilization_pct"),
        "idle_cash": overview.get("idle_cash"),
        "equity": overview.get("equity"),
        "over_portfolio": overview.get("over_portfolio"),
    }


def try_buying_power() -> Optional[Dict[str, Any]]:
    """若交易账户已连接,取 buying_power。"""
    try:
        from app.api import trading as trading_api
        adapter = getattr(trading_api, "_trading_adapter", None)
        if adapter is None:
            return None
        acc = adapter.query_account()
        if not acc:
            return None
        return {
            "buying_power": acc.get("buying_power"),
            "cash": acc.get("cash") or acc.get("balance"),
            "source": "trading_account",
        }
    except Exception as e:
        logger.debug("buying_power skip: %s", e)
        return None


def build_today(
    host: str = "127.0.0.1",
    port: int = 11111,
    *,
    refresh_positions: bool = True,
) -> Dict[str, Any]:
    """聚合今日看板。"""
    from app.api.leaps import _load_config
    from app.core.wheel_post_assign import post_assign_queue

    cfg = _load_config()
    session = us_session_phase()
    stale = False
    age_min = None
    positions: Dict[str, Any] = {"items": []}
    pos_error = None

    if refresh_positions:
        try:
            from app.api.wheel import check_open_positions_core
            positions = check_open_positions_core(host, port)
            save_positions_cache(positions)
        except Exception as e:
            pos_error = str(e)
            cached = load_positions_cache(max_age_min=24 * 60)
            if cached and cached.get("data"):
                positions = cached["data"]
                stale = True
                age_min = cached.get("age_minutes")
            else:
                positions = {"items": [], "error": pos_error}
    else:
        cached = load_positions_cache(max_age_min=24 * 60)
        if cached and cached.get("data"):
            positions = cached["data"]
            stale = bool(cached.get("stale"))
            age_min = cached.get("age_minutes")

    items = positions.get("items") or []
    must = [
        i for i in items
        if (i.get("action_priority") or 9) <= 3
        or (i.get("action_code") or "") in ("PREPARE_ASSIGN", "ROLL_ADJUST", "CLOSE", "ROLL", "REPLACE")
    ]
    must.sort(key=lambda x: (x.get("action_priority") or 9))

    # 机会(缓存,不强制重扫)
    opps_summary = {}
    primary = []
    try:
        from app.core.wheel_opportunities import build_opportunities
        data = build_opportunities(host, port, refresh_pool=False, run_pool_if_empty=False)
        opps_summary = data.get("summary") or {}
        primary = (data.get("primary_picks") or [])[:5]
        # 可执行过滤标注
        for p in primary:
            p["executable"] = _is_executable_opp(p, opps_summary)
    except Exception as e:
        opps_summary = {"error": str(e)}

    post_q = post_assign_queue()
    events = event_calendar(21)
    conc = concentration_warnings(cfg)
    bp = try_buying_power()
    pf = positions.get("portfolio_context") or {}

    capital = {
        "utilization_pct": pf.get("utilization_pct") or conc.get("utilization_pct"),
        "idle_cash": pf.get("idle_cash") or conc.get("idle_cash"),
        "equity": pf.get("equity") or conc.get("equity"),
        "capital_tight": pf.get("capital_tight"),
        "portfolio_put_blocked": pf.get("portfolio_put_blocked") or opps_summary.get("portfolio_put_blocked"),
        "buying_power": (bp or {}).get("buying_power"),
        "bp_source": (bp or {}).get("source"),
    }

    return {
        "as_of": _now_iso(),
        "session": session,
        "stale": stale,
        "stale_age_minutes": age_min,
        "positions_error": pos_error,
        "must_manage": must[:15],
        "must_count": len(must),
        "primary_opens": primary,
        "post_assign": post_q[:10],
        "events": events[:20],
        "concentration": conc,
        "capital": capital,
        "portfolio_context": pf,
        "opp_summary": {
            "actionable": opps_summary.get("actionable_count") or opps_summary.get("actionable"),
            "put_blocked": capital.get("portfolio_put_blocked"),
        },
        "headline": _headline(must, post_q, primary, capital, stale),
    }


def _is_executable_opp(p: Dict[str, Any], summary: Dict[str, Any]) -> bool:
    if p.get("actionable") is False:
        return False
    if summary.get("portfolio_put_blocked") and (p.get("side") or "").upper() == "PUT":
        return False
    if p.get("exceeds_capital"):
        return False
    sp = p.get("spread_pct")
    if sp is not None:
        try:
            if float(sp) > 8:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _headline(must, post_q, primary, capital, stale) -> str:
    parts = []
    if stale:
        parts.append("行情缓存")
    if must:
        parts.append(f"{len(must)}项必管")
    if post_q:
        parts.append(f"{len(post_q)}笔待挂CC")
    exec_n = sum(1 for p in primary if p.get("executable"))
    if exec_n:
        parts.append(f"{exec_n}笔可开")
    if capital.get("portfolio_put_blocked"):
        parts.append("停新Put")
    if capital.get("capital_tight"):
        parts.append("资金紧")
    return " · ".join(parts) if parts else "今日清闲"
