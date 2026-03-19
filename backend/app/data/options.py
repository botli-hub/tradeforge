"""期权链与收益分析（MVP）"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import NormalDist
from typing import Any, Dict, List, Optional


_NORMAL = NormalDist()


@dataclass
class OptionQuote:
    option_symbol: str
    underlying_symbol: str
    expiry: str
    strike: float
    option_type: str  # CALL / PUT
    bid: float
    ask: float
    last: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    volume: int
    open_interest: int
    intrinsic_value: float
    time_value: float
    contract_size: int = 100


@dataclass
class OptionLeg:
    option_type: str  # CALL / PUT
    side: str         # LONG / SHORT
    strike: float
    premium: float
    quantity: int = 1
    contract_size: int = 100


def next_expirations(count: int = 6, start: Optional[date] = None) -> List[str]:
    start = start or date.today()
    expiries: List[str] = []
    current = start
    while len(expiries) < count:
        if current.weekday() == 4:  # Friday
            expiries.append(current.isoformat())
        current += timedelta(days=1)
    return expiries


def _strike_step(spot: float) -> float:
    if spot < 25:
        return 1.0
    if spot < 100:
        return 2.5
    if spot < 250:
        return 5.0
    if spot < 500:
        return 10.0
    return 20.0


def _round_to_step(value: float, step: float) -> float:
    return round(round(value / step) * step, 2)


def _seeded_random(*parts: str) -> random.Random:
    joined = '|'.join(parts)
    seed = int(hashlib.md5(joined.encode('utf-8')).hexdigest()[:8], 16)
    return random.Random(seed)


def _option_metrics(spot: float, strike: float, years_to_expiry: float, iv: float, option_type: str, risk_free: float = 0.03):
    option_type = option_type.upper()
    if years_to_expiry <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(spot - strike, 0.0) if option_type == 'CALL' else max(strike - spot, 0.0)
        return {
            'price': intrinsic,
            'delta': 1.0 if option_type == 'CALL' and spot > strike else (-1.0 if option_type == 'PUT' and spot < strike else 0.0),
            'gamma': 0.0,
            'theta': 0.0,
            'vega': 0.0,
            'intrinsic': intrinsic,
            'time_value': 0.0,
        }

    sqrt_t = math.sqrt(years_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free + 0.5 * iv * iv) * years_to_expiry) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    pdf = _NORMAL.pdf(d1)

    if option_type == 'CALL':
        price = spot * _NORMAL.cdf(d1) - strike * math.exp(-risk_free * years_to_expiry) * _NORMAL.cdf(d2)
        delta = _NORMAL.cdf(d1)
        theta = (-(spot * pdf * iv) / (2 * sqrt_t) - risk_free * strike * math.exp(-risk_free * years_to_expiry) * _NORMAL.cdf(d2)) / 365
        intrinsic = max(spot - strike, 0.0)
    else:
        price = strike * math.exp(-risk_free * years_to_expiry) * _NORMAL.cdf(-d2) - spot * _NORMAL.cdf(-d1)
        delta = _NORMAL.cdf(d1) - 1
        theta = (-(spot * pdf * iv) / (2 * sqrt_t) + risk_free * strike * math.exp(-risk_free * years_to_expiry) * _NORMAL.cdf(-d2)) / 365
        intrinsic = max(strike - spot, 0.0)

    gamma = pdf / (spot * iv * sqrt_t)
    vega = spot * pdf * sqrt_t / 100
    time_value = max(price - intrinsic, 0.0)

    return {
        'price': max(price, intrinsic),
        'delta': delta,
        'gamma': gamma,
        'theta': theta,
        'vega': vega,
        'intrinsic': intrinsic,
        'time_value': time_value,
    }


def generate_option_chain(symbol: str, expiry: str, spot_price: float, strike_count: int = 8) -> Dict[str, Any]:
    expiry_date = date.fromisoformat(expiry)
    days_to_expiry = max((expiry_date - date.today()).days, 1)
    years_to_expiry = max(days_to_expiry / 365.0, 1 / 365.0)
    step = _strike_step(spot_price)
    center = _round_to_step(spot_price, step)
    rng = _seeded_random(symbol.upper(), expiry)

    strikes = [round(center + step * offset, 2) for offset in range(-strike_count, strike_count + 1)]
    strikes = [strike for strike in strikes if strike > 0]

    contracts: List[Dict[str, Any]] = []
    for strike in strikes:
        moneyness = abs(math.log(max(spot_price, 0.01) / strike))
        base_iv = 0.24 + min(0.18, moneyness * 0.65) + min(days_to_expiry / 3650, 0.08)

        for option_type in ('CALL', 'PUT'):
            metrics = _option_metrics(spot_price, strike, years_to_expiry, base_iv, option_type)
            last = round(metrics['price'], 2)
            spread = max(0.03, round(last * 0.03, 2))
            bid = round(max(last - spread / 2, 0.01), 2)
            ask = round(last + spread / 2, 2)
            volume = rng.randint(20, 2500)
            open_interest = rng.randint(100, 9000)
            contract = OptionQuote(
                option_symbol=f"{symbol.upper()}-{expiry}-{option_type[0]}-{strike:.2f}",
                underlying_symbol=symbol.upper(),
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                bid=bid,
                ask=ask,
                last=last,
                iv=round(base_iv, 4),
                delta=round(metrics['delta'], 4),
                gamma=round(metrics['gamma'], 4),
                theta=round(metrics['theta'], 4),
                vega=round(metrics['vega'], 4),
                volume=volume,
                open_interest=open_interest,
                intrinsic_value=round(metrics['intrinsic'], 2),
                time_value=round(metrics['time_value'], 2),
            )
            contracts.append(contract.__dict__)

    return {
        'symbol': symbol.upper(),
        'expiry': expiry,
        'spot_price': round(spot_price, 2),
        'days_to_expiry': days_to_expiry,
        'contracts': contracts,
    }


def _payoff_for_leg(price: float, leg: OptionLeg) -> float:
    option_type = leg.option_type.upper()
    side = leg.side.upper()
    multiplier = 1 if side == 'LONG' else -1

    if option_type == 'CALL':
        intrinsic = max(price - leg.strike, 0.0)
    else:
        intrinsic = max(leg.strike - price, 0.0)

    pnl_per_share = intrinsic - leg.premium if side == 'LONG' else leg.premium - intrinsic
    return pnl_per_share * leg.quantity * leg.contract_size


def _interpolate_breakevens(points: List[Dict[str, float]]) -> List[float]:
    result: List[float] = []
    for i in range(1, len(points)):
        prev = points[i - 1]
        cur = points[i]
        if prev['pnl'] == 0:
            result.append(round(prev['underlying_price'], 2))
            continue
        if prev['pnl'] * cur['pnl'] < 0:
            ratio = abs(prev['pnl']) / (abs(prev['pnl']) + abs(cur['pnl']))
            be = prev['underlying_price'] + (cur['underlying_price'] - prev['underlying_price']) * ratio
            result.append(round(be, 2))
    return sorted(set(result))


def _summary_from_strategy(strategy: str, legs: List[OptionLeg]) -> Dict[str, Any]:
    strategy = (strategy or '').lower()
    if strategy == 'long_call' and len(legs) == 1:
        leg = legs[0]
        debit = leg.premium * leg.quantity * leg.contract_size
        return {
            'max_profit': None,
            'max_loss': round(debit, 2),
            'breakeven_points': [round(leg.strike + leg.premium, 2)],
        }
    if strategy == 'long_put' and len(legs) == 1:
        leg = legs[0]
        debit = leg.premium * leg.quantity * leg.contract_size
        max_profit = max(0.0, (leg.strike - leg.premium) * leg.quantity * leg.contract_size)
        return {
            'max_profit': round(max_profit, 2),
            'max_loss': round(debit, 2),
            'breakeven_points': [round(leg.strike - leg.premium, 2)],
        }
    if strategy == 'bull_call_spread' and len(legs) == 2:
        long_leg = next((leg for leg in legs if leg.side.upper() == 'LONG'), None)
        short_leg = next((leg for leg in legs if leg.side.upper() == 'SHORT'), None)
        if long_leg and short_leg:
            net_debit = long_leg.premium - short_leg.premium
            width = short_leg.strike - long_leg.strike
            multiplier = long_leg.quantity * long_leg.contract_size
            return {
                'max_profit': round(max(0.0, (width - net_debit) * multiplier), 2),
                'max_loss': round(max(0.0, net_debit * multiplier), 2),
                'breakeven_points': [round(long_leg.strike + net_debit, 2)],
            }
    if strategy == 'bear_put_spread' and len(legs) == 2:
        long_leg = next((leg for leg in legs if leg.side.upper() == 'LONG'), None)
        short_leg = next((leg for leg in legs if leg.side.upper() == 'SHORT'), None)
        if long_leg and short_leg:
            net_debit = long_leg.premium - short_leg.premium
            width = long_leg.strike - short_leg.strike
            multiplier = long_leg.quantity * long_leg.contract_size
            return {
                'max_profit': round(max(0.0, (width - net_debit) * multiplier), 2),
                'max_loss': round(max(0.0, net_debit * multiplier), 2),
                'breakeven_points': [round(long_leg.strike - net_debit, 2)],
            }
    return {}


def calculate_payoff(strategy: str, underlying_price: float, legs_payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    legs = [OptionLeg(**payload) for payload in legs_payload]
    if not legs:
        return {
            'strategy': strategy,
            'summary': {'max_profit': 0, 'max_loss': 0, 'breakeven_points': []},
            'points': [],
        }

    strikes = [leg.strike for leg in legs]
    floor_price = max(0.01, min(min(strikes) * 0.7, underlying_price * 0.7))
    ceil_price = max(max(strikes) * 1.3, underlying_price * 1.3)
    step = max((ceil_price - floor_price) / 80, 0.5)

    points: List[Dict[str, float]] = []
    price = floor_price
    while price <= ceil_price + 1e-9:
        pnl = sum(_payoff_for_leg(price, leg) for leg in legs)
        points.append({
            'underlying_price': round(price, 2),
            'pnl': round(pnl, 2),
        })
        price += step

    fallback_summary = {
        'max_profit': round(max(point['pnl'] for point in points), 2),
        'max_loss': round(abs(min(point['pnl'] for point in points)), 2),
        'breakeven_points': _interpolate_breakevens(points),
    }
    exact_summary = _summary_from_strategy(strategy, legs)
    summary = {**fallback_summary, **exact_summary}

    return {
        'strategy': strategy,
        'summary': summary,
        'points': points,
    }
