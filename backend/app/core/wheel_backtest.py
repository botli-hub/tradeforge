"""Wheel 规则回测(简化)

无历史期权链时,用 HV 合成近似权利金:
  premium ≈ spot * sigma * sqrt(DTE/365) * 0.4 * delta_factor

规则:
  - 每月(或每 dte 周期)在 OTM put 开仓
  - 50% 止盈用近似(价格路径碰触)
  - 到期:若 close < strike 则 assign,持股后下一期卖 call
  - 输出 CAGR、最大回撤、assign 次数等

用途:对比参数 profile,非精确定价回测。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class BTParams:
    delta: float = 0.25
    dte: int = 30
    profit_take: float = 0.50
    floor_pct: float = 0.90  # strike 不高于 spot * floor 相关:用 spot*(1-k*delta)
    min_annualized: float = 15.0
    initial_capital: float = 100000.0
    contracts: int = 1
    contract_size: int = 100
    skip_earnings: bool = False  # 无财报数据时忽略


def _synthetic_premium(spot: float, sigma: float, dte: int, delta: float) -> float:
    """极简权利金近似。"""
    if spot <= 0 or sigma <= 0 or dte <= 0:
        return 0.0
    t = dte / 365.0
    # 卖方 delta 越高权利金越高
    return max(0.05, spot * sigma * math.sqrt(t) * 0.35 * (0.5 + delta))


def _hv_from_closes(closes: List[float], window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.25
    rets = []
    seg = closes[-(window + 1):]
    for i in range(1, len(seg)):
        if seg[i - 1] > 0:
            rets.append(math.log(seg[i] / seg[i - 1]))
    if len(rets) < 5:
        return 0.25
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return max(0.10, math.sqrt(var) * math.sqrt(252))


def run_wheel_backtest(
    symbol: str,
    params: Optional[Dict[str, Any]] = None,
    lookback_bars: int = 504,
) -> Dict[str, Any]:
    from app.core.volatility import get_daily_closes

    p = BTParams(**{k: v for k, v in (params or {}).items() if k in BTParams.__dataclass_fields__})
    closes = get_daily_closes(symbol, limit=lookback_bars)
    if len(closes) < 80:
        return {"ok": False, "error": f"{symbol} 日K不足({len(closes)}),需≥80根", "symbol": symbol}

    cash = p.initial_capital
    shares = 0.0
    share_cost = 0.0
    equity_curve: List[float] = []
    trades: List[Dict[str, Any]] = []
    open_put = None  # {strike, premium, open_i, dte, collateral}
    open_call = None
    assign_n = 0
    called_n = 0
    premium_sum = 0.0

    i = 60  # 预热
    while i < len(closes):
        spot = closes[i]
        sigma = _hv_from_closes(closes[: i + 1])
        # mark equity
        eq = cash + shares * spot
        if open_put:
            # 卖方:义务按 mark 粗估(简化不算)
            pass
        equity_curve.append(eq)

        # 管理 open put
        if open_put:
            age = i - open_put["open_i"]
            # 近似:权利金随时间线性衰减 + 内在价值
            intrinsic = max(open_put["strike"] - spot, 0)
            remain_frac = max(0.0, 1 - age / max(open_put["dte"], 1))
            mark = max(intrinsic, open_put["premium"] * remain_frac * 0.9)
            # 止盈:买回成本 ≤ premium * (1-profit_take)
            if mark <= open_put["premium"] * (1 - p.profit_take):
                cost = mark * p.contracts * p.contract_size
                cash -= cost
                premium_sum += open_put["premium"] * p.contracts * p.contract_size - cost
                trades.append({"i": i, "type": "BUY_PUT_CLOSE", "spot": spot, "mark": mark})
                open_put = None
            elif age >= open_put["dte"]:
                if spot < open_put["strike"]:
                    # assign
                    cost = open_put["strike"] * p.contracts * p.contract_size
                    cash -= cost
                    shares += p.contracts * p.contract_size
                    share_cost = open_put["strike"] - open_put["premium"]  # 每股约
                    assign_n += 1
                    premium_sum += open_put["premium"] * p.contracts * p.contract_size
                    trades.append({"i": i, "type": "ASSIGNED", "strike": open_put["strike"], "spot": spot})
                else:
                    premium_sum += open_put["premium"] * p.contracts * p.contract_size
                    trades.append({"i": i, "type": "EXPIRE_PUT", "spot": spot})
                open_put = None

        # 管理 open call
        if open_call and shares > 0:
            age = i - open_call["open_i"]
            intrinsic = max(spot - open_call["strike"], 0)
            remain_frac = max(0.0, 1 - age / max(open_call["dte"], 1))
            mark = max(intrinsic, open_call["premium"] * remain_frac * 0.9)
            if mark <= open_call["premium"] * (1 - p.profit_take):
                cost = mark * p.contracts * p.contract_size
                cash -= cost
                premium_sum += open_call["premium"] * p.contracts * p.contract_size - cost
                trades.append({"i": i, "type": "BUY_CALL_CLOSE", "spot": spot})
                open_call = None
            elif age >= open_call["dte"]:
                if spot > open_call["strike"]:
                    # called away
                    cash += open_call["strike"] * shares
                    premium_sum += open_call["premium"] * p.contracts * p.contract_size
                    shares = 0
                    share_cost = 0
                    called_n += 1
                    trades.append({"i": i, "type": "CALLED_AWAY", "spot": spot})
                else:
                    premium_sum += open_call["premium"] * p.contracts * p.contract_size
                    trades.append({"i": i, "type": "EXPIRE_CALL", "spot": spot})
                open_call = None

        # 开仓
        if shares > 0 and open_call is None:
            # 卖 call: strike ≈ max(share_cost, spot * 1.02)
            strike = max(share_cost or spot, spot * 1.02)
            prem = _synthetic_premium(spot, sigma, p.dte, p.delta)
            cash += prem * p.contracts * p.contract_size
            open_call = {"strike": strike, "premium": prem, "open_i": i, "dte": p.dte}
            trades.append({"i": i, "type": "SELL_CALL", "strike": strike, "premium": prem, "spot": spot})
        elif shares <= 0 and open_put is None:
            # 卖 put
            # 近似 delta→OTM%: delta 0.25 ≈ 略 OTM
            otm = 0.02 + p.delta * 0.15
            strike = spot * (1 - otm)
            floor = spot * p.floor_pct
            if strike > floor:
                strike = floor
            prem = _synthetic_premium(spot, sigma, p.dte, p.delta)
            coll = strike * p.contracts * p.contract_size
            ann = prem / strike * 365 / p.dte * 100 if strike else 0
            if coll <= cash and ann >= p.min_annualized:
                cash += prem * p.contracts * p.contract_size  # 收权利金;担保不真正锁死现金简化
                # 简化:不冻结 coll 到 cash,只在 assign 时扣
                open_put = {
                    "strike": strike, "premium": prem, "open_i": i,
                    "dte": p.dte, "collateral": coll,
                }
                trades.append({"i": i, "type": "SELL_PUT", "strike": strike, "premium": prem, "spot": spot, "ann": ann})

        i += 1

    # 清仓 mark
    final_spot = closes[-1]
    if open_put:
        premium_sum += open_put["premium"] * p.contracts * p.contract_size * 0.5
        open_put = None
    if open_call:
        premium_sum += open_call["premium"] * p.contracts * p.contract_size * 0.5
        open_call = None
    final_eq = cash + shares * final_spot
    equity_curve.append(final_eq)

    # 指标
    peak = equity_curve[0]
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)
    days = len(closes) - 60
    years = max(days / 252.0, 0.01)
    ret = final_eq / p.initial_capital - 1
    cagr = (final_eq / p.initial_capital) ** (1 / years) - 1 if final_eq > 0 else -1

    return {
        "ok": True,
        "symbol": symbol,
        "params": {
            "delta": p.delta, "dte": p.dte, "profit_take": p.profit_take,
            "floor_pct": p.floor_pct, "min_annualized": p.min_annualized,
            "initial_capital": p.initial_capital, "contracts": p.contracts,
        },
        "bars": len(closes),
        "trading_days": days,
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "premium_sum_est": round(premium_sum, 2),
        "assign_count": assign_n,
        "called_away_count": called_n,
        "trade_count": len(trades),
        "equity_curve_sample": [round(e, 2) for e in equity_curve[:: max(1, len(equity_curve) // 50)]],
        "note": "合成权利金近似回测,用于参数相对比较,非实盘预期。",
    }


def compare_profiles(symbol: str, profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    results = []
    for prof in profiles:
        name = prof.get("name") or "unnamed"
        r = run_wheel_backtest(symbol, prof.get("params") or prof)
        r["profile_name"] = name
        results.append(r)
    ok = [r for r in results if r.get("ok")]
    ok.sort(key=lambda x: x.get("cagr_pct") or -999, reverse=True)
    return {"symbol": symbol, "results": results, "best": ok[0] if ok else None}
