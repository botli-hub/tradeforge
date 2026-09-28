"""Auditable score evidence, forward-looking review and execution measurement."""
import math
from datetime import datetime, timezone


def evidence(item):
    return {
        'facts': {k: item.get(k) for k in ('bid','ask','quote_asof','strike','expiry','dte','volume','open_interest','contract_size')},
        'estimates': {k: item.get(k) for k in ('delta','iv','pop','scenario_ev_pct')},
        'rules': {'score': item.get('score'), 'factors': item.get('score_factors', item.get('factors')), 'ema_type': item.get('ema_type')},
        'validated_edge': False, 'calibration': '证据不足：排序分与 delta 概率近似均未经样本外校准',
    }


def review_position(item, decision):
    strike = item.get('strike')
    price = item.get('buyback_ask') or item.get('ask')
    qty = float(item.get('qty') or 1)
    size = float(item.get('contract_size') or 100)
    remaining = price*qty*size if isinstance(price, (int,float)) and price > 0 else None
    would = decision.get('would_open_today')
    reasons = list(decision.get('would_open_reasons') or [])
    if would is False:
        reasons.append('当前条件不支持重新开仓；重新比较减仓、退出和继续持有')
    if decision.get('early_assign_risk'):
        reasons.append('存在提前指派风险，核查现金或交割股份及除息日')
    return {'would_open_today': would, 'reasons': reasons,
            'remaining_premium_at_risk': remaining,
            'assignment_cash': strike*qty*size if strike and item.get('side') == 'PUT' else None,
            'review_required': would is not True or bool(decision.get('early_assign_risk')),
            'cost_basis_role': '历史成本用于损益核算；不作为未来收益优势的证据',
            'roll_rule': 'Roll 拆分为旧腿已实现盈亏与新腿独立风险，净收权利金不等于盈利'}


def attribution(nav, cycles):
    from app.core.wheel_cc_legs import expand_open_option_rows
    option_realized = sum(float(c.get('option_realized') or 0) for c in cycles)
    stock_realized = sum(float(c.get('stock_realized') or 0) for c in cycles)
    fees = sum(float(c.get('total_fees') or 0) for c in cycles)
    open_premium = sum(float(l.get('open_price') or 0)*float(l.get('open_qty') or 0)*float(l.get('open_contract_size') or 100)
                       for l in expand_open_option_rows(cycles))
    pieces = {'option_realized_gross': option_realized, 'stock_realized_gross': stock_realized,
              'option_unrealized': open_premium+nav['option_mtm'],
              'stock_unrealized': nav['stock_mv']-nav['stock_cost'], 'fees': -fees}
    total = sum(pieces.values())
    residual = nav['equity']-nav['starting_cash']-total
    return {**{k: round(v, 4) for k,v in pieces.items()}, 'total_pnl': round(total,4),
            'reconciliation_residual': round(residual,4),
            'reconciled': abs(residual)<.02 and not nav.get('reconciliation_required') and not nav.get('valuation_incomplete'),
            'cash_income': None, 'cash_income_note': '未登记现金利息/分红，不推算收入',
            'valuation_incomplete': nav.get('valuation_incomplete', False)}


def execution_metrics(payload):
    """Positive shortfall means adverse execution for either trade direction."""
    p = dict(payload)
    if p.get('side') not in ('BUY','SELL') or p.get('status') not in ('pending','filled','partial','cancelled'):
        raise ValueError('side/status 无效')
    for k in ('requested_qty','filled_qty','contract_size'):
        v = float(p.get(k, 0 if k == 'filled_qty' else 1))
        if not math.isfinite(v) or v < 0 or (k != 'filled_qty' and v == 0) or int(v) != v:
            raise ValueError(f'{k} 必须为有效整数')
        p[k] = int(v)
    if p['filled_qty'] > p['requested_qty']:
        raise ValueError('成交数量超过委托数量')
    if p['status'] == 'filled' and p['filled_qty'] != p['requested_qty']:
        raise ValueError('filled 状态须全部成交')
    if p['status'] == 'partial' and not 0 < p['filled_qty'] < p['requested_qty']:
        raise ValueError('partial 状态须部分成交')
    if p['status'] == 'pending' and p['filled_qty']:
        raise ValueError('pending 状态不能已有成交')
    for k in ('reference_price','limit_price','fill_price','mark_after'):
        if p.get(k) is not None:
            v = float(p[k])
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{k} 无效')
            p[k] = v
    def timestamp(k):
        value = p.get(k)
        if not value:
            return None
        t = datetime.fromisoformat(value.replace('Z','+00:00'))
        if t.tzinfo is None:
            raise ValueError(f'{k} 必须包含时区')
        return t
    sent, filled, marked = (timestamp(k) for k in ('submitted_at','filled_at','mark_after_at'))
    if sent is None:
        raise ValueError('需要委托时间')
    if filled and filled < sent:
        raise ValueError('成交时间早于委托时间')
    if p['filled_qty'] and (filled is None or p.get('fill_price') is None):
        raise ValueError('成交记录需要成交价和时间')
    if p.get('mark_after') is not None and (not marked or not filled or marked <= filled):
        raise ValueError('成交后报价须有晚于成交的时间')
    units = p['filled_qty']*p['contract_size']
    sign = 1 if p['side']=='BUY' else -1
    ref, fill = p.get('reference_price'), p.get('fill_price')
    shortfall = sign*(fill-ref)*units if units and fill is not None and ref is not None else None
    markout = sign*(p['mark_after']-fill)*units if units and fill is not None and p.get('mark_after') is not None else None
    return {**p, 'shortfall_dollars': shortfall, 'markout_dollars': markout,
            'fill_latency_seconds': (filled-sent).total_seconds() if filled else None,
            'fill_rate': p['filled_qty']/p['requested_qty'], 'source': 'user_recorded_not_broker_confirmed'}


def roll_review(open_price, close, opening):
    units=float(close['qty'])*float(close['contract_size'])
    fee_close=float(close.get('fee') or 0);fee_open=float(opening.get('fee') or 0)
    closed_pnl=(float(open_price)-float(close['price']))*units-fee_close if open_price is not None else None
    new_credit=float(opening['price'])*units-fee_open
    return {'closed_leg_pnl_before_original_entry_fee':closed_pnl,
            'new_leg_premium_after_fee':new_credit,
            'net_cashflow':new_credit-float(close['price'])*units-fee_close,
            'new_assignment_notional':float(opening['strike'])*units if opening['trade_type']=='SELL_PUT' else None,
            'new_expiry':opening['expiry'],
            'note':'净收款不是盈利；旧腿损益尚未分摊原开仓手续费，新腿仍有完整持仓风险'}
