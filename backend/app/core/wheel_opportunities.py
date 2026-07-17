"""统一可交易机会流

合流:
  - 全池扫描(质量分)
  - 开仓时机/时机历史(触线)
分类:
  - dual: 触线 ∩ 有质量分
  - timing: 仅触线
  - score: 仅打分
可做 actionable:
  - 无硬红线(超资金/Put财报硬过滤命中/Put 趋势 DOWN 且非 dual 强信号)
  - 且 (dual | 触线 READY/STRONG | 纯高分达阈值)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def _norm_code(code: Optional[str]) -> str:
    c = (code or "").strip().upper()
    if c.startswith("US."):
        c = c[3:]
    return c


def _parse_contract(code: Optional[str]) -> Dict[str, Any]:
    """从 Futu 合约码解析 strike/到期/DTE/方向。
    例: US.MSFT260821P460000 → strike=460, expiry=2026-08-21, side=PUT
    """
    from datetime import date as _date
    out: Dict[str, Any] = {
        "strike": None, "expiry": None, "expiry_raw": None,
        "dte": None, "side": None, "underlying": None,
    }
    if not code:
        return out
    try:
        from app.core.leaps_monitor import _parse_futu_contract, _dte
        und, exp_raw, strike, opt = _parse_futu_contract(code)
        if und:
            out["underlying"] = und
        if exp_raw:
            out["expiry_raw"] = exp_raw
            try:
                out["expiry"] = datetime.strptime("20" + exp_raw, "%Y%m%d").date().isoformat()
            except Exception:
                out["expiry"] = exp_raw
            out["dte"] = _dte(exp_raw)
        if strike and strike > 0:
            out["strike"] = float(strike)
        if opt in ("P", "C"):
            out["side"] = "PUT" if opt == "P" else "CALL"
    except Exception:
        pass
    return out


def _fill_from_code(item: Dict[str, Any]) -> Dict[str, Any]:
    """用合约码补全空的 strike/expiry/dte/side;有 bid+strike+dte 时补年化。

    注意: timing.trigger_price 是合约 K 线 last/high(触线点),不是实时 bid。
    深 ITM 时 last 可接近内在价值(如 89),绝不能当作卖出参考权利金去算年化。
    """
    parsed = _parse_contract(item.get("contract_code"))
    if item.get("strike") is None and parsed.get("strike") is not None:
        item["strike"] = parsed["strike"]
    if not item.get("expiry") and parsed.get("expiry"):
        item["expiry"] = parsed["expiry"]
    if item.get("dte") is None and parsed.get("dte") is not None:
        item["dte"] = parsed["dte"]
    if not item.get("side") and parsed.get("side"):
        item["side"] = parsed["side"]
    # 年化: 仅用真实 bid / premium_used
    prem = item.get("premium_used") or item.get("bid")
    strike = item.get("strike")
    dte = item.get("dte")
    if item.get("annualized") is None and prem and strike and dte and strike > 0 and dte > 0:
        try:
            prem_f = float(prem)
            strike_f = float(strike)
            # 权利金 > 25% strike 多半是深 ITM last 误入,不算年化以免刷屏
            if prem_f / strike_f <= 0.25:
                item["annualized"] = round(prem_f / strike_f * 365 / int(dte) * 100, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # 展示用合约简码
    code = item.get("contract_code") or ""
    item["contract_short"] = _norm_code(code) if code else None
    # 显式标记:有无真实买价(前端/备忘用)
    bid = item.get("bid")
    item["has_live_bid"] = bool(bid is not None and float(bid or 0) > 0)
    return item


def _opp_key(symbol: str, side: str, strike: Any, expiry: Any, code: Optional[str] = None) -> str:
    if code:
        return f"C:{_norm_code(code)}"
    return f"K:{symbol}|{side}|{strike}|{str(expiry or '')[:10]}"


def _strength_from_row(ema_type: Optional[str], iv_rank: Optional[float], min_iv: float) -> str:
    """机会流强度:触线本身即 READY;EMA200+高 IV → STRONG。

    注意:TG 推送仍用 leaps_monitor.signal_strength(可 strong_only);
    这里放宽是避免「历史有触线、机会页全进观察且被隐藏」。
    """
    iv = iv_rank or 0
    if ema_type == "EMA200" and iv >= min_iv:
        return "STRONG"
    if ema_type in ("EMA200", "EMA50"):
        return "READY"
    if iv >= min_iv:
        return "READY"
    return "WATCH"


def _symbol_context(symbol: str) -> Dict[str, Any]:
    from app.data import wheel_repository as wrepo

    t = wrepo.get_target(symbol) or {}
    usage = wrepo.get_capital_usage()["per_symbol"].get(symbol, {})
    committed = (usage.get("csp_collateral") or 0) + (usage.get("holding_cost") or 0)
    max_cap = t.get("max_capital") or 0
    headroom = (max_cap - committed) if max_cap > 0 else None
    cycles = wrepo.get_active_cycles(symbol)
    statuses = [c["status"] for c in cycles]
    stage = "IDLE"
    if any(s == "CC_OPEN" for s in statuses):
        stage = "CC_OPEN"
    elif any(s == "CSP_OPEN" for s in statuses):
        stage = "CSP_OPEN"
    elif any(s == "HOLDING" for s in statuses):
        stage = "HOLDING"
    elif statuses:
        stage = statuses[0]
    holding = next((c for c in cycles if c["status"] == "HOLDING"), None)
    return {
        "stage": stage,
        "headroom": round(headroom, 2) if headroom is not None else None,
        "max_capital": max_cap,
        "committed": round(committed, 2),
        "cost_basis": (holding or {}).get("cost_basis"),
        "floor_price": t.get("floor_price"),
        "enabled": bool(t.get("enabled", True)),
    }


def _red_flags(
    *,
    side: str,
    trend: Optional[str],
    covers_earnings: bool,
    exceeds_capital: bool,
    below_floor: bool,
    earnings_hard: bool,
    portfolio_stress: bool = False,
    iv_rank: Optional[float] = None,
    iv_low_threshold: float = 25.0,
) -> List[str]:
    flags = []
    if exceeds_capital:
        flags.append("超资金上限")
    if side == "PUT" and covers_earnings and earnings_hard:
        flags.append("覆盖财报")
    if side == "PUT" and trend == "DOWN":
        flags.append("趋势DOWN")
    if side == "PUT" and below_floor:
        flags.append("已入愿接区·指派风险升")
    if side == "PUT" and portfolio_stress:
        flags.append("组合压力高")
    if side == "PUT" and iv_rank is not None and iv_rank < iv_low_threshold:
        flags.append("IV低位")
    return flags


def _grade_actionable(
    source: str,
    strength: Optional[str],
    score: Optional[float],
    min_score: float,
    flags: List[str],
    dual_overrides_trend: bool = True,
) -> Tuple[str, bool]:
    """返回 (grade, actionable)。grade: dual|timing|score|blocked|watch

    硬阻断:超资金 / 覆盖财报 / 组合压力高(新 Put)
    「已入愿接区」「IV低位」仅软标签或软降档,不单独 hard block。
    """
    hard = [f for f in flags if f in ("超资金上限", "覆盖财报", "组合压力高")]
    soft_all = [f for f in flags if f not in hard]
    # 不参与降档的软标签(仍出现在 flags 里给前端角标)
    soft_demote = [f for f in soft_all if f not in (
        "已入愿接区·指派风险升", "低于接货底线", "IV低位",
    )]
    if hard:
        return "blocked", False
    if source == "dual":
        if soft_demote and not dual_overrides_trend:
            return "watch", False
        if "趋势DOWN" in soft_demote and strength == "WATCH":
            return "watch", False
        return "dual", True
    if source == "timing":
        if strength in ("STRONG", "READY") and not soft_demote:
            return "timing", True
        if strength in ("STRONG", "READY") and soft_demote:
            # 趋势DOWN 等:仍展示为 watch,但保留触线
            return "watch", False
        return "watch", False
    # score only: IV 过低时降观察(卖方权利金薄)
    if score is not None and score >= min_score and not soft_demote:
        if "IV低位" in soft_all:
            return "watch", False
        return "score", True
    if score is not None and score >= min_score:
        return "watch", False
    return "watch", False


def _portfolio_put_stress(cfg: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """是否应暂停新开 Put:利用率超限 或 行权压力过高。"""
    from app.core.wheel_portfolio import portfolio_overview

    pcfg = cfg.get("wheel_portfolio", {}) or {}
    pos = cfg.get("wheel_position", {}) or {}
    overview = portfolio_overview(
        total_equity=float(pcfg["total_equity"]) if pcfg.get("total_equity") else None,
        max_portfolio_pct=float(pcfg.get("max_portfolio_pct", 0.80)),
        max_symbol_pct=float(pcfg.get("max_symbol_pct", 0.25)),
    )
    stress = float(overview.get("assignment_stress") or 0)
    committed = float(overview.get("total_committed") or 0)
    equity = overview.get("equity")
    # 与前端 stressBlocksNewPuts 均衡档 1.5x 对齐;可用配置覆盖
    ratio = float(pos.get("stress_put_block_ratio", 1.5) or 1.5)
    base = max(committed, float(equity or 0) * 0.3) if equity else committed
    stress_block = bool(stress > 0 and base > 0 and stress >= base * ratio)
    over_pf = bool(overview.get("over_portfolio"))
    blocked = stress_block or over_pf
    meta = {
        "portfolio_put_blocked": blocked,
        "assignment_stress": stress,
        "utilization_pct": overview.get("utilization_pct"),
        "over_portfolio": over_pf,
        "stress_block": stress_block,
        "equity": equity,
    }
    return blocked, meta


def build_opportunities(
    host: str = "127.0.0.1",
    port: int = 11111,
    *,
    refresh_pool: bool = False,
    run_pool_if_empty: bool = True,
    timing_limit: int = 40,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    from app.api.leaps import _load_config
    from app.core.wheel_score import get_scan_cfg
    from app.data import leaps_repository as lrepo
    from app.services import wheel_scanner

    cfg = _load_config()
    scan_cfg = get_scan_cfg(cfg)
    timing_cfg = cfg.get("wheel_timing", {}) or {}
    min_iv = float(timing_cfg.get("push_min_iv_rank", 50) or 0)
    earnings_hard = bool(scan_cfg.get("earnings_hard_filter", True))
    iv_low_thr = float(scan_cfg.get("iv_low_threshold", 25) or 25)
    put_stress, portfolio_meta = _portfolio_put_stress(cfg)
    # 可做分数阈值:配置或全池中位数*0.6
    min_score_cfg = scan_cfg.get("opportunity_min_score")
    if min_score is not None:
        score_threshold = float(min_score)
    elif min_score_cfg is not None:
        score_threshold = float(min_score_cfg)
    else:
        score_threshold = 0.0  # 稍后用池内分布填充

    def _opend_alive(h: str, p: int, timeout: float = 0.4) -> bool:
        import socket
        try:
            with socket.create_connection((h, int(p)), timeout=timeout):
                return True
        except OSError:
            return False

    pool_meta: Dict[str, Any] = {"scanned_at": None, "from_cache": False, "error": None}
    pool_opps: List[Dict[str, Any]] = []
    last = wheel_scanner.get_last_result()
    if last and not refresh_pool:
        pool_opps = list(last.get("opportunities") or [])
        pool_meta = {
            "scanned_at": last.get("scanned_at"),
            "from_cache": True,
            "targets_scanned": last.get("targets_scanned"),
            "total_found": last.get("total_found"),
            "errors": last.get("errors") or [],
            "skipped": last.get("skipped") or [],
        }
    elif run_pool_if_empty or refresh_pool:
        if not _opend_alive(host, port):
            pool_meta["error"] = f"OpenD 未连接({host}:{port}),跳过全池扫描;仍合流触线时机"
            if last:
                pool_opps = list(last.get("opportunities") or [])
                pool_meta["scanned_at"] = last.get("scanned_at")
                pool_meta["from_cache"] = True
                pool_meta["stale_fallback"] = True
        else:
            try:
                last = wheel_scanner.run_scan(host, port, force_refresh=refresh_pool)
                pool_opps = list(last.get("opportunities") or [])
                pool_meta = {
                    "scanned_at": last.get("scanned_at"),
                    "from_cache": False,
                    "targets_scanned": last.get("targets_scanned"),
                    "total_found": last.get("total_found"),
                    "errors": last.get("errors") or [],
                    "skipped": last.get("skipped") or [],
                }
            except Exception as e:
                pool_meta["error"] = str(e)
                if last:
                    pool_opps = list(last.get("opportunities") or [])
                    pool_meta["scanned_at"] = last.get("scanned_at")
                    pool_meta["from_cache"] = True
                    pool_meta["stale_fallback"] = True

    scores = [float(o.get("score") or 0) for o in pool_opps if o.get("score") is not None]
    if score_threshold <= 0 and scores:
        scores_sorted = sorted(scores)
        mid = scores_sorted[len(scores_sorted) // 2]
        score_threshold = max(mid * 0.55, 1.0)
    elif score_threshold <= 0:
        score_threshold = 10.0  # 无池数据时的宽松默认

    pool_by_code: Dict[str, Dict] = {}
    pool_by_sk: Dict[str, Dict] = {}
    for o in pool_opps:
        code = _norm_code(o.get("contract_code"))
        if code:
            pool_by_code[code] = o
        sk = f"{o.get('symbol')}|{o.get('side')}|{o.get('strike')}|{str(o.get('expiry') or '')[:10]}"
        pool_by_sk[sk] = o

    # 时机:历史表优先(字段更全),信号表补充
    hist = lrepo.get_timing_history(page=1, page_size=timing_limit)
    timing_rows: List[Dict[str, Any]] = list(hist.get("items") or [])
    recent_sigs = lrepo.get_recent_signals(
        limit=timing_limit, levels=["WHEEL_PUT", "WHEEL_CALL"]
    )

    merged: Dict[str, Dict[str, Any]] = {}

    def ensure_ctx(symbol: str) -> Dict[str, Any]:
        return _symbol_context(symbol)

    def flags_for(
        *,
        side: str,
        trend: Optional[str],
        covers: bool,
        exceeds: bool,
        below: bool,
        iv_rank: Optional[float] = None,
    ) -> List[str]:
        return _red_flags(
            side=side,
            trend=trend,
            covers_earnings=covers,
            exceeds_capital=exceeds,
            below_floor=below,
            earnings_hard=earnings_hard,
            portfolio_stress=put_stress and side == "PUT",
            iv_rank=iv_rank,
            iv_low_threshold=iv_low_thr,
        )

    # 1) timing history
    for h in timing_rows:
        symbol = h.get("symbol") or ""
        side = h.get("side") or "PUT"
        code = h.get("contract_code")
        key = _opp_key(symbol, side, h.get("strike"), h.get("expiry"), code)
        pool_o = pool_by_code.get(_norm_code(code)) or pool_by_sk.get(
            f"{symbol}|{side}|{h.get('strike')}|{str(h.get('expiry') or '')[:10]}"
        )
        strength = _strength_from_row(h.get("ema_type"), h.get("iv_rank"), min_iv)
        source = "dual" if pool_o else "timing"
        trend = (pool_o or {}).get("trend")
        covers = bool((pool_o or {}).get("covers_earnings"))
        exceeds = bool((pool_o or {}).get("exceeds_capital"))
        below = bool(h.get("below_floor"))
        ivr = h.get("iv_rank") if h.get("iv_rank") is not None else (pool_o or {}).get("iv_rank")
        flags = flags_for(
            side=side, trend=trend, covers=covers, exceeds=exceeds, below=below, iv_rank=ivr,
        )
        score = (pool_o or {}).get("score")
        grade, actionable = _grade_actionable(
            source, strength, float(score) if score is not None else None,
            score_threshold, flags,
        )
        ctx = ensure_ctx(symbol)
        item = {
            "id": key,
            "source": source,
            "grade": grade,
            "actionable": actionable,
            "symbol": symbol,
            "side": side,
            "contract_code": code,
            "strike": h.get("strike") if h.get("strike") is not None else (pool_o or {}).get("strike"),
            "expiry": (h.get("expiry") or (pool_o or {}).get("expiry") or "")[:10] or None,
            "dte": h.get("dte") if h.get("dte") is not None else (pool_o or {}).get("dte"),
            "delta": h.get("delta") if h.get("delta") is not None else (pool_o or {}).get("delta"),
            "bid": h.get("bid") if h.get("bid") is not None else (pool_o or {}).get("bid"),
            "premium_used": (pool_o or {}).get("premium_used"),
            "spread_pct": (pool_o or {}).get("spread_pct"),
            "annualized": h.get("annualized") if h.get("annualized") is not None else (pool_o or {}).get("annualized"),
            "score": score,
            "score_factors": (pool_o or {}).get("score_factors"),
            "pop": (pool_o or {}).get("pop"),
            "iv_rank": h.get("iv_rank") if h.get("iv_rank") is not None else (pool_o or {}).get("iv_rank"),
            "trend": trend,
            "covers_earnings": covers,
            "exceeds_capital": exceeds,
            "flags": flags,
            "timing": {
                "ema_type": h.get("ema_type"),
                "ema_value": h.get("ema_value"),
                "trigger_price": h.get("trigger_price"),
                "strength": strength,
                "times_triggered": h.get("times_triggered"),
                "last_seen": h.get("last_seen"),
                "below_floor": below,
            },
            "cycle_id": (pool_o or {}).get("cycle_id"),
            "context": ctx,
            "rank_boost": 0,
        }
        # dual 置顶加权
        if source == "dual":
            item["rank_boost"] = 1000
        elif strength == "STRONG":
            item["rank_boost"] = 400
        elif strength == "READY":
            item["rank_boost"] = 200
        merged[key] = item

    # 2) recent signals not in history merge (same code updates)
    for s in recent_sigs:
        symbol = s.get("symbol") or ""
        side = "CALL" if "CALL" in (s.get("signal_level") or "") else "PUT"
        code = s.get("contract_code")
        key = _opp_key(symbol, side, s.get("strike"), s.get("expiry"), code)
        if key in merged:
            # 刷新最近触达时间
            merged[key]["timing"] = merged[key].get("timing") or {}
            merged[key]["timing"]["last_seen"] = s.get("created_at") or merged[key]["timing"].get("last_seen")
            if not merged[key].get("source") == "dual":
                pool_o = pool_by_code.get(_norm_code(code))
                if pool_o:
                    merged[key]["source"] = "dual"
                    merged[key]["score"] = pool_o.get("score")
                    merged[key]["score_factors"] = pool_o.get("score_factors")
                    merged[key]["rank_boost"] = 1000
                    flags = merged[key].get("flags") or []
                    grade, actionable = _grade_actionable(
                        "dual",
                        (merged[key].get("timing") or {}).get("strength"),
                        float(pool_o.get("score") or 0),
                        score_threshold, flags,
                    )
                    merged[key]["grade"] = grade
                    merged[key]["actionable"] = actionable
            continue
        pool_o = pool_by_code.get(_norm_code(code))
        strength = _strength_from_row(s.get("ema_type"), s.get("iv_rank"), min_iv)
        source = "dual" if pool_o else "timing"
        trend = (pool_o or {}).get("trend")
        covers = bool((pool_o or {}).get("covers_earnings"))
        exceeds = bool((pool_o or {}).get("exceeds_capital"))
        below = bool(s.get("below_floor")) if "below_floor" in s else False
        ivr = s.get("iv_rank") if s.get("iv_rank") is not None else (pool_o or {}).get("iv_rank")
        flags = flags_for(
            side=side, trend=trend, covers=covers, exceeds=exceeds, below=below, iv_rank=ivr,
        )
        score = (pool_o or {}).get("score")
        grade, actionable = _grade_actionable(
            source, strength, float(score) if score is not None else None,
            score_threshold, flags,
        )
        merged[key] = {
            "id": key,
            "source": source,
            "grade": grade,
            "actionable": actionable,
            "symbol": symbol,
            "side": side,
            "contract_code": code,
            "strike": s.get("strike") or (pool_o or {}).get("strike"),
            "expiry": (str(s.get("expiry") or (pool_o or {}).get("expiry") or "")[:10]) or None,
            "dte": s.get("dte") or (pool_o or {}).get("dte"),
            "delta": s.get("delta") if s.get("delta") is not None else (pool_o or {}).get("delta"),
            "bid": s.get("bid") if s.get("bid") is not None else (pool_o or {}).get("bid"),
            "premium_used": (pool_o or {}).get("premium_used"),
            "spread_pct": (pool_o or {}).get("spread_pct"),
            "annualized": s.get("annualized") or (pool_o or {}).get("annualized"),
            "score": score,
            "score_factors": (pool_o or {}).get("score_factors"),
            "pop": (pool_o or {}).get("pop"),
            "iv_rank": s.get("iv_rank") if s.get("iv_rank") is not None else (pool_o or {}).get("iv_rank"),
            "trend": trend,
            "covers_earnings": covers,
            "exceeds_capital": exceeds,
            "flags": flags,
            "timing": {
                "ema_type": s.get("ema_type"),
                "ema_value": s.get("ema_value"),
                "trigger_price": s.get("trigger_price"),
                "strength": strength,
                "times_triggered": 1,
                "last_seen": s.get("created_at"),
                "below_floor": below,
            },
            "cycle_id": (pool_o or {}).get("cycle_id"),
            "context": ensure_ctx(symbol),
            "rank_boost": 1000 if source == "dual" else (400 if strength == "STRONG" else 200 if strength == "READY" else 50),
        }

    # 3) score-only from pool
    for o in pool_opps:
        symbol = o.get("symbol") or ""
        side = o.get("side") or "PUT"
        code = o.get("contract_code")
        key = _opp_key(symbol, side, o.get("strike"), o.get("expiry"), code)
        if key in merged:
            # 已是 timing/dual,补全字段
            m = merged[key]
            for f in ("score", "score_factors", "pop", "spread_pct", "premium_used", "cycle_id", "otm_pct"):
                if m.get(f) is None and o.get(f) is not None:
                    m[f] = o.get(f)
            if m.get("source") == "timing":
                m["source"] = "dual"
                m["rank_boost"] = 1000
                # recompute flags with pool data
                flags = flags_for(
                    side=side,
                    trend=o.get("trend"),
                    covers=bool(o.get("covers_earnings")),
                    exceeds=bool(o.get("exceeds_capital")),
                    below=bool((m.get("timing") or {}).get("below_floor")),
                    iv_rank=m.get("iv_rank") if m.get("iv_rank") is not None else o.get("iv_rank"),
                )
                m["flags"] = flags
                m["trend"] = o.get("trend")
                m["covers_earnings"] = bool(o.get("covers_earnings"))
                m["exceeds_capital"] = bool(o.get("exceeds_capital"))
                grade, actionable = _grade_actionable(
                    "dual", (m.get("timing") or {}).get("strength"),
                    float(o.get("score") or 0), score_threshold, flags,
                )
                m["grade"] = grade
                m["actionable"] = actionable
            continue

        flags = flags_for(
            side=side,
            trend=o.get("trend"),
            covers=bool(o.get("covers_earnings")),
            exceeds=bool(o.get("exceeds_capital")),
            below=False,
            iv_rank=o.get("iv_rank"),
        )
        score = o.get("score")
        grade, actionable = _grade_actionable(
            "score", None, float(score) if score is not None else None,
            score_threshold, flags,
        )
        merged[key] = {
            "id": key,
            "source": "score",
            "grade": grade,
            "actionable": actionable,
            "symbol": symbol,
            "side": side,
            "contract_code": code,
            "strike": o.get("strike"),
            "expiry": str(o.get("expiry") or "")[:10] or None,
            "dte": o.get("dte"),
            "delta": o.get("delta"),
            "bid": o.get("bid"),
            "premium_used": o.get("premium_used"),
            "spread_pct": o.get("spread_pct"),
            "annualized": o.get("annualized"),
            "score": score,
            "score_factors": o.get("score_factors"),
            "pop": o.get("pop"),
            "iv_rank": o.get("iv_rank"),
            "trend": o.get("trend"),
            "covers_earnings": bool(o.get("covers_earnings")),
            "exceeds_capital": bool(o.get("exceeds_capital")),
            "flags": flags,
            "timing": None,
            "cycle_id": o.get("cycle_id"),
            "context": ensure_ctx(symbol),
            "rank_boost": 0,
        }

    items = [_fill_from_code(x) for x in merged.values()]

    # 同标的+同方向:仅展示最优 1 条(双满足/高分优先),其余折叠进 siblings
    # 先排序再折叠
    def _event_at(x: Dict[str, Any]) -> str:
        """最近事件时间:触线 last_seen 优先,否则空(打分项排后)。"""
        t = (x.get("timing") or {}).get("last_seen") or ""
        return str(t)

    def sort_key(x: Dict[str, Any]):
        # 时间倒序为主;可做略优先仍保留;再来源/分数
        grade_ord = {"dual": 0, "timing": 1, "score": 2, "watch": 3, "blocked": 4}.get(x.get("grade") or "", 5)
        act = 0 if x.get("actionable") else 1
        return (
            act,  # 可做在前
            _event_at(x) == "",  # 有时间的在前
            # 时间倒序:字符串 ISO 反序
            # 用负时间不好写,下面 items.sort reverse 对时间段
            grade_ord,
            -(x.get("rank_boost") or 0),
            -(x.get("score") or 0),
            -(x.get("annualized") or 0),
        )

    # 同 symbol|side 最多 N 条(默认 10);按质量排序,避免「最新 5 条」挤掉真正好的 strike
    max_per_sym_side = int(scan_cfg.get("opp_max_per_symbol_side", 10) or 10)
    strength_ord = {"STRONG": 0, "READY": 1, "WATCH": 2}

    def _group_rank_key(x: Dict[str, Any]):
        dte = x.get("dte")
        core = 0 if (dte is not None and 21 <= int(dte) <= 45) else 1
        st = (x.get("timing") or {}).get("strength") or ""
        return (
            0 if x.get("actionable") else 1,
            0 if x.get("source") == "dual" or x.get("grade") == "dual" else 1,
            strength_ord.get(st, 3),
            core,
            -(float(x.get("annualized") or 0)),
            -(float(x.get("score") or 0)),
            str(x.get("event_at") or ""),
        )

    by_sym_side: Dict[str, List[Dict]] = {}
    for it in items:
        it["event_at"] = _event_at(it) or None
        k = f"{it.get('symbol')}|{it.get('side')}"
        by_sym_side.setdefault(k, []).append(it)
    capped: List[Dict[str, Any]] = []
    primary_picks: List[Dict[str, Any]] = []
    for k, group in by_sym_side.items():
        group.sort(key=_group_rank_key)
        top = group[:max_per_sym_side]
        for i, it in enumerate(top):
            it["group_size"] = len(group)
            it["group_rank"] = i + 1
            # 同标的同方向最优一条 = 主推(先可做,再质量)
            it["is_top_pick"] = i == 0
            if i == 0:
                primary_picks.append(it)
        capped.extend(top)
    items = capped
    # 最终:可做优先 → 时间倒序 → 分
    items.sort(
        key=lambda x: (
            0 if x.get("actionable") else 1,
            str(x.get("event_at") or ""),
            float(x.get("score") or 0),
            float(x.get("annualized") or 0),
        ),
        reverse=False,
    )
    # 可做块内、非可做块内各自时间倒序
    act_items = [x for x in items if x.get("actionable")]
    rest_items = [x for x in items if not x.get("actionable")]
    act_items.sort(key=lambda x: str(x.get("event_at") or ""), reverse=True)
    rest_items.sort(key=lambda x: str(x.get("event_at") or ""), reverse=True)
    items = act_items + rest_items

    actionable = [x for x in items if x.get("actionable")]
    dual = [x for x in items if x.get("source") == "dual" or x.get("grade") == "dual"]
    watch = [x for x in items if x.get("grade") == "watch"]
    blocked = [x for x in items if x.get("grade") == "blocked"]
    idle_slots = []
    from app.data import wheel_repository as wrepo
    for t in wrepo.get_targets():
        if not t.get("enabled"):
            continue
        ctx = ensure_ctx(t["symbol"])
        if ctx.get("stage") in ("IDLE",) and (ctx.get("headroom") is None or (ctx.get("headroom") or 0) > 0):
            # 无 actionable put 机会
            has_put = any(
                i["symbol"] == t["symbol"] and i["side"] == "PUT" and i.get("actionable")
                for i in items
            )
            if not has_put:
                idle_slots.append({
                    "symbol": t["symbol"],
                    "headroom": ctx.get("headroom"),
                    "stage": ctx.get("stage"),
                })

    put_n = sum(1 for x in actionable if x["side"] == "PUT")
    call_n = sum(1 for x in actionable if x["side"] == "CALL")
    headline = (
        f"今日 {len(actionable)} 个可做({put_n} Put / {call_n} Call)"
        + (f", ★双满足 {len(dual)} 个" if dual else "")
        + (f", {len(idle_slots)} 个标的资金空档待填" if idle_slots else "")
    )
    if put_stress:
        headline = "⚠ 组合压力高,已暂停新开 Put · " + headline
    if actionable:
        top = actionable[0]
        why = []
        if top.get("source") == "dual":
            why.append("触线+高分")
        elif top.get("timing"):
            why.append(f"触{(top['timing'] or {}).get('ema_type')}")
        if top.get("score") is not None:
            why.append(f"分{top['score']}")
        headline += f"。优先看 {top['symbol']} {top['side']}" + (f"({' · '.join(why)})" if why else "")

    # 主推列表:每 symbol|side 一条,可做优先
    primary_sorted = sorted(
        primary_picks,
        key=lambda x: (
            0 if x.get("actionable") else 1,
            0 if x.get("source") == "dual" else 1,
            -(float(x.get("annualized") or 0)),
        ),
    )

    return {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "headline": headline,
        "portfolio": portfolio_meta,
        "primary_picks": primary_sorted[:12],
        "summary": {
            "actionable": len(actionable),
            "actionable_put": put_n,
            "actionable_call": call_n,
            "dual": len(dual),
            "watch": len(watch),
            "blocked": len(blocked),
            "total": len(items),
            "portfolio_put_blocked": put_stress,
            "idle_slots": len(idle_slots),
            "min_score_threshold": round(score_threshold, 2),
        },
        "idle_slots": idle_slots[:15],
        "items": items,
        "actionable_items": actionable,
        "pool": pool_meta,
        "rules": {
            "actionable": "无硬红线(超资金/Put财报/组合压力) 且 (双满足 | 触线READY/STRONG | 纯高分≥阈值)",
            "dual": "触线历史/信号 与 全池扫描合约匹配",
            "min_score_threshold": score_threshold,
            "earnings_hard_filter": earnings_hard,
            "portfolio_put_block": "利用率超限或行权压力≥committed×ratio 时停新 Put",
        },
    }
