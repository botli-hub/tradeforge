"""Wheel 策略 REST API"""
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.data import wheel_repository as repo
from app.data.wheel_repository import WheelError
from app.data.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 标的管理 ──────────────────────────────────────────────────────────────────

def _wheel_cfg() -> Dict[str, Any]:
    from app.api.leaps import _load_config
    return _load_config()


@router.get("/targets")
def list_targets():
    from datetime import datetime
    targets = repo.get_targets()
    # 附带全部活跃 cycle(支持同标的多轮并行) + 空转天数
    for t in targets:
        cycles = repo.get_active_cycles(t["symbol"])
        t["active_cycles"] = cycles
        # 空转:无在场合约且不持股(资金未在工作)
        working = any(c["status"] in ("CSP_OPEN", "CC_OPEN", "HOLDING") for c in cycles)
        idle_days = None
        if not working and t.get("enabled"):
            ref = repo.get_last_trade_time(t["symbol"]) or t.get("created_at")
            try:
                idle_days = max((datetime.now() - datetime.fromisoformat(ref)).days, 0)
            except Exception:
                idle_days = None
        t["idle_days"] = idle_days
        try:
            from app.core.volatility import brief_profile
            t["volatility_brief"] = brief_profile(t["symbol"])
        except Exception:
            t["volatility_brief"] = None
    return targets


@router.get("/targets/candidates")
def target_candidates():
    """股票池美股/港股候选(含未启用)。

    返回全部 US/HK 池内标的,附带:
      - enabled: 是否在股票池启用
      - in_wheel: 是否已是 wheel 标的(前端下拉禁用,避免「美股消失」的错觉)
    不再只返回 enabled=1 且未添加的,否则美股全进 wheel 后下拉只剩港股。
    """
    existing = {t["symbol"].upper() for t in repo.get_targets()}
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT symbol, name, market, enabled
            FROM stocks
            WHERE upper(market) IN ('US', 'HK')
               OR market IN ('美股', '港股')
            ORDER BY
              CASE upper(market)
                WHEN 'US' THEN 0
                WHEN '美股' THEN 0
                WHEN 'HK' THEN 1
                WHEN '港股' THEN 1
                ELSE 2
              END,
              symbol
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            sym = (d.get("symbol") or "").upper()
            d["symbol"] = d.get("symbol") or sym
            m = (d.get("market") or "").upper()
            if m in ("美股",) or d.get("market") == "美股":
                d["market"] = "US"
            elif m in ("港股",) or d.get("market") == "港股":
                d["market"] = "HK"
            else:
                d["market"] = m if m in ("US", "HK") else d.get("market")
            d["enabled"] = bool(d.get("enabled"))
            d["in_wheel"] = sym in existing
            out.append(d)
        return out
    finally:
        conn.close()


class TargetIn(BaseModel):
    symbol: str
    name: Optional[str] = None
    market: Optional[str] = None
    floor_price: float
    max_capital: float = 0
    delta_min: float = 0.15
    delta_max: float = 0.30
    dte_min: int = 21
    dte_max: int = 45
    min_annualized: float = 15.0
    min_open_interest: int = 100
    enabled: bool = True


@router.post("/targets")
def add_target(body: TargetIn):
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    if body.floor_price <= 0:
        raise HTTPException(status_code=400, detail="接货底线价必须大于 0")
    name, market = body.name, body.market
    if not name or not market:
        conn = get_db()
        try:
            row = conn.execute("SELECT name, market FROM stocks WHERE symbol = ?", (symbol,)).fetchone()
        finally:
            conn.close()
        name = name or (row["name"] if row else symbol)
        market = market or (row["market"] if row else ("HK" if symbol.endswith(".HK") else "US"))
    repo.upsert_target({
        "symbol": symbol, "name": name, "market": market,
        "floor_price": body.floor_price, "max_capital": body.max_capital,
        "delta_min": body.delta_min, "delta_max": body.delta_max,
        "dte_min": body.dte_min, "dte_max": body.dte_max,
        "min_annualized": body.min_annualized,
        "min_open_interest": body.min_open_interest,
        "enabled": 1 if body.enabled else 0,
    })
    # 自动订阅历史日K:HV/EMA/IV rank 等档案数据依赖本地K线积累
    try:
        from app.data.history_repository import upsert_subscription
        upsert_subscription(symbol, name=name, enabled=True)
    except Exception as e:
        logger.warning("history 订阅失败(%s): %s", symbol, e)
    return repo.get_target(symbol)


class TargetUpdate(BaseModel):
    name: Optional[str] = None
    floor_price: Optional[float] = None
    max_capital: Optional[float] = None
    delta_min: Optional[float] = None
    delta_max: Optional[float] = None
    dte_min: Optional[int] = None
    dte_max: Optional[int] = None
    min_annualized: Optional[float] = None
    min_open_interest: Optional[int] = None
    enabled: Optional[bool] = None


@router.put("/targets/{symbol}")
def update_target(symbol: str, body: TargetUpdate):
    data = body.model_dump()
    if data.get("enabled") is not None:
        data["enabled"] = 1 if data["enabled"] else 0
    if not repo.update_target(symbol, **data):
        raise HTTPException(status_code=404, detail=f"{symbol} 不是 wheel 标的或无可更新字段")
    return repo.get_target(symbol)


@router.delete("/targets/{symbol}")
def delete_target(symbol: str):
    if repo.get_active_cycles(symbol):
        raise HTTPException(status_code=400, detail=f"{symbol} 有进行中的周期,请先结束周期再删除")
    if not repo.delete_target(symbol):
        raise HTTPException(status_code=404, detail=f"{symbol} 不是 wheel 标的")
    return {"ok": True}


# ── 周期与台账 ────────────────────────────────────────────────────────────────

@router.get("/cycles")
def list_cycles(symbol: Optional[str] = None, status: Optional[str] = None):
    return repo.get_cycles(symbol=symbol, status=status)


@router.get("/trades")
def list_trades(cycle_id: Optional[str] = None, symbol: Optional[str] = None, limit: int = 200):
    return repo.get_trades(cycle_id=cycle_id, symbol=symbol, limit=limit)


class TradeIn(BaseModel):
    symbol: str
    trade_type: str
    contract_code: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    qty: float = 1
    price: float = 0
    fee: float = 0
    contract_size: int = 100
    note: Optional[str] = None
    traded_at: Optional[str] = None
    cycle_id: Optional[str] = None
    new_cycle: bool = False


@router.post("/trades")
def record_trade(body: TradeIn):
    # 卖 Put 前校验标的资金上限(max_capital > 0 时生效)
    if body.trade_type == "SELL_PUT" and body.strike:
        target = repo.get_target(body.symbol.strip().upper())
        if target and (target.get("max_capital") or 0) > 0:
            usage = repo.get_capital_usage()["per_symbol"].get(body.symbol.strip().upper(), {})
            committed = usage.get("csp_collateral", 0) + usage.get("holding_cost", 0)
            new_collateral = body.strike * (body.qty or 1) * (body.contract_size or 100)
            if committed + new_collateral > target["max_capital"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"超出 {body.symbol} 资金上限:已占用 {committed:.0f} + 本单担保 {new_collateral:.0f} "
                           f"> 上限 {target['max_capital']:.0f}。可在标的设置调高上限(0=不限)",
                )
    # 卖出开仓且未填合约代码时,按 strike+到期日 自动补全
    contract_code = body.contract_code
    if (not contract_code and body.trade_type in ("SELL_PUT", "SELL_CALL")
            and body.strike and body.expiry):
        contract_code = _resolve_contract_code(
            body.symbol.strip().upper(), body.trade_type, body.strike, body.expiry)
    try:
        cycle = repo.record_trade(
            symbol=body.symbol, trade_type=body.trade_type,
            contract_code=contract_code, strike=body.strike, expiry=body.expiry,
            qty=body.qty, price=body.price, fee=body.fee,
            contract_size=body.contract_size, note=body.note, traded_at=body.traded_at,
            cycle_id=body.cycle_id, new_cycle=body.new_cycle,
        )
        return cycle
    except WheelError as e:
        raise HTTPException(status_code=400, detail=str(e))


def ensure_target_subscriptions() -> int:
    """确保所有启用的 wheel 标的都有历史日K订阅(波动率档案依赖)。返回新增数"""
    from app.data.history_repository import list_subscriptions, upsert_subscription
    existing = {s["symbol"] for s in list_subscriptions()}
    added = 0
    for t in repo.get_targets():
        if t.get("enabled") and t["symbol"] not in existing:
            try:
                upsert_subscription(t["symbol"], name=t.get("name"), enabled=True)
                added += 1
            except Exception:
                pass
    return added


def backfill_missing_contract_codes() -> Dict[str, Any]:
    """为存量卖出交易补全合约代码(按 strike+到期日推导),并重放所属周期。
    幂等:已有代码的交易跳过;推导失败(如港股无 OpenD)留空下次再试。"""
    updated, failed = 0, 0
    for t in repo.get_trades(limit=2000):
        if t["trade_type"] not in ("SELL_PUT", "SELL_CALL"):
            continue
        if (t.get("contract_code") or "").strip():
            continue
        if not t.get("strike") or not t.get("expiry"):
            continue
        code = _resolve_contract_code(t["symbol"], t["trade_type"], t["strike"], t["expiry"])
        if not code:
            failed += 1
            continue
        try:
            repo.update_trade(t["id"], contract_code=code)
            updated += 1
        except WheelError as e:
            logger.warning("补全 %s 失败: %s", t["id"], e)
            failed += 1
    return {"updated": updated, "failed": failed}


@router.post("/trades/backfill-codes")
def backfill_codes():
    """手动触发存量合约代码补全(OpenD 在线时港股也能补)"""
    return backfill_missing_contract_codes()


def _resolve_contract_code(symbol: str, trade_type: str, strike: float,
                           expiry: str) -> Optional[str]:
    """按 strike+到期日 推导期权代码。
    优先富途期权链精确匹配(US/HK 通用,可校验合约存在);
    OpenD 不可用时,美股按 OCC 规则合成(US.AAPL260821P00200000)。"""
    side = "PUT" if trade_type == "SELL_PUT" else "CALL"
    exp = str(expiry)[:10]
    try:
        from app.services.wheel_scanner import cached_chain
        futu_cfg = _wheel_cfg().get("futu", {}) or {}
        chain = cached_chain(symbol, exp,
                             futu_cfg.get("host", "127.0.0.1"), futu_cfg.get("port", 11111))
        for c in chain.get("contracts", []):
            if c.get("option_type") == side and abs((c.get("strike") or 0) - strike) < 1e-6:
                return c.get("option_symbol")
        logger.info("期权链中未找到 %s %s %s %s,回退合成", symbol, side, strike, exp)
    except Exception as e:
        logger.info("期权链补全代码不可用(%s): %s", symbol, e)
    if not symbol.endswith(".HK"):
        try:
            yymmdd = exp[2:4] + exp[5:7] + exp[8:10]
            return f"US.{symbol}{yymmdd}{'P' if side == 'PUT' else 'C'}{int(round(strike * 1000)):08d}"
        except Exception:
            pass
    return None


class TradeUpdate(BaseModel):
    trade_type: Optional[str] = None
    contract_code: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    qty: Optional[float] = None
    price: Optional[float] = None
    fee: Optional[float] = None
    contract_size: Optional[int] = None
    note: Optional[str] = None
    traded_at: Optional[str] = None


@router.put("/trades/{trade_id}")
def update_trade(trade_id: str, body: TradeUpdate):
    try:
        return repo.update_trade(trade_id, **body.model_dump())
    except WheelError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: str):
    try:
        return repo.delete_trade(trade_id)
    except WheelError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 统计 ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def stats():
    return repo.get_stats()


# ── 合约建议(卖 Put / 卖 Call)─────────────────────────────────────────────────

def _annualized(premium: float, collateral: float, dte: int) -> float:
    if collateral <= 0 or dte <= 0:
        return 0.0
    return round(premium / collateral * (365 / dte) * 100, 2)


def _suggest(symbol: str, side: str, host: str, port: int,
             cycle_id: Optional[str] = None) -> Dict[str, Any]:
    """side: PUT | CALL"""
    # 走扫描器的 TTL 缓存,单标的查询与全池扫描共享期权链,限频友好
    from app.services.wheel_scanner import (
        cached_expirations as _load_option_expirations,
        cached_chain as _load_option_chain,
    )

    target = repo.get_target(symbol)
    if target is None:
        raise HTTPException(status_code=404, detail=f"{symbol} 不是 wheel 标的,请先添加")

    cycles = repo.get_active_cycles(symbol)
    if cycle_id:
        cycle = next((c for c in cycles if c["id"] == cycle_id), None)
        if cycle is None:
            raise HTTPException(status_code=404, detail="指定的进行中周期不存在")
    elif side == "CALL":
        holding = [c for c in cycles if c["status"] == "HOLDING"]
        cycle = holding[0] if holding else None
    else:
        cycle = cycles[0] if cycles else None
    cost_basis = (cycle or {}).get("cost_basis")
    if side == "CALL":
        if cycle is None or cycle["status"] != "HOLDING":
            raise HTTPException(status_code=400, detail="卖 Call 需要处于持股状态(HOLDING)")

    dte_min, dte_max = target["dte_min"], target["dte_max"]
    delta_min, delta_max = target["delta_min"], target["delta_max"]
    min_oi = target.get("min_open_interest") or 0
    floor = target["floor_price"]

    expirations = _load_option_expirations(symbol, host, port)
    in_range = []
    for exp in expirations:
        try:
            dte = (date.fromisoformat(exp[:10]) - date.today()).days
        except Exception:
            continue
        if dte_min <= dte <= dte_max:
            in_range.append((exp, dte))
    if not in_range:
        return {"symbol": symbol, "side": side, "suggestions": [],
                "message": f"没有 DTE 在 {dte_min}~{dte_max} 天内的到期日"}

    pos_cfg = _wheel_cfg().get("wheel_position", {}) or {}
    margin_ratio = pos_cfg.get("margin_ratio", 0.25)

    from app.core.wheel_score import (
        get_scan_cfg, spread_pct, score_contract, trend_profile, is_iv_high,
        premium_from_quote, estimate_pop, sort_key_for_mode, buffer_atr_multiple,
    )
    from app.core.wheel_portfolio import headroom_ratio_for_symbol
    scan_cfg = get_scan_cfg(_wheel_cfg())
    pricing = scan_cfg.get("premium_pricing", "mid")

    # 财报 / 除息 / 事件封锁
    from app.core.earnings import get_next_earnings
    from app.core.dividends import dividend_warn
    earnings_date = get_next_earnings(symbol)
    div_warn = dividend_warn(symbol, int(pos_cfg.get("dividend_warn_days", 14)))
    headroom_ratio = headroom_ratio_for_symbol(symbol) if side == "PUT" else None

    suggestions: List[Dict[str, Any]] = []
    spot = None
    last_chain_contracts: List[Dict[str, Any]] = []
    chain_snapshots: List[Dict[str, Any]] = []
    filtered_earnings = 0
    try:
        from app.services.wheel_scanner import update_scan_progress as _prog
    except Exception:
        def _prog(**_kw):  # type: ignore
            return None

    for exp, dte in in_range[:3]:  # 最多取 3 个到期日,控制请求量
        exp_label = str(exp)[:10]
        _prog(
            symbol=symbol, side=side, expiry=exp_label,
            contract_i=0, contract_n=0,
            message=f"正在扫描 {symbol} · 到期 {exp_label} · 拉取期权链…",
        )
        chain = _load_option_chain(symbol, exp, host, port)
        spot = chain["spot_price"]
        last_chain_contracts = chain["contracts"]
        chain_snapshots.append({"expiry": exp, "dte": dte, "contracts": chain["contracts"]})
        # 本到期日该方向合约总数（筛选前）
        side_contracts = [c for c in chain["contracts"] if c.get("option_type") == side]
        total_side = len(side_contracts)
        _prog(
            symbol=symbol, side=side, expiry=exp_label,
            contract_i=0, contract_n=total_side,
            message=f"正在扫描 {symbol} · 到期 {exp_label} · 0/{total_side}",
        )
        for ci, c in enumerate(side_contracts, start=1):
            # 进度步进：每张更新太密，每 5 张或最后一张刷一次
            if ci == 1 or ci == total_side or ci % 5 == 0:
                _prog(
                    symbol=symbol, side=side, expiry=exp_label,
                    contract_i=ci, contract_n=total_side,
                    message=f"正在扫描 {symbol} · 到期 {exp_label} · {ci}/{total_side}",
                )
            d = abs(c.get("delta") or 0)
            if d < delta_min or d > delta_max:
                continue
            if (c.get("open_interest") or 0) < min_oi:
                continue
            bid = c.get("bid") or 0
            ask = c.get("ask")
            prem = premium_from_quote(bid, ask, pricing)
            if prem <= 0 and bid <= 0:
                continue
            # 流动性:bid-ask spread 过宽的合约实际成交会吃掉大量收益,直接过滤
            sp = spread_pct(bid, ask)
            if sp is not None and sp > scan_cfg["max_spread_pct"]:
                continue
            strike = c["strike"]
            size = c.get("contract_size") or 100
            covers_earnings = bool(earnings_date and earnings_date <= exp[:10])
            # 财报硬过滤(Put)
            if (
                side == "PUT"
                and covers_earnings
                and scan_cfg.get("earnings_hard_filter", True)
            ):
                filtered_earnings += 1
                continue
            if side == "PUT":
                if strike > floor:
                    continue
                collateral = strike
                if_assigned_cost = round(strike - prem, 4)
                extra = {"assigned_cost": if_assigned_cost}
            else:
                if cost_basis is not None and strike < cost_basis:
                    continue
                collateral = cost_basis or strike
                shares = (cycle or {}).get("shares") or 0
                if_called = round(
                    ((strike - (cost_basis or 0)) * shares + prem * size) if cost_basis else 0, 2
                )
                extra = {"if_called_total": if_called}
            ann = _annualized(prem, collateral, dte)
            if ann < (target.get("min_annualized") or 0):
                continue
            # 保证金口径年化(仅 PUT;决策仍以现金担保年化为主)
            ann_margin = _annualized(prem, strike * margin_ratio, dte) if side == "PUT" else None
            pop = estimate_pop(side, d)
            suggestions.append({
                "contract_code": c["option_symbol"],
                "expiry": exp, "dte": dte, "strike": strike,
                "delta": round(d, 4), "delta_source": c.get("delta_source", "futu"),
                "bid": bid, "ask": ask,
                "premium_used": round(prem, 4),
                "premium_pricing": pricing,
                "iv": c.get("iv"), "open_interest": c.get("open_interest"),
                "volume": c.get("volume"), "contract_size": size,
                "annualized": ann,
                "annualized_margin": ann_margin,
                "annualized_cash": ann,  # 明确现金担保口径
                "spread_pct": sp,
                "covers_earnings": covers_earnings,
                "pop": round(pop, 4),
                "otm_pct": round(
                    (spot - strike) / spot * 100 if side == "PUT" else (strike - spot) / spot * 100, 2
                ),
                "limit_price_hint": round(prem, 2),
                **extra,
            })

    # 波动率档案(用已拉取的链,不额外请求)
    volatility = None
    if spot is not None and last_chain_contracts:
        try:
            from app.core.volatility import build_profile
            volatility = build_profile(symbol, spot, chain_contracts=last_chain_contracts)
        except Exception as e:
            logger.warning("volatility profile 失败: %s", e)

    # IV 期限结构 + skew
    term_structure = None
    skew = None
    try:
        from app.core.wheel_iv_extra import term_structure_from_chains, skew_from_chain
        if spot and chain_snapshots:
            term_structure = term_structure_from_chains(chain_snapshots, spot)
            skew = skew_from_chain(last_chain_contracts, spot)
    except Exception as e:
        logger.warning("iv term/skew 失败: %s", e)

    # 趋势档案(本地日K EMA50/EMA200,无额外请求)
    trend = None
    try:
        trend = trend_profile(symbol, spot)
    except Exception as e:
        logger.warning("trend profile 失败: %s", e)

    atr = (trend or {}).get("atr20")
    # 综合打分:年化 × 流动性 × 趋势 × 财报 × IV × POP × 缓冲 × 资金余量
    kept: List[Dict[str, Any]] = []
    for s in suggestions:
        buf = buffer_atr_multiple(side, spot, s["strike"], atr)
        s["buffer_atr"] = buf
        scored = score_contract(
            s["annualized"], side, s["delta"], s.get("spread_pct"),
            s["covers_earnings"], volatility, trend, scan_cfg,
            pop=s.get("pop"), buffer_atr=buf, headroom_ratio=headroom_ratio,
            premium=s.get("premium_used"), collateral=s["strike"],
        )
        if scored is None:
            continue
        s["score"] = scored["score"]
        s["robust_score"] = scored.get("robust_score")
        s["ev_pct"] = scored.get("ev_pct")
        s["pop"] = scored.get("pop", s.get("pop"))
        s["score_factors"] = scored["factors"]
        # skew 陡峭时近 delta put 降权标记
        if side == "PUT" and skew and (skew.get("put_skew") or 0) > 8 and s["delta"] > 0.22:
            s["score"] = round((s["score"] or 0) * 0.9, 2)
            s["score_factors"]["skew_penalty"] = 0.9
        kept.append(s)
    suggestions = kept
    mode = scan_cfg.get("sort_mode", "score")
    suggestions.sort(key=lambda x: sort_key_for_mode(x, mode), reverse=True)

    delta_preference = None
    if is_iv_high(volatility):
        delta_preference = "IV 高位:同等年化优先更低 delta(更远离行权价)"
        if volatility and volatility.get("iv_rank_source") == "hv_proxy":
            delta_preference += "(IV 历史不足,当前用 HV rank 近似)"

    trend_warning = None
    if side == "PUT" and trend and trend.get("trend") == "DOWN":
        trend_warning = f"现价低于 EMA200({trend.get('ema200')}),下跌趋势中卖 Put 接货风险高,评分已降权"
    elif side == "PUT" and trend and trend.get("trend") == "WEAK":
        trend_warning = f"现价低于 EMA50({trend.get('ema50')}),趋势转弱,评分已降权"

    from datetime import date as _date2
    days_to_earn = None
    if earnings_date:
        try:
            days_to_earn = (_date2.fromisoformat(earnings_date) - _date2.today()).days
        except Exception:
            pass

    # 动态 floor / call 锚点
    floor_suggest = None
    call_anchors = None
    try:
        from app.core.wheel_floor import suggest_floor, suggest_call_strikes
        if side == "PUT" and spot:
            floor_suggest = suggest_floor(
                symbol, spot, floor, (volatility or {}).get("iv_rank"),
            )
        if side == "CALL" and spot:
            call_anchors = suggest_call_strikes(
                symbol, spot, cost_basis, delta_min, delta_max,
            )
    except Exception as e:
        logger.warning("floor/call suggest 失败: %s", e)

    return {
        "symbol": symbol, "side": side, "spot_price": spot,
        "cost_basis": cost_basis,
        "filters": {"delta": [delta_min, delta_max], "dte": [dte_min, dte_max],
                    "min_oi": min_oi, "min_annualized": target.get("min_annualized"),
                    "floor_price": floor, "premium_pricing": pricing,
                    "earnings_hard_filter": scan_cfg.get("earnings_hard_filter", True),
                    "sort_mode": mode},
        "suggestions": suggestions[:20],
        "volatility": volatility,
        "term_structure": term_structure,
        "skew": skew,
        "trend": trend,
        "trend_warning": trend_warning,
        "margin_ratio": margin_ratio,
        "earnings_date": earnings_date,
        "days_to_earnings": days_to_earn,
        "earnings_warn": days_to_earn is not None and days_to_earn <= pos_cfg.get("earnings_warn_days", 14),
        "earnings_filtered_count": filtered_earnings,
        "dividend_warn": div_warn,
        "delta_preference": delta_preference,
        "headroom_ratio": headroom_ratio,
        "floor_suggest": floor_suggest,
        "call_anchors": call_anchors,
    }


# ── 在场合约体检(利润目标 / 临期 / ITM)────────────────────────────────────────

def _position_hints(item: Dict[str, Any], min_annualized: float, profit_target: float,
                    pos_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """在场合约行动判定 — 委托动态决策树(见 wheel_decision)。"""
    from app.core.wheel_decision import decide_position
    return decide_position(item, min_annualized, profit_target, pos_cfg or _wheel_cfg().get("wheel_position"))


def _portfolio_context_for_manage() -> Dict[str, Any]:
    """体检用组合上下文:利用率 / 是否资金紧 / 是否停新 Put。不触发扫描。"""
    full_cfg = _wheel_cfg()
    pos_cfg = full_cfg.get("wheel_position", {}) or {}
    pcfg = full_cfg.get("wheel_portfolio", {}) or {}
    tight_util = float(pos_cfg.get("capital_tight_util_pct", 75.0))
    try:
        from app.core.wheel_opportunities import _portfolio_put_stress
        from app.core.wheel_portfolio import portfolio_overview

        put_blocked, stress_meta = _portfolio_put_stress(full_cfg)
        overview = portfolio_overview(
            total_equity=float(pcfg["total_equity"]) if pcfg.get("total_equity") else None,
            max_portfolio_pct=float(pcfg.get("max_portfolio_pct", 0.80)),
            max_symbol_pct=float(pcfg.get("max_symbol_pct", 0.25)),
        )
        util = overview.get("utilization_pct")
        over_pf = bool(overview.get("over_portfolio"))
        capital_tight = bool(
            over_pf
            or (util is not None and float(util) >= tight_util)
        )
        headroom_by: Dict[str, Optional[float]] = {}
        committed_by: Dict[str, float] = {}
        max_cap_by: Dict[str, Optional[float]] = {}
        for row in overview.get("per_symbol") or []:
            sym = row.get("symbol")
            if sym:
                headroom_by[sym] = row.get("headroom")
                committed_by[sym] = float(row.get("committed") or 0)
                max_cap_by[sym] = (
                    float(row["max_capital"]) if row.get("max_capital") else None
                )
        return {
            "utilization_pct": util,
            "capital_tight": capital_tight,
            "portfolio_put_blocked": put_blocked,
            "idle_cash": overview.get("idle_cash"),
            "over_portfolio": over_pf,
            "equity": overview.get("equity"),
            "assignment_stress": stress_meta.get("assignment_stress"),
            "headroom_by_symbol": headroom_by,
            "committed_by_symbol": committed_by,
            "max_capital_by_symbol": max_cap_by,
            "capital_tight_util_pct": tight_util,
        }
    except Exception as e:
        logger.warning("portfolio_context for manage failed: %s", e)
        return {
            "utilization_pct": None,
            "capital_tight": False,
            "portfolio_put_blocked": False,
            "idle_cash": None,
            "over_portfolio": False,
            "equity": None,
            "assignment_stress": None,
            "headroom_by_symbol": {},
            "committed_by_symbol": {},
            "max_capital_by_symbol": {},
            "capital_tight_util_pct": tight_util,
        }


def check_open_positions_core(host: str, port: int) -> Dict[str, Any]:
    """拉在场合约与标的快照,计算浮盈/DTE/ITM/delta 及行动建议。供 API 和后台推送共用"""
    import futu
    from datetime import date as _date
    from app.core.leaps_monitor import _throttle, _to_futu_symbol

    cfg = _wheel_cfg().get("wheel_position", {}) or {}
    profit_target = cfg.get("profit_target_pct", 50)
    portfolio_ctx = _portfolio_context_for_manage()
    capital_tight = bool(portfolio_ctx.get("capital_tight"))
    put_blocked = bool(portfolio_ctx.get("portfolio_put_blocked"))
    util_pct = portfolio_ctx.get("utilization_pct")
    headroom_by = portfolio_ctx.get("headroom_by_symbol") or {}

    cycles = [c for c in repo.get_cycles(include_closed=False)
              if c["status"] in ("CSP_OPEN", "CC_OPEN") and c.get("open_contract_code")]
    if not cycles:
        return {
            "items": [],
            "profit_target_pct": profit_target,
            "pos_cfg": cfg,
            "portfolio_context": portfolio_ctx,
        }

    codes = [c["open_contract_code"] for c in cycles]
    und_codes = list({_to_futu_symbol(c["symbol"]) for c in cycles})
    quotes: Dict[str, Dict[str, float]] = {}
    ctx = futu.OpenQuoteContext(host=host, port=port)
    try:
        for i in range(0, len(codes) + len(und_codes), 80):
            chunk = (codes + und_codes)[i:i + 80]
            _throttle()
            ret, snap = ctx.get_market_snapshot(chunk)
            if ret == futu.RET_OK and snap is not None and not snap.empty:
                for _, row in snap.iterrows():
                    quotes[str(row.get("code"))] = {
                        "last": float(row.get("last_price", 0) or 0),
                        "ask": float(row.get("ask_price", 0) or 0),
                        "bid": float(row.get("bid_price", 0) or 0),
                        "delta": abs(float(row.get("option_delta", 0) or 0)),
                        "theta": abs(float(row.get("option_theta", 0) or 0)),
                    }
    finally:
        ctx.close()

    min_ann_by_symbol: Dict[str, float] = {}
    target_by_symbol: Dict[str, Any] = {}
    trend_by_symbol: Dict[str, Optional[str]] = {}
    equity = portfolio_ctx.get("equity")
    committed_by: Dict[str, float] = portfolio_ctx.get("committed_by_symbol") or {}

    items = []
    for c in cycles:
        q = quotes.get(c["open_contract_code"], {})
        cur = q.get("last") or q.get("ask") or 0
        buyback = q.get("ask") or q.get("last") or 0
        open_price = c.get("open_price") or 0
        # 浮盈用买回 ask(卖方真实平仓成本),无 ask 再退 last
        close_for_pnl = buyback or cur
        profit_pct = (
            round((open_price - close_for_pnl) / open_price * 100, 1)
            if open_price and close_for_pnl else None
        )
        und = quotes.get(_to_futu_symbol(c["symbol"]), {})
        spot = und.get("last") or 0
        strike = c.get("open_strike") or 0
        itm = bool(spot and strike and (
            (c["open_option_type"] == "PUT" and spot < strike) or
            (c["open_option_type"] == "CALL" and spot > strike)))
        dte = c.get("open_dte")
        # 每标的只取一次 target: min_annualized + floor_price 供决策树
        if c["symbol"] not in target_by_symbol:
            tgt = repo.get_target(c["symbol"])
            target_by_symbol[c["symbol"]] = tgt
            min_ann_by_symbol[c["symbol"]] = float((tgt or {}).get("min_annualized") or 0)
        tgt = target_by_symbol[c["symbol"]]
        floor_px = float((tgt or {}).get("floor_price") or 0) or None
        max_cap = float((tgt or {}).get("max_capital") or 0) or None
        qty = c.get("open_qty") or 1
        size = c.get("contract_size") or c.get("open_contract_size") or 100
        # 轻量趋势:每标的缓存一次(不跑全量 admission)
        if c["symbol"] not in trend_by_symbol:
            tr_name = None
            try:
                from app.core.wheel_score import trend_profile
                tp = trend_profile(c["symbol"], float(spot) if spot else None)
                tr_name = (tp or {}).get("trend")
            except Exception:
                tr_name = None
            trend_by_symbol[c["symbol"]] = tr_name
        trend_name = trend_by_symbol[c["symbol"]]
        # 除息窗口先算,决策树需要 days_to_ex_div
        days_to_ex_div = None
        dividend_warn_payload = None
        if c["open_option_type"] == "CALL":
            try:
                from app.core.dividends import dividend_warn
                dw = dividend_warn(c["symbol"], int(cfg.get("dividend_warn_days", 14)))
                if dw:
                    dividend_warn_payload = dw
                    days_to_ex_div = dw.get("days_to_ex")
            except Exception:
                pass
        item = {
            "cycle_id": c["id"], "symbol": c["symbol"], "side": c["open_option_type"],
            "contract_code": c["open_contract_code"], "strike": strike,
            "expiry": c.get("open_expiry"), "dte": dte,
            "open_price": open_price, "current_price": cur, "buyback_ask": buyback,
            "profit_pct": profit_pct, "spot": spot, "itm": itm,
            "delta": q.get("delta") or 0,
            "theta": q.get("theta") or 0,
            "qty": qty,
            "contract_size": size,
            "days_to_ex_div": days_to_ex_div,
            "floor_price": floor_px,
            "profit_hit": profit_pct is not None and profit_pct >= profit_target,
            "expiring": dte is not None and dte <= 7,
            "capital_util_pct": util_pct,
            "capital_tight": capital_tight,
            "portfolio_put_blocked": put_blocked,
            "symbol_headroom": headroom_by.get(c["symbol"]),
            "trend": trend_name,
            "target_enabled": bool((tgt or {}).get("enabled", True)) if tgt is not None else True,
            "equity": equity,
            "symbol_max_capital": max_cap,
            "symbol_committed": committed_by.get(c["symbol"]),
            "share_cost": c.get("share_cost"),
            "cost_basis": c.get("cost_basis"),
        }
        item.update(_position_hints(item, min_ann_by_symbol[c["symbol"]], profit_target, cfg))
        # 与决策树对齐:profit_hit 以树内结果为准
        item["profit_hit"] = bool((item.get("decision_tree") or {}).get("profit_hit"))
        # 释放资本粗估 + 换仓提示(不重扫机会)
        if c["open_option_type"] == "PUT" and strike:
            freed = float(strike) * float(qty) * float(size)
        else:
            freed = 0.0  # CC 主要是解除义务,不释放现金担保
        item["freed_capital_est"] = round(freed, 2) if freed else None
        code = (item.get("action_code") or "").upper()
        if code in ("REPLACE", "CLOSE"):
            if put_blocked and c["open_option_type"] == "PUT":
                item["replace_hint"] = (
                    "释放后组合仍停新 Put(利用率/行权压力),先空仓或等触线;可看 CC 机会"
                )
            elif freed > 0:
                item["replace_hint"] = (
                    f"释放约 ${freed:,.0f} 担保,可去机会列表开新 Put/CC"
                )
            else:
                item["replace_hint"] = "结束 Call 义务后可再卖 CC 或调仓,见机会列表"
        if dividend_warn_payload:
            item["dividend_warn"] = dividend_warn_payload
        items.append(item)
    items.sort(key=lambda x: (
        x.get("action_priority") or 9,
        x.get("dte") if x.get("dte") is not None else 999,
        x.get("symbol") or "",
    ))
    return {
        "items": items,
        "profit_target_pct": profit_target,
        "pos_cfg": cfg,
        "portfolio_context": {
            k: v for k, v in portfolio_ctx.items()
            if k not in ("headroom_by_symbol", "committed_by_symbol", "max_capital_by_symbol")
        },
    }


@router.get("/open-positions/check")
def check_open_positions(host: str = Query("127.0.0.1"), port: int = Query(11111)):
    try:
        return check_open_positions_core(host, port)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"在场合约体检失败(OpenD?): {e}")


# ── Roll 对比 ─────────────────────────────────────────────────────────────────

@router.get("/roll-options")
def roll_options(
    cycle_id: str = Query(...),
    host: str = Query("127.0.0.1"),
    port: int = Query(11111),
    allow_down_strike: bool = Query(False, description="允许 Call 向下/Put 向上调 strike"),
    max_spread_pct: float = Query(10.0, description="点差超过则剔除"),
    min_oi: int = Query(0, description="最低 OI,0=不限"),
    qty: Optional[float] = Query(None, description="张数预览,默认用 cycle 持仓张数"),
):
    """Roll 决策台:场景结论 + 三卡片 + 多报价情景 + 效率/事件/限价。"""
    from datetime import date as _date
    from app.api.options import _load_option_expirations, _load_option_chain
    from app.core.leaps_monitor import _throttle
    from app.core import wheel_roll as wr
    from app.core.earnings import get_next_earnings
    from app.core.dividends import get_next_dividend
    import futu

    cycle = repo.get_cycle(cycle_id)
    if cycle is None or cycle["status"] not in ("CSP_OPEN", "CC_OPEN"):
        raise HTTPException(status_code=400, detail="该周期没有在场合约")
    symbol, side = cycle["symbol"], cycle["open_option_type"]
    code = (cycle.get("open_contract_code") or "").strip()
    size = int(cycle.get("open_contract_size") or 100)
    qty_f = float(qty) if qty is not None and qty > 0 else float(cycle.get("open_qty") or 1)
    qty = max(qty_f, 0.01)
    target = repo.get_target(symbol) or {}
    warnings: List[str] = []
    pos_cfg = _wheel_cfg().get("wheel_position", {}) or {}
    scan_cfg = (_wheel_cfg().get("wheel_scan") or {})
    max_spread_pct = float(max_spread_pct or scan_cfg.get("max_spread_pct") or 10)

    if code and "." not in code:
        code = f"US.{code}"

    def _opend_alive(h: str, p: int, timeout: float = 0.4) -> bool:
        import socket
        try:
            with socket.create_connection((h, int(p)), timeout=timeout):
                return True
        except OSError:
            return False

    opend_ok = _opend_alive(host, port)
    if not opend_ok:
        warnings.append(
            f"OpenD 未连接({host}:{port})：无法拉期权链，仍给出决策建议；启动 OpenD 后点刷新可看 Roll 候选"
        )

    # 当前合约: bid/ask + delta
    buyback_bid, buyback_ask, cur_delta = 0.0, 0.0, 0.0
    if code and opend_ok:
        try:
            ctx = futu.OpenQuoteContext(host=host, port=port)
            try:
                _throttle()
                ret, snap = ctx.get_market_snapshot([code])
                if ret == futu.RET_OK and snap is not None and not snap.empty:
                    row = snap.iloc[0]
                    buyback_bid = float(row.get("bid_price", 0) or 0)
                    buyback_ask = float(row.get("ask_price", 0) or row.get("last_price", 0) or 0)
                    if buyback_ask <= 0:
                        buyback_ask = buyback_bid
                    cur_delta = abs(float(row.get("option_delta", 0) or 0))
                else:
                    warnings.append(f"当前合约快照失败({code}),买回价请手动填写")
            finally:
                ctx.close()
        except Exception as e:
            warnings.append(f"当前合约行情异常: {e}")
    elif not code:
        warnings.append("未记录合约代码,买回价请手动填写")

    buyback = buyback_ask  # 默认平仓用 ask
    open_price = float(cycle.get("open_price") or 0)
    cur_dte = cycle.get("open_dte")
    cur_strike = float(cycle.get("open_strike") or 0)
    cur_expiry = str(cycle.get("open_expiry") or "")[:10]
    cost_basis = cycle.get("cost_basis")
    share_cost = cycle.get("share_cost")
    shares = float(cycle.get("shares") or 0) or (qty * size if side == "CALL" else 0)

    # 浮盈
    profit_pct = None
    if open_price > 0 and buyback_ask > 0:
        profit_pct = round((open_price - buyback_ask) / open_price * 100, 1)
    remaining_ann = None
    if cur_strike > 0 and cur_dte and cur_dte > 0 and buyback_ask > 0:
        remaining_ann = round(buyback_ask / cur_strike * 365 / cur_dte * 100, 2)

    # delta 带
    delta_lo = float(target.get("delta_min") or 0.15)
    delta_hi = float(target.get("delta_max") or 0.30)
    if delta_lo > delta_hi:
        delta_lo, delta_hi = delta_hi, delta_lo
    delta_hard_max = 0.50
    target_mid = (delta_lo + delta_hi) / 2.0
    match_current = bool(cur_delta > 0 and delta_lo <= cur_delta <= delta_hi)
    if match_current:
        pref_lo, pref_hi = max(0.05, cur_delta - 0.08), min(delta_hard_max, cur_delta + 0.08)
        delta_mode = "match_current"
    else:
        pref_lo, pref_hi = max(0.05, delta_lo - 0.05), min(delta_hard_max, delta_hi + 0.08)
        delta_mode = "target_band"
        if cur_delta >= delta_hard_max:
            warnings.append(
                f"当前 δ≈{cur_delta:.2f} 偏高,候选按目标 δ {delta_lo:.2f}~{delta_hi:.2f},不追高 δ"
            )

    # 成本底线
    call_cost_floor = None
    for v in (cost_basis, share_cost):
        try:
            fv = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            fv = 0.0
        if fv > 0:
            call_cost_floor = max(call_cost_floor or 0.0, fv)
    if call_cost_floor is None and cur_strike > 0 and side == "CALL":
        call_cost_floor = cur_strike
        warnings.append(f"无成本基础,CALL 底线暂用当前 strike ${cur_strike:g}")
    put_strike_cap = None
    if side == "PUT":
        try:
            put_strike_cap = float(target.get("floor_price") or 0) or None
        except (TypeError, ValueError):
            put_strike_cap = None

    # 事件
    earnings_date = get_next_earnings(symbol)
    div = get_next_dividend(symbol)
    div_date = (div or {}).get("date")

    # 到期日:目标 DTE + 优先覆盖 30–45
    dte_lo = int(target.get("dte_min") or 21)
    dte_hi = int(target.get("dte_max") or 60)
    dte_hi_eff = max(dte_hi, 45)
    next_exps = []
    if opend_ok:
        try:
            expirations = _load_option_expirations(symbol, host, port)
        except HTTPException as e:
            warnings.append(f"获取到期日失败: {e.detail}")
            expirations = []
        except Exception as e:
            warnings.append(f"获取到期日异常: {e}")
            expirations = []
        for exp in expirations:
            try:
                dte = (_date.fromisoformat(exp[:10]) - _date.today()).days
            except Exception:
                continue
            if exp[:10] > cur_expiry and dte_lo <= dte <= max(dte_hi_eff, dte_lo + 30):
                next_exps.append((exp, dte))
        # 优先 30–45 DTE
        next_exps.sort(key=lambda x: (0 if 30 <= x[1] <= 45 else 1, abs(x[1] - 37), x[1]))
        if not next_exps and expirations:
            warnings.append(
                f"无可用到期日(需 >{cur_expiry} 且 DTE∈[{dte_lo},{max(dte_hi_eff, dte_lo+30)}])"
            )
    # OpenD 不可用时 next_exps 为空,仍返回决策三卡片

    def _strike_ok(strike: float, spot_v: Optional[float]) -> bool:
        if not strike or strike <= 0:
            return False
        if side == "CALL":
            if call_cost_floor is not None and strike + 1e-9 < call_cost_floor:
                return False
            if spot_v and spot_v > 0 and strike < spot_v * 0.95:
                return False
        else:
            if put_strike_cap is not None and strike > put_strike_cap + 1e-9:
                return False
            if spot_v and spot_v > 0 and strike > spot_v * 1.08:
                return False
        return True

    candidates: List[Dict[str, Any]] = []
    spot = None
    skipped = {"below_cost": 0, "spread": 0, "oi": 0, "delta": 0, "earnings": 0}

    for exp, dte in next_exps[:4]:
        try:
            _throttle()
            chain = _load_option_chain(symbol, exp, host, port)
        except HTTPException as e:
            warnings.append(f"期权链 {exp} 失败: {e.detail}")
            continue
        spot = chain.get("spot_price") or spot
        covers_earn = bool(earnings_date and earnings_date <= exp[:10])
        covers_div = bool(div_date and div_date <= exp[:10])
        # Put 默认硬过滤覆盖财报
        if side == "PUT" and covers_earn and scan_cfg.get("earnings_hard_filter", True):
            skipped["earnings"] += 1
            continue

        for c in chain["contracts"]:
            if c.get("option_type") != side:
                continue
            bid = float(c.get("bid") or 0)
            ask = float(c.get("ask") or 0)
            if bid <= 0:
                continue
            strike = float(c.get("strike") or 0)
            if not _strike_ok(strike, spot):
                if side == "CALL" and call_cost_floor and strike < call_cost_floor:
                    skipped["below_cost"] += 1
                continue
            sp = wr.spread_pct(bid, ask)
            if sp is not None and sp > max_spread_pct:
                skipped["spread"] += 1
                continue
            oi = int(c.get("open_interest") or 0)
            if min_oi > 0 and oi < min_oi:
                skipped["oi"] += 1
                continue

            try:
                d = abs(float(c.get("delta") or 0))
            except (TypeError, ValueError):
                d = 0.0
            delta_unknown = d <= 1e-9
            if delta_unknown:
                if not spot or spot <= 0:
                    if abs(strike - cur_strike) > 1e-6:
                        continue
                else:
                    if side == "CALL" and not (spot * 0.98 <= strike <= spot * 1.20):
                        continue
                    if side == "PUT" and not (spot * 0.80 <= strike <= spot * 1.02):
                        continue
                otm = ((strike - spot) / spot if side == "CALL" else (spot - strike) / spot) if spot else 0
                d_for_sort = max(0.05, min(0.45, 0.35 - otm * 1.2))
                band = "wide"
            else:
                if d > delta_hard_max:
                    skipped["delta"] += 1
                    continue
                if spot and spot > 0:
                    if side == "CALL" and strike < spot and d > 0.40:
                        skipped["delta"] += 1
                        continue
                    if side == "PUT" and strike > spot and d > 0.40:
                        skipped["delta"] += 1
                        continue
                d_for_sort = d
                in_pref = pref_lo <= d <= pref_hi
                in_wide = 0.10 <= d <= min(0.45, delta_hard_max)
                if not in_pref and not in_wide:
                    skipped["delta"] += 1
                    continue
                band = "preferred" if in_pref else "wide"

            branch = wr.classify_branch(side, strike, cur_strike, exp, cur_expiry)
            enriched = wr.enrich_candidate(
                side=side, contract=c, expiry=exp, dte=dte, cur_dte=cur_dte,
                cur_strike=cur_strike, cur_expiry=cur_expiry,
                buyback_bid=buyback_bid, buyback_ask=buyback_ask, size=size,
                spot=spot, cost_basis=cost_basis if cost_basis else call_cost_floor,
                call_cost_floor=call_cost_floor, shares=shares or size,
                band=band, branch=branch, delta_unknown=delta_unknown,
                d_for_sort=d_for_sort, target_mid=target_mid,
                delta_lo=delta_lo, delta_hi=delta_hi,
                covers_earnings=covers_earn, covers_dividend=covers_div,
                allow_down_strike=allow_down_strike,
            )
            if not enriched:
                continue
            # 草稿补合约代码与 qty
            enriched["draft_legs"][0]["contract_code"] = code
            enriched["draft_legs"][0]["qty"] = qty
            enriched["draft_legs"][1]["qty"] = qty
            # 按张数缩放金额字段
            if qty != 1:
                for k in ("net_credit_per_contract", "net_credit_conservative"):
                    if enriched.get(k) is not None:
                        enriched[k] = round(enriched[k] * qty, 2)
                if enriched.get("credit_per_day") is not None:
                    enriched["credit_per_day"] = round(enriched["credit_per_day"] * qty, 3)
                for sc in (enriched.get("pricing") or {}).values():
                    sc["net_credit_per_contract"] = round(sc["net_credit_per_contract"] * qty, 2)
            candidates.append(enriched)

    # 禁止 worse_direction 进主列表(除非 allow)
    if not allow_down_strike:
        n_worse = sum(1 for c in candidates if c.get("worse_direction"))
        candidates = [c for c in candidates if not c.get("worse_direction")]
        if n_worse:
            warnings.append(f"已隐藏 {n_worse} 个不利方向调 strike(勾选允许可看)")

    if side == "CALL" and call_cost_floor:
        candidates = [c for c in candidates if (c.get("strike") or 0) + 1e-9 >= call_cost_floor]

    candidates.sort(
        key=lambda x: (
            0 if x.get("band") == "preferred" else 1,
            -(x.get("rank_score") or 0),
            -(x.get("net_credit_conservative") or 0),
        )
    )

    for k, n in skipped.items():
        if n and k == "below_cost":
            warnings.append(f"已过滤 {n} 个 strike 低于成本的合约")
        elif n and k == "spread":
            warnings.append(f"已过滤 {n} 个点差>{max_spread_pct}% 的合约")
        elif n and k == "earnings":
            warnings.append(f"已跳过 {n} 个覆盖财报的到期日(Put 硬过滤)")

    # 现价/ITM
    spot_v = spot
    itm = bool(spot_v and cur_strike and (
        (side == "PUT" and spot_v < cur_strike) or (side == "CALL" and spot_v > cur_strike)
    ))
    deep_itm = bool(itm and (cur_delta > 0.5 or (
        spot_v and cur_strike and abs(spot_v - cur_strike) / spot_v > 0.03
    )))

    close_notional = float(buyback_ask or 0) * float(size or 100)
    scenario = wr.decide_roll_scenario(
        side=side, dte=cur_dte, profit_pct=profit_pct, itm=itm, deep_itm=deep_itm,
        delta=cur_delta, remaining_ann=remaining_ann,
        min_annualized=float(target.get("min_annualized") or 0),
        profit_target=float(pos_cfg.get("profit_target_pct") or 50),
        hard_roll_dte=int(pos_cfg.get("hard_roll_dte") or 21),
        close_notional=close_notional,
        pos_cfg=pos_cfg,
    )

    decision = wr.build_decision_cards(
        candidates, side=side, cur_strike=cur_strike,
        buyback_ask=buyback_ask, size=size, open_price=open_price,
        scenario=scenario, allow_down_strike=allow_down_strike,
    )

    credit = [c for c in candidates if (c.get("net_credit_per_contract") or 0) > 0]
    debit = [c for c in candidates if (c.get("net_credit_per_contract") or 0) <= 0]
    primary = credit if credit else debit
    if candidates and not credit:
        warnings.append("无 roll-for-credit:仅展示 debit 候选作参考,大额倒贴通常不值得")

    by_branch: Dict[str, List] = {}
    for c in primary:
        by_branch.setdefault(c.get("branch") or "other", []).append(c)

    history = wr.roll_history_for_symbol(symbol, limit=8)

    # 默认选中:高亮卡片候选
    hl = decision.get("highlighted")
    default_pick = None
    if hl == "roll_out" and decision["cards"]["roll_out"].get("candidate"):
        default_pick = decision["cards"]["roll_out"]["candidate"]
    elif hl == "adjust_strike" and decision["cards"]["adjust_strike"].get("candidate"):
        default_pick = decision["cards"]["adjust_strike"]["candidate"]
    elif primary:
        default_pick = primary[0]

    return {
        "cycle_id": cycle_id,
        "symbol": symbol,
        "side": side,
        "spot_price": spot,
        "qty": qty,
        "allow_down_strike": allow_down_strike,
        "decision": {
            "headline": scenario.get("headline"),
            "detail": scenario.get("detail"),
            "recommended_action": scenario.get("recommended_action"),
            "scenario": scenario.get("scenario"),
            "prefer_card": decision.get("highlighted"),
            "profit_pct": profit_pct,
            "remaining_annualized": remaining_ann,
            "itm": itm,
            "deep_itm": deep_itm,
        },
        "cards": decision.get("cards"),
        "highlighted_card": decision.get("highlighted"),
        "default_candidate": default_pick,
        "strike_floor": {
            "call_min_strike": call_cost_floor,
            "cost_basis": cost_basis,
            "share_cost": share_cost,
            "put_max_strike": put_strike_cap,
            "rule": "CALL strike ≥ max(cost_basis, share_cost); PUT strike ≤ floor_price",
        },
        "delta_filter": {
            "mode": delta_mode,
            "preferred": [round(pref_lo, 3), round(pref_hi, 3)],
            "target": [delta_lo, delta_hi],
            "hard_max": delta_hard_max,
            "current_delta": cur_delta,
        },
        "liquidity": {"max_spread_pct": max_spread_pct, "min_oi": min_oi},
        "events": {
            "earnings_date": earnings_date,
            "dividend": div,
        },
        "pricing_legend": {
            "optimistic": "平仓 mid / 开仓 mid",
            "default": "平仓 ask / 开仓 bid(推荐决策)",
            "conservative": "平仓 ask+tick / 开仓 bid−tick",
        },
        "current": {
            "contract_code": code,
            "strike": cycle.get("open_strike"),
            "expiry": cur_expiry,
            "dte": cur_dte,
            "open_price": open_price,
            "buyback_bid": buyback_bid,
            "buyback_ask": buyback_ask,
            "delta": cur_delta,
            "contract_size": size,
            "cost_basis": cost_basis,
            "share_cost": share_cost,
            "shares": shares,
            "profit_pct": profit_pct,
            "remaining_annualized": remaining_ann,
            "itm": itm,
        },
        "candidates": primary[:15],
        "debit_candidates": debit[:5],
        "branches": {k: v[:5] for k, v in by_branch.items()},
        "same_strike_highlights": [c for c in primary if c.get("same_strike")][:5],
        "roll_history": history,
        "skipped_counts": skipped,
        "alternatives": {
            "let_expire": decision["cards"]["no_roll"]["options"]["let_expire"],
            "close_now": decision["cards"]["no_roll"]["options"]["close_now"],
        },
        "warnings": warnings,
    }


@router.get("/roll-history")
def roll_history_api(symbol: str = Query(...), limit: int = Query(10, ge=1, le=50)):
    from app.core.wheel_roll import roll_history_for_symbol
    return {"symbol": symbol.strip().upper(), "items": roll_history_for_symbol(symbol.strip().upper(), limit)}


# ── 全池扫描 ──────────────────────────────────────────────────────────────────

@router.get("/scan")
def scan_all(host: str = Query("127.0.0.1"), port: int = Query(11111),
             refresh: bool = Query(False, description="true=清缓存强制拉最新期权链"),
             use_last: bool = Query(False, description="true=直接返回上次扫描结果(秒回)")):
    """扫描所有启用标的,自动判定卖 Put/卖 Call,按综合分跨标的排序输出 Top 机会"""
    from app.services import wheel_scanner
    if use_last:
        last = wheel_scanner.get_last_result()
        if last:
            return last
    try:
        return wheel_scanner.run_scan(host, port, force_refresh=refresh)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"全池扫描失败(OpenD?): {e}")


@router.get("/scan/progress")
def scan_progress():
    """全池扫描实时进度:当前标的/到期日/合约 n/m。"""
    from app.services import wheel_scanner
    return wheel_scanner.get_scan_progress()


@router.get("/quote")
def quote_contract(
    symbol: str = Query(...),
    contract_code: str = Query(...),
    host: str = Query("127.0.0.1"),
    port: int = Query(11111),
    side: Optional[str] = Query(None, description="PUT|CALL,可省略由合约码推断"),
):
    """用 OpenD 补单合约实时 bid/ask(详情/备忘用)。"""
    from app.services.wheel_scanner import cached_expirations, cached_chain
    from app.core.leaps_monitor import _parse_futu_contract

    sym = symbol.strip().upper()
    code = contract_code.strip()
    und, exp_raw, strike, opt = _parse_futu_contract(code)
    side_u = (side or ("PUT" if opt == "P" else "CALL" if opt == "C" else "")).upper()
    expiry = None
    if exp_raw and len(exp_raw) == 6:
        try:
            expiry = datetime.strptime("20" + exp_raw, "%Y%m%d").date().isoformat()
        except Exception:
            expiry = None
    if not expiry:
        # 从链里按合约码反查
        try:
            exps = cached_expirations(sym, host, port)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"拉到期日失败: {e}")
        for exp in exps[:12]:
            try:
                chain = cached_chain(sym, exp, host, port)
            except Exception:
                continue
            for c in chain.get("contracts") or []:
                if (c.get("option_symbol") or "").upper().replace("US.", "") == code.upper().replace("US.", ""):
                    return {
                        "symbol": sym,
                        "contract_code": c.get("option_symbol") or code,
                        "side": c.get("option_type"),
                        "strike": c.get("strike"),
                        "expiry": str(exp)[:10],
                        "bid": c.get("bid"),
                        "ask": c.get("ask"),
                        "last": c.get("last_price") or c.get("last"),
                        "delta": c.get("delta"),
                        "spot_price": chain.get("spot_price"),
                    }
        raise HTTPException(status_code=404, detail="期权链中未找到该合约")
    try:
        chain = cached_chain(sym, expiry, host, port, force=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"拉期权链失败: {e}")
    norm = code.upper().replace("US.", "")
    for c in chain.get("contracts") or []:
        cc = (c.get("option_symbol") or "").upper().replace("US.", "")
        if cc == norm or (side_u and c.get("option_type") == side_u and abs((c.get("strike") or 0) - (strike or 0)) < 1e-6):
            return {
                "symbol": sym,
                "contract_code": c.get("option_symbol") or code,
                "side": c.get("option_type"),
                "strike": c.get("strike"),
                "expiry": expiry,
                "bid": c.get("bid"),
                "ask": c.get("ask"),
                "last": c.get("last_price") or c.get("last"),
                "delta": c.get("delta"),
                "spot_price": chain.get("spot_price"),
            }
    raise HTTPException(status_code=404, detail="到期日链中未找到该合约")


@router.post("/scan/push")
def scan_and_push(host: str = Query("127.0.0.1"), port: int = Query(11111),
                  refresh: bool = Query(False)):
    """手动触发扫描并推送 Telegram"""
    from app.services import wheel_scanner
    try:
        return wheel_scanner.push_scan(host, port, force_refresh=refresh)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"扫描推送失败: {e}")


@router.get("/opportunities")
def unified_opportunities(
    host: str = Query("127.0.0.1"),
    port: int = Query(11111),
    refresh: bool = Query(False, description="true=强制重跑全池扫描再合流"),
    run_pool: bool = Query(True, description="缓存为空时是否自动跑全池"),
    filter: str = Query(
        "actionable",
        description="actionable|all|dual|timing|score|watch|blocked",
    ),
    side: Optional[str] = Query(None, description="PUT|CALL"),
    hide_blocked: bool = Query(True),
):
    """统一可交易机会流:触线时机 ∩ 全池质量分 + 摘要/可做定义。"""
    from app.core.wheel_opportunities import build_opportunities

    try:
        data = build_opportunities(
            host, port,
            refresh_pool=refresh,
            run_pool_if_empty=run_pool,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"机会合流失败: {e}")

    items = list(data.get("items") or [])
    f = (filter or "all").lower()
    if f == "actionable":
        items = [x for x in items if x.get("actionable")]
    elif f == "dual":
        items = [x for x in items if x.get("source") == "dual" or x.get("grade") == "dual"]
    elif f == "timing":
        items = [x for x in items if x.get("source") in ("timing", "dual")]
    elif f == "score":
        items = [x for x in items if x.get("source") in ("score", "dual")]
    elif f == "watch":
        items = [x for x in items if x.get("grade") == "watch"]
    elif f == "blocked":
        items = [x for x in items if x.get("grade") == "blocked"]
    # all: no grade filter

    if hide_blocked and f not in ("blocked", "all", "watch"):
        items = [x for x in items if x.get("grade") != "blocked"]
    if f == "all" and hide_blocked:
        items = [x for x in items if x.get("grade") != "blocked"]

    if side:
        s = side.strip().upper()
        items = [x for x in items if x.get("side") == s]

    data["items"] = items
    data["filter_applied"] = {
        "filter": f, "side": side, "hide_blocked": hide_blocked, "count": len(items),
    }
    return data


@router.get("/suggest/put")
def suggest_put(symbol: str = Query(...), host: str = Query("127.0.0.1"), port: int = Query(11111),
                cycle_id: Optional[str] = Query(None)):
    return _suggest(symbol.strip().upper(), "PUT", host, port, cycle_id)


@router.get("/suggest/call")
def suggest_call(symbol: str = Query(...), host: str = Query("127.0.0.1"), port: int = Query(11111),
                 cycle_id: Optional[str] = Query(None)):
    return _suggest(symbol.strip().upper(), "CALL", host, port, cycle_id)


# ── 组合 / 压力测试 / 准入 / 归因 / 对账 / 回测 / Profile ──────────────────────

@router.get("/portfolio")
def portfolio(
    equity: Optional[float] = Query(None, description="组合净值,空则用配置或 max_capital 之和"),
):
    from app.core.wheel_portfolio import portfolio_overview
    pcfg = _wheel_cfg().get("wheel_portfolio", {}) or {}
    eq = equity if equity and equity > 0 else (pcfg.get("total_equity") or None)
    if eq is not None and eq <= 0:
        eq = None
    return portfolio_overview(
        total_equity=eq,
        max_portfolio_pct=float(pcfg.get("max_portfolio_pct", 0.80)),
        max_symbol_pct=float(pcfg.get("max_symbol_pct", 0.25)),
    )


@router.get("/portfolio/stress")
def portfolio_stress(equity: Optional[float] = Query(None)):
    from app.core.wheel_portfolio import stress_test
    pcfg = _wheel_cfg().get("wheel_portfolio", {}) or {}
    eq = equity if equity and equity > 0 else (pcfg.get("total_equity") or None)
    if eq is not None and eq <= 0:
        eq = None
    return stress_test(total_equity=eq)


@router.get("/portfolio/correlation")
def portfolio_corr():
    from app.core.wheel_portfolio import correlation_matrix
    return correlation_matrix()


@router.get("/admission")
def admission(symbol: Optional[str] = Query(None)):
    from app.core.wheel_admission import score_symbol, score_all_targets
    if symbol:
        return score_symbol(symbol.strip().upper())
    return score_all_targets()


@router.get("/floor-suggest")
def floor_suggest_api(symbol: str = Query(...), spot: Optional[float] = Query(None)):
    from app.core.wheel_floor import suggest_floor
    from app.core.volatility import brief_profile
    t = repo.get_target(symbol.strip().upper())
    vol = brief_profile(symbol.strip().upper())
    return suggest_floor(
        symbol.strip().upper(), spot,
        (t or {}).get("floor_price"),
        vol.get("iv_rank"),
    )


@router.get("/attribution/health")
def attribution_health():
    from app.core.wheel_attribution import strategy_health
    return strategy_health()


@router.get("/attribution/cycle/{cycle_id}")
def attribution_cycle(cycle_id: str):
    from app.core.wheel_attribution import cycle_attribution
    return cycle_attribution(cycle_id)


@router.get("/attribution/suggestion-logs")
def suggestion_logs(limit: int = Query(10, ge=1, le=50)):
    from app.core.wheel_attribution import recent_suggestion_logs
    return {"items": recent_suggestion_logs(limit)}


@router.get("/reconcile")
def reconcile_api(
    host: str = Query("127.0.0.1"),
    port: int = Query(11111),
    trd_env: str = Query("SIMULATE"),
):
    from app.core.wheel_reconcile import reconcile
    try:
        return reconcile(host, port, trd_env)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"对账失败(OpenD/交易上下文?): {e}")


class DraftApplyIn(BaseModel):
    symbol: str
    trade_type: str
    contract_code: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    qty: float = 1
    price: float = 0
    fee: float = 0
    contract_size: int = 100
    note: Optional[str] = None
    cycle_id: Optional[str] = None


@router.post("/reconcile/apply-draft")
def apply_reconcile_draft(body: DraftApplyIn):
    from app.core.wheel_reconcile import apply_draft
    try:
        return apply_draft(body.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class RollDraftIn(BaseModel):
    cycle_id: str
    buyback_price: float
    sell_contract_code: str
    sell_strike: float
    sell_expiry: str
    sell_price: float
    qty: float = 1
    fee_close: float = 0
    fee_open: float = 0
    contract_size: int = 100


@router.post("/roll/register")
def register_roll(body: RollDraftIn):
    """一键登记 Roll 两腿:买回平仓 + 卖出新约(同一 cycle)。"""
    cycle = repo.get_cycle(body.cycle_id)
    if not cycle or cycle["status"] not in ("CSP_OPEN", "CC_OPEN"):
        raise HTTPException(status_code=400, detail="周期无在场合约")
    side = cycle["open_option_type"]
    symbol = cycle["symbol"]
    close_type = "BUY_PUT_CLOSE" if side == "PUT" else "BUY_CALL_CLOSE"
    open_type = "SELL_PUT" if side == "PUT" else "SELL_CALL"
    try:
        repo.record_trade(
            symbol=symbol, trade_type=close_type, cycle_id=body.cycle_id,
            contract_code=cycle.get("open_contract_code"),
            strike=cycle.get("open_strike"), expiry=cycle.get("open_expiry"),
            qty=body.qty, price=body.buyback_price, fee=body.fee_close,
            contract_size=body.contract_size, note="Roll 平仓腿",
        )
        c2 = repo.record_trade(
            symbol=symbol, trade_type=open_type, cycle_id=body.cycle_id,
            contract_code=body.sell_contract_code,
            strike=body.sell_strike, expiry=body.sell_expiry,
            qty=body.qty, price=body.sell_price, fee=body.fee_open,
            contract_size=body.contract_size, note="Roll 开仓腿",
        )
        return c2
    except WheelError as e:
        raise HTTPException(status_code=400, detail=str(e))


class BacktestIn(BaseModel):
    symbol: str
    params: Optional[Dict[str, Any]] = None


@router.post("/backtest")
def wheel_backtest(body: BacktestIn):
    from app.core.wheel_backtest import run_wheel_backtest
    return run_wheel_backtest(body.symbol.strip().upper(), body.params)


class CompareProfilesIn(BaseModel):
    symbol: str
    profiles: List[Dict[str, Any]]


@router.post("/backtest/compare")
def wheel_backtest_compare(body: CompareProfilesIn):
    from app.core.wheel_backtest import compare_profiles
    return compare_profiles(body.symbol.strip().upper(), body.profiles)


@router.get("/profiles")
def list_profiles():
    cfg = _wheel_cfg()
    wp = cfg.get("wheel_profiles") or {}
    return {
        "active": wp.get("active", "balanced"),
        "presets": list((wp.get("presets") or {}).keys()),
        "detail": wp.get("presets") or {},
    }


class ActivateProfileIn(BaseModel):
    name: str


@router.post("/profiles/activate")
def activate_profile(body: ActivateProfileIn):
    """将预设 profile 合并进 backend_config 并生效。"""
    import json
    from app.core.config import deep_merge, get_db_overrides, get_effective_config
    from app.data.wheel_repository import set_kv

    cfg = get_effective_config()
    presets = (cfg.get("wheel_profiles") or {}).get("presets") or {}
    if body.name not in presets:
        raise HTTPException(status_code=404, detail=f"未知 profile: {body.name}")
    overlay = presets[body.name]
    existing = get_db_overrides()
    merged = deep_merge(existing, overlay)
    merged = deep_merge(merged, {"wheel_profiles": {"active": body.name}})
    set_kv("backend_config", json.dumps(merged, ensure_ascii=False))
    import app.api.leaps as leaps_mod
    leaps_mod._config_cache = None
    return {"ok": True, "active": body.name, "applied": overlay, "effective": get_effective_config()}


@router.post("/alerts/push")
def push_position_alerts(host: str = Query("127.0.0.1"), port: int = Query(11111)):
    """推送在场合约高优先级行动建议到 Telegram。"""
    from app.core.wheel_decision import format_alert_line
    from app.services.notifier import TelegramNotifier

    data = check_open_positions_core(host, port)
    urgent = [i for i in data.get("items") or [] if (i.get("action_priority") or 9) <= 3]
    if not urgent:
        return {"sent": False, "count": 0, "message": "无高优先级行动"}
    lines = ["🚨 Wheel 持仓行动提醒"] + [format_alert_line(i) for i in urgent[:15]]
    notifier = TelegramNotifier.from_config(_wheel_cfg())
    sent = notifier.send("\n".join(lines)) if notifier._enabled else False
    return {"sent": sent, "count": len(urgent), "items": urgent}


class EventBlockIn(BaseModel):
    symbol: Optional[str] = None  # 空=全局
    event_date: str
    label: Optional[str] = None


@router.get("/event-blocks")
def list_event_blocks():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM wheel_event_blocks ORDER BY event_date"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/event-blocks")
def add_event_block(body: EventBlockIn):
    from app.data.database import _now_iso
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO wheel_event_blocks (symbol, event_date, label, created_at) VALUES (?,?,?,?)",
            (body.symbol, body.event_date[:10], body.label, _now_iso()),
        )
        conn.commit()
        return {"id": cur.lastrowid, "ok": True}
    finally:
        conn.close()


@router.delete("/event-blocks/{block_id}")
def del_event_block(block_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM wheel_event_blocks WHERE id = ?", (block_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
