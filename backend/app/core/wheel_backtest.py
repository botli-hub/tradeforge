"""Wheel scenario simulation and chronological, paired quote-data research.

Synthetic mode is an HV/European-price scenario, never validated performance.
Historical mode requires dated option bid/ask snapshots for every open leg.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import math
from statistics import NormalDist
from typing import Any, Dict, List, Optional

N = NormalDist()


@dataclass
class BTParams:
    delta: float = .25
    dte: int = 30
    profit_take: float = .5
    floor_pct: float = .90
    min_annualized: float = 15
    initial_capital: float = 100000
    contracts: int = 1
    contract_size: int = 100
    skip_earnings: bool = False
    fee_per_contract: float = .65
    slippage_pct: float = .02
    risk_free_rate: float = .03
    warmup_bars: int = 60

    def validate(self):
        for k, v in asdict(self).items():
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"{k} 必须为有限数字")
        if not 0 < self.delta < .5 or not 0 < self.profit_take <= 1:
            raise ValueError("delta 必须在 (0,0.5), profit_take 在 (0,1]")
        if not 0 < self.floor_pct <= 1 or self.initial_capital <= 0:
            raise ValueError("floor_pct/initial_capital 无效")
        if self.fee_per_contract < 0 or not 0 <= self.slippage_pct < 1 or self.min_annualized < 0:
            raise ValueError("费用、滑点、年化阈值无效")
        for k in ("dte", "contracts", "contract_size", "warmup_bars"):
            v = getattr(self, k)
            if v < 1 or int(v) != v:
                raise ValueError(f"{k} 必须为正整数")


def option_price(spot, strike, years, sigma, side, rate=.03):
    """European benchmark; intrinsic floor avoids impossible assignment values."""
    intrinsic = max(strike - spot, 0) if side == "PUT" else max(spot - strike, 0)
    if years <= 0:
        return intrinsic
    sigma = max(sigma, .001)
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    discount = math.exp(-rate * years)
    value = (strike * discount * N.cdf(-d2) - spot * N.cdf(-d1)) if side == "PUT" else (spot * N.cdf(d1) - strike * discount * N.cdf(d2))
    return max(intrinsic, value)


def strike_for_delta(spot, sigma, dte, delta, side, rate=.03):
    t = dte / 365.0
    d1 = N.inv_cdf(1 - delta if side == "PUT" else delta)
    return spot * math.exp((rate + sigma * sigma / 2) * t - d1 * sigma * math.sqrt(t))


def _hv_from_closes(closes, window=20):
    seg = closes[-window - 1:]
    rets = [math.log(b / a) for a, b in zip(seg, seg[1:])]
    if len(rets) < 2:
        return .25
    mean = sum(rets) / len(rets)
    return max(.01, math.sqrt(sum((r - mean)**2 for r in rets) / (len(rets) - 1) * 252))


def _bars(rows):
    result = []
    for r in rows:
        d = date.fromisoformat(str(r.get("date") or r.get("ts"))[:10])
        close = float(r["close"])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("日线 close 必须为正有限数")
        if result and d <= result[-1]["date"]:
            raise ValueError("日线日期必须严格递增、无重复")
        result.append({"date": d, "close": close})
    return result


def run_on_bars(rows, params=None, *, quotes=None, timing_only=False, ema_period=50):
    p = BTParams(**{k:v for k,v in (params or {}).items() if k in BTParams.__dataclass_fields__})
    p.validate()
    bars = _bars(rows)
    if len(bars) <= p.warmup_bars:
        return {"ok": False, "error": "日线不足预热长度"}
    if p.skip_earnings:
        return {"ok": False, "error": "缺少历史财报日数据,不能宣称已过滤财报"}
    historical = quotes is not None
    if timing_only and not historical:
        return {"ok": False, "error": "触线研究需要历史合约报价"}
    chain_by_day, touch_by_day = {}, {}
    histories = {}
    for q in sorted(quotes or [], key=lambda x: str(x["date"])):
        day = date.fromisoformat(str(q["date"])[:10])
        side = str(q["side"]).upper()
        bid, ask, strike = float(q["bid"]), float(q["ask"]), float(q["strike"])
        expiry = date.fromisoformat(str(q["expiry"])[:10])
        delta = abs(float(q["delta"]))
        if side not in ("PUT", "CALL") or not all(math.isfinite(v) for v in (bid, ask, strike, delta)) or bid < 0 or ask < bid or strike <= 0 or not 0 <= delta <= 1:
            raise ValueError("历史期权报价无效")
        code = str(q["contract_code"])
        if any(x["contract_code"] == code for x in chain_by_day.get(day, [])):
            raise ValueError("每合约每日只接受一个收盘快照")
        row = dict(q, side=side, bid=bid, ask=ask, strike=strike, expiry=expiry, delta=delta, contract_code=code)
        chain_by_day.setdefault(day, []).append(row)
        # Daily EMA crossover uses only past snapshots. Execution is next observed day.
        h = histories.setdefault(code, [])
        mid = (bid + ask) / 2
        if len(h) >= ema_period:
            ema = sum(h[:ema_period]) / ema_period
            for value in h[ema_period:]:
                ema += 2 / (ema_period + 1) * (value - ema)
            if h[-1] < ema <= mid:
                touch_by_day.setdefault(day, set()).add(code)
        h.append(mid)
    cash, shares, cost = p.initial_capital, 0, 0.0
    leg = None
    curve, trades = [], []
    qty = p.contracts * p.contract_size
    fee = p.contracts * p.fee_per_contract
    assign_n = called_n = 0
    premium_net = 0.0
    assigned_at = None
    recovery_days = []
    idle_days = 0
    previous_day = None
    def book(day, kind, flow, **details):
        trades.append({"date": day.isoformat(), "type": kind, "cashflow": round(flow, 8), **details})
    for i in range(p.warmup_bars, len(bars)):
        day, spot = bars[i]["date"], bars[i]["close"]
        sigma = _hv_from_closes([x["close"] for x in bars[:i+1]])
        day_chain = chain_by_day.get(day, [])
        if historical and not day_chain:
            return {"ok": False, "error": f"{day} 缺少历史合约快照;不跳过缺失日或自动合成", "missing_date": day.isoformat()}
        def mark(open_leg, closing=False):
            left = (open_leg["expiry"] - day).days
            if left <= 0:
                return max(open_leg["strike"] - spot, 0) if open_leg["side"] == "PUT" else max(spot - open_leg["strike"], 0)
            if historical:
                quote = next((q for q in day_chain if q["contract_code"] == open_leg["contract_code"]), None)
                if quote is None:
                    raise ValueError(f"{day} 缺少在场合约 {open_leg['contract_code']} 报价")
                return quote["ask"] if closing else (quote["bid"] + quote["ask"]) / 2
            value = option_price(spot, open_leg["strike"], left / 365, sigma, open_leg["side"], p.risk_free_rate)
            return value * (1 + p.slippage_pct) if closing else value
        expired_today = False
        if leg:
            if day >= leg["expiry"]:
                # Never settle a Friday expiry against next Monday's stock price.
                if day != leg["expiry"]:
                    return {"ok": False, "error": "缺少到期日标的收盘价"}
                intrinsic = mark(leg)
                if intrinsic > 0 and leg["side"] == "PUT":
                    flow = -leg["strike"] * qty
                    cash += flow; shares = qty; cost = leg["strike"]
                    assign_n += 1; assigned_at = day
                    book(day, "ASSIGNED", flow, strike=cost)
                elif intrinsic > 0:
                    flow = leg["strike"] * qty
                    cash += flow; shares = 0; called_n += 1
                    book(day, "CALLED_AWAY", flow, strike=leg["strike"])
                else:
                    book(day, "EXPIRE", 0)
                leg = None; expired_today = True
            elif mark(leg, True) * qty + fee <= leg["premium"] * qty * (1-p.profit_take):
                flow = -mark(leg, True) * qty - fee
                cash += flow; premium_net += flow
                book(day, "BUY_"+leg["side"]+"_CLOSE", flow)
                leg = None
        if assigned_at is not None and cash + shares * spot - (mark(leg) * qty if leg else 0) >= p.initial_capital:
            recovery_days.append((day - assigned_at).days); assigned_at = None
        # No new sale on the last observation; terminal obligations are liquidated below.
        if leg is None and i < len(bars)-1 and not expired_today:
            side = "CALL" if shares else "PUT"
            chosen = None
            if historical:
                valid = [q for q in day_chain if q["side"] == side and q["bid"] > 0 and q["expiry"] > day
                         and q["expiry"] in {x["date"] for x in bars[i+1:]}
                         and abs((q["expiry"]-day).days-p.dte) <= 7
                         and abs(q["delta"]-p.delta) <= .05
                         and (side != "PUT" or q["strike"] <= spot*p.floor_pct)
                         and (side != "CALL" or q["strike"] >= cost)]
                if timing_only:
                    valid = [q for q in valid if q["contract_code"] in touch_by_day.get(previous_day, set())]
                if valid:
                    chosen = min(valid, key=lambda q: (abs(q["delta"]-p.delta), q["contract_code"]))
                    chosen = dict(chosen, premium=chosen["bid"])
            else:
                target = day + timedelta(days=p.dte)
                future_dates = [b["date"] for b in bars[i+1:] if b["date"] >= target]
                expiry = future_dates[0] if future_dates else target
                days = (expiry-day).days
                strike = strike_for_delta(spot, sigma, days, p.delta, side, p.risk_free_rate)
                strike = min(strike, spot*p.floor_pct) if side == "PUT" else max(strike, cost)
                prem = option_price(spot,strike,days/365,sigma,side,p.risk_free_rate)*(1-p.slippage_pct)
                chosen = {"side":side,"strike":strike,"expiry":expiry,"premium":prem}
            if chosen:
                prem, strike = chosen["premium"], chosen["strike"]
                days = (chosen["expiry"]-day).days
                ann = (prem*qty-fee)/(strike*qty)*365/days*100
                funded = cash >= strike*qty+fee if side == "PUT" else shares >= qty and cash >= fee
                if funded and ann >= p.min_annualized:
                    flow = prem*qty-fee
                    cash += flow; premium_net += flow; leg = chosen
                    book(day,"SELL_"+side,flow,strike=strike,premium=prem,expiry=chosen["expiry"].isoformat())
        liability = mark(leg)*qty if leg else 0
        curve.append({"date":day.isoformat(),"equity":cash+shares*spot-liability,
                      "cash":cash,"stock_mv":shares*spot,"option_liability":liability})
        if not shares and not leg:
            idle_days += 1
        previous_day = day
    if leg:
        flow = -mark(leg,True)*qty-fee
        cash += flow; premium_net += flow
        book(day,"TERMINAL_BUY_"+leg["side"],flow)
        leg = None
    final = cash+shares*bars[-1]["close"]
    curve[-1].update(equity=final,cash=cash,option_liability=0)
    peak, dd = p.initial_capital, 0.0
    for row in curve:
        peak = max(peak,row["equity"])
        dd = max(dd,(peak-row["equity"])/peak)
    elapsed = (bars[-1]["date"]-bars[p.warmup_bars]["date"]).days
    years = max(elapsed/365.25,1/365.25)
    return {"ok":True,"params":asdict(p),"bars":len(bars),"trading_days":len(curve),
        "final_equity":round(final,2),"total_return_pct":round((final/p.initial_capital-1)*100,4),
        "cagr_pct":round(((final/p.initial_capital)**(1/years)-1)*100,4) if final>0 else -100,
        "max_drawdown_pct":round(dd*100,4),"premium_sum_est":round(premium_net,2),
        "assign_count":assign_n,"called_away_count":called_n,"trade_count":len(trades),
        "cash":cash,"shares":shares,"trades":trades,"equity_curve":curve,
        "equity_curve_sample":[round(r["equity"],2) for r in curve[::max(1,len(curve)//50)]],
        "idle_days":idle_days,"assignment_recovery_days":recovery_days,
        "unrecovered_assignment":assigned_at is not None,
        "evidence_level":"historical_daily_quotes" if historical else "synthetic_scenario",
        "validated_edge":False,"timing_only":timing_only,
        "note":"含期权负债、费用、滑点/买卖价差；无提前行权/分红/税费。历史对照仅日线 EMA，不能外推 1h 触线。" if historical else "HV 欧式定价情景模拟，含完整盯市；非历史期权回测，不用于证明策略优势或最优参数。"}


def load_daily_bars(symbol, limit=504):
    from app.data.database import get_db
    conn=get_db()
    try:
        return [dict(r) for r in reversed(conn.execute("SELECT ts, close FROM kline_bars WHERE symbol=? AND timeframe='1d' ORDER BY ts DESC LIMIT ?",(symbol,limit)).fetchall())]
    finally:
        conn.close()


def run_wheel_backtest(symbol, params=None, lookback_bars=504):
    try:
        return dict(run_on_bars(load_daily_bars(symbol,lookback_bars),params),symbol=symbol)
    except (ValueError,TypeError,OverflowError) as e:
        return {"ok":False,"symbol":symbol,"error":str(e)}


def compare_profiles(symbol, profiles):
    results=[dict(run_wheel_backtest(symbol,p.get("params") or p),profile_name=p.get("name","unnamed")) for p in profiles]
    return {"symbol":symbol,"results":results,"best":None,
        "note":"合成情景不评选最优参数；请使用独立历史报价样本做对照。"}


def compare_timing(rows, quotes, params=None, ema_period=50):
    if not quotes:
        return {"ok":False,"error":"需要历史合约 bid/ask、delta、执行价、到期日和逐日日期；现有触线日志不能替代历史期权报价", "validated_edge":False}
    if ema_period not in (50,200):
        return {"ok":False,"error":"EMA 周期仅支持 50/200"}
    try:
        base=run_on_bars(rows,params,quotes=quotes)
        timing=run_on_bars(rows,params,quotes=quotes,timing_only=True,ema_period=ema_period)
    except (ValueError,TypeError,KeyError) as e:
        return {"ok":False,"error":str(e),"validated_edge":False}
    valid=bool(base.get("ok") and timing.get("ok"))
    return {"ok":valid,"baseline":base,"timing":timing,"validated_edge":False,
        "total_return_difference_pp":round(timing["total_return_pct"]-base["total_return_pct"],4) if valid else None,
        "assumptions":"同样本/参数/资金/费用；收盘信号最早下一日执行。日线 EMA 对照不验证 1h 策略，须另做样本外检验。"}
