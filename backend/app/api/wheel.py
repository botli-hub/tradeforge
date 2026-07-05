"""Wheel 策略 REST API"""
import logging
from datetime import date
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
    return targets


@router.get("/targets/candidates")
def target_candidates():
    """股票池美股/港股(启用),排除已是 wheel 标的的"""
    existing = {t["symbol"] for t in repo.get_targets()}
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT symbol, name, market FROM stocks WHERE market IN ('US','HK') AND enabled = 1 ORDER BY market, symbol"
        ).fetchall()
        return [dict(r) for r in rows if r["symbol"] not in existing]
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
    try:
        cycle = repo.record_trade(
            symbol=body.symbol, trade_type=body.trade_type,
            contract_code=body.contract_code, strike=body.strike, expiry=body.expiry,
            qty=body.qty, price=body.price, fee=body.fee,
            contract_size=body.contract_size, note=body.note, traded_at=body.traded_at,
            cycle_id=body.cycle_id, new_cycle=body.new_cycle,
        )
        return cycle
    except WheelError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    from app.api.options import _load_option_expirations, _load_option_chain

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

    # 财报标注
    from app.core.earnings import get_next_earnings
    earnings_date = get_next_earnings(symbol)

    suggestions: List[Dict[str, Any]] = []
    spot = None
    last_chain_contracts: List[Dict[str, Any]] = []
    for exp, dte in in_range[:3]:  # 最多取 3 个到期日,控制请求量
        chain = _load_option_chain(symbol, exp, host, port)
        spot = chain["spot_price"]
        last_chain_contracts = chain["contracts"]
        for c in chain["contracts"]:
            if c["option_type"] != side:
                continue
            d = abs(c.get("delta") or 0)
            if d < delta_min or d > delta_max:
                continue
            if (c.get("open_interest") or 0) < min_oi:
                continue
            bid = c.get("bid") or 0
            if bid <= 0:
                continue
            strike = c["strike"]
            size = c.get("contract_size") or 100
            if side == "PUT":
                if strike > floor:
                    continue
                collateral = strike
                if_assigned_cost = round(strike - bid, 4)
                extra = {"assigned_cost": if_assigned_cost}
            else:
                if cost_basis is not None and strike < cost_basis:
                    continue
                collateral = cost_basis or strike
                shares = (cycle or {}).get("shares") or 0
                if_called = round(((strike - (cost_basis or 0)) * shares + bid * size) if cost_basis else 0, 2)
                extra = {"if_called_total": if_called}
            ann = _annualized(bid, collateral, dte)
            if ann < (target.get("min_annualized") or 0):
                continue
            # 保证金口径年化(仅 PUT;covered call 的担保是正股,无此口径)
            ann_margin = _annualized(bid, strike * margin_ratio, dte) if side == "PUT" else None
            # 该合约到期前是否有财报
            covers_earnings = bool(earnings_date and earnings_date <= exp[:10])
            suggestions.append({
                "contract_code": c["option_symbol"],
                "expiry": exp, "dte": dte, "strike": strike,
                "delta": round(d, 4), "bid": bid, "ask": c.get("ask"),
                "iv": c.get("iv"), "open_interest": c.get("open_interest"),
                "volume": c.get("volume"), "contract_size": size,
                "annualized": ann,
                "annualized_margin": ann_margin,
                "covers_earnings": covers_earnings,
                "otm_pct": round((spot - strike) / spot * 100 if side == "PUT" else (strike - spot) / spot * 100, 2),
                **extra,
            })

    suggestions.sort(key=lambda x: x["annualized"], reverse=True)

    # 波动率档案(用已拉取的链,不额外请求)
    volatility = None
    if spot is not None and last_chain_contracts:
        try:
            from app.core.volatility import build_profile
            volatility = build_profile(symbol, spot, chain_contracts=last_chain_contracts)
        except Exception as e:
            logger.warning("volatility profile 失败: %s", e)

    # 动态 delta:IV 高位(rank≥70 或 IV/HV≥1.3)时偏好更低 delta——
    # 用 annualized×(1−delta) 重排,同等年化下低 delta 排前
    delta_preference = None
    iv_high = bool(volatility and (
        (volatility.get("iv_rank") or 0) >= 70 or (volatility.get("iv_hv_ratio") or 0) >= 1.3))
    if iv_high:
        suggestions.sort(key=lambda x: x["annualized"] * (1 - x["delta"]), reverse=True)
        delta_preference = "IV 高位:同等年化优先更低 delta(更远离行权价)"

    from datetime import date as _date2
    days_to_earn = None
    if earnings_date:
        try:
            days_to_earn = (_date2.fromisoformat(earnings_date) - _date2.today()).days
        except Exception:
            pass

    return {
        "symbol": symbol, "side": side, "spot_price": spot,
        "cost_basis": cost_basis,
        "filters": {"delta": [delta_min, delta_max], "dte": [dte_min, dte_max],
                    "min_oi": min_oi, "min_annualized": target.get("min_annualized"),
                    "floor_price": floor},
        "suggestions": suggestions[:20],
        "volatility": volatility,
        "margin_ratio": margin_ratio,
        "earnings_date": earnings_date,
        "days_to_earnings": days_to_earn,
        "earnings_warn": days_to_earn is not None and days_to_earn <= pos_cfg.get("earnings_warn_days", 14),
        "delta_preference": delta_preference,
    }


# ── 在场合约体检(利润目标 / 临期 / ITM)────────────────────────────────────────

def check_open_positions_core(host: str, port: int) -> Dict[str, Any]:
    """拉在场合约与标的快照,计算浮盈/DTE/ITM。供 API 和后台推送共用"""
    import futu
    from datetime import date as _date
    from app.core.leaps_monitor import _throttle, _to_futu_symbol

    cfg = _wheel_cfg().get("wheel_position", {}) or {}
    profit_target = cfg.get("profit_target_pct", 50)

    cycles = [c for c in repo.get_cycles(include_closed=False)
              if c["status"] in ("CSP_OPEN", "CC_OPEN") and c.get("open_contract_code")]
    if not cycles:
        return {"items": [], "profit_target_pct": profit_target}

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
                    }
    finally:
        ctx.close()

    items = []
    for c in cycles:
        q = quotes.get(c["open_contract_code"], {})
        cur = q.get("last") or q.get("ask") or 0
        buyback = q.get("ask") or q.get("last") or 0
        open_price = c.get("open_price") or 0
        profit_pct = round((open_price - cur) / open_price * 100, 1) if open_price and cur else None
        und = quotes.get(_to_futu_symbol(c["symbol"]), {})
        spot = und.get("last") or 0
        strike = c.get("open_strike") or 0
        itm = bool(spot and strike and (
            (c["open_option_type"] == "PUT" and spot < strike) or
            (c["open_option_type"] == "CALL" and spot > strike)))
        dte = c.get("open_dte")
        items.append({
            "cycle_id": c["id"], "symbol": c["symbol"], "side": c["open_option_type"],
            "contract_code": c["open_contract_code"], "strike": strike,
            "expiry": c.get("open_expiry"), "dte": dte,
            "open_price": open_price, "current_price": cur, "buyback_ask": buyback,
            "profit_pct": profit_pct, "spot": spot, "itm": itm,
            "profit_hit": profit_pct is not None and profit_pct >= profit_target,
            "expiring": dte is not None and dte <= 7,
        })
    return {"items": items, "profit_target_pct": profit_target}


@router.get("/open-positions/check")
def check_open_positions(host: str = Query("127.0.0.1"), port: int = Query(11111)):
    try:
        return check_open_positions_core(host, port)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"在场合约体检失败(OpenD?): {e}")


# ── Roll 对比 ─────────────────────────────────────────────────────────────────

@router.get("/roll-options")
def roll_options(cycle_id: str = Query(...), host: str = Query("127.0.0.1"), port: int = Query(11111)):
    """对在场合约给出 Roll 候选:买回当前 + 卖出下一到期日相近 delta 合约的净权利金对比"""
    from datetime import date as _date
    from app.api.options import _load_option_expirations, _load_option_chain
    from app.core.leaps_monitor import _throttle, _to_futu_symbol
    import futu

    cycle = repo.get_cycle(cycle_id)
    if cycle is None or cycle["status"] not in ("CSP_OPEN", "CC_OPEN"):
        raise HTTPException(status_code=400, detail="该周期没有在场合约")
    symbol, side = cycle["symbol"], cycle["open_option_type"]
    code = (cycle.get("open_contract_code") or "").strip()
    size = cycle.get("open_contract_size") or 100
    target = repo.get_target(symbol) or {}
    warnings: List[str] = []

    # 规范化合约代码(手动登记时可能没带市场前缀)
    if code and "." not in code:
        code = f"US.{code}"

    # 当前合约买回价 + delta(失败不阻断,降级为按同 strike 匹配)
    buyback, cur_delta = 0.0, 0.0
    if code:
        try:
            ctx = futu.OpenQuoteContext(host=host, port=port)
            try:
                _throttle()
                ret, snap = ctx.get_market_snapshot([code])
                if ret == futu.RET_OK and snap is not None and not snap.empty:
                    row = snap.iloc[0]
                    buyback = float(row.get("ask_price", 0) or row.get("last_price", 0) or 0)
                    cur_delta = abs(float(row.get("option_delta", 0) or 0))
                else:
                    warnings.append(f"当前合约快照失败(限频或合约码无效: {code}),买回价请手动填写,候选按同 strike 匹配")
            finally:
                ctx.close()
        except Exception as e:
            warnings.append(f"当前合约行情获取异常: {e}")
    else:
        warnings.append("该周期未记录合约代码,买回价请手动填写,候选按同 strike 匹配")

    # 找当前到期日之后、DTE 范围内的到期日
    cur_expiry = str(cycle.get("open_expiry") or "")[:10]
    dte_lo = target.get("dte_min", 21)
    dte_hi = target.get("dte_max", 60)
    try:
        expirations = _load_option_expirations(symbol, host, port)
    except HTTPException as e:
        raise HTTPException(status_code=502, detail=f"获取到期日失败(OpenD/限频): {e.detail}")
    next_exps = []
    for exp in expirations:
        try:
            dte = (_date.fromisoformat(exp[:10]) - _date.today()).days
        except Exception:
            continue
        if exp[:10] > cur_expiry and dte_lo <= dte <= max(dte_hi, dte_lo + 30):
            next_exps.append((exp, dte))
    if not next_exps:
        warnings.append(f"当前到期日({cur_expiry})之后、DTE {dte_lo}~{max(dte_hi, dte_lo + 30)} 天内没有可用到期日")
    candidates = []
    for exp, dte in next_exps[:2]:
        try:
            _throttle()
            chain = _load_option_chain(symbol, exp, host, port)
        except HTTPException as e:
            warnings.append(f"期权链 {exp} 获取失败: {e.detail}")
            continue
        for c in chain["contracts"]:
            if c["option_type"] != side:
                continue
            d = abs(c.get("delta") or 0)
            bid = c.get("bid") or 0
            if bid <= 0:
                continue
            # delta 相近(±0.08),或无 delta 时同 strike
            if cur_delta > 0 and abs(d - cur_delta) > 0.08:
                continue
            if cur_delta == 0 and c["strike"] != cycle.get("open_strike"):
                continue
            net_credit = round((bid - buyback) * size, 2)
            candidates.append({
                "contract_code": c["option_symbol"], "expiry": exp, "dte": dte,
                "strike": c["strike"], "delta": round(d, 3), "bid": bid,
                "net_credit_per_contract": net_credit,
                "annualized": round(bid / (c["strike"] or 1) * 365 / dte * 100, 2) if dte else None,
            })
    candidates.sort(key=lambda x: x["net_credit_per_contract"], reverse=True)
    return {
        "cycle_id": cycle_id, "symbol": symbol, "side": side,
        "current": {"contract_code": code, "strike": cycle.get("open_strike"),
                    "expiry": cur_expiry, "dte": cycle.get("open_dte"),
                    "open_price": cycle.get("open_price"), "buyback_ask": buyback,
                    "delta": cur_delta, "contract_size": size},
        "candidates": candidates[:10],
        "warnings": warnings,
    }


@router.get("/suggest/put")
def suggest_put(symbol: str = Query(...), host: str = Query("127.0.0.1"), port: int = Query(11111),
                cycle_id: Optional[str] = Query(None)):
    return _suggest(symbol.strip().upper(), "PUT", host, port, cycle_id)


@router.get("/suggest/call")
def suggest_call(symbol: str = Query(...), host: str = Query("127.0.0.1"), port: int = Query(11111),
                 cycle_id: Optional[str] = Query(None)):
    return _suggest(symbol.strip().upper(), "CALL", host, port, cycle_id)
