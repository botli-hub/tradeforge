"""Pre-expiry, joint spot/IV/liquidity stress. Model estimates, not loss bounds."""
import math
from datetime import date
from statistics import NormalDist
from app.core.wheel_backtest import option_price

DEFAULTS = {'enabled': False, 'max_stress_loss_pct': 20., 'per_trade_loss_pct': 2.,
            'leveraged_trade_loss_pct': 1., 'drawdown_stop_pct': 15.,
            'leveraged_symbols': ['TQQQ','SQQQ','SOXL','SOXS','UPRO','SPXU'],
            'leveraged_allowed': False, 'fee_per_contract': .65}
SCENARIOS = [(-.1,.2,.05), (-.2,.4,.10), (-.4,.6,.20), (.2,.2,.10)]


def settings(config):
    cfg = {**DEFAULTS, **(config.get('wheel_risk_budget') or {})}
    for k in ('max_stress_loss_pct','per_trade_loss_pct','leveraged_trade_loss_pct','drawdown_stop_pct'):
        if not isinstance(cfg[k], (int,float)) or not math.isfinite(cfg[k]) or not 0 < cfg[k] <= 100:
            raise ValueError(f'{k} 必须在 (0,100]')
    if not isinstance(cfg['fee_per_contract'],(int,float)) or not math.isfinite(cfg['fee_per_contract']) or cfg['fee_per_contract'] < 0:
        raise ValueError('手续费无效')
    return cfg


def implied_sigma(spot, strike, years, side, mark):
    low, high = .001, 5.
    if mark < option_price(spot,strike,years,low,side)-1e-6 or mark > option_price(spot,strike,years,high,side)+1e-6:
        return None
    for _ in range(55):
        mid=(low+high)/2
        if option_price(spot,strike,years,mid,side) < mark: low=mid
        else: high=mid
    return (low+high)/2


def stress_nav(nav, asof=None, fee=.65):
    today = date.fromisoformat(str(asof)[:10]) if asof else date.today()
    spots={r['symbol']:r.get('spot') for r in nav.get('stock_rows',[])}
    spots.update(nav.get('spots_used') or {})
    issues=[]; legs=[]; greek={'delta_dollars':sum(r['mv'] for r in nav.get('stock_rows',[])), 'gamma_1pct_dollars':0., 'vega_1point_dollars':0.}
    if nav.get('valuation_incomplete'): issues.append('持仓估值不完整')
    if any(not math.isfinite(float(nav.get(k) or 0)) for k in ('cash','equity')):
        issues.append('账户估值无效')
    for r in nav.get('option_rows',[]):
        spot=spots.get(r['symbol']); k=r.get('strike'); mark=r.get('mark'); expiry=r.get('expiry')
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (spot,k,r.get('qty'),r.get('contract_size',100))) or not isinstance(mark,(int,float)) or not math.isfinite(mark) or mark<0 or not expiry or r.get('mark_fallback') or r.get('side') not in ('PUT','CALL'):
            issues.append(f"{r.get('contract_code')} 缺少价格/期限/执行价"); continue
        try:
            t=(date.fromisoformat(str(expiry)[:10])-today).days/365
        except ValueError:
            issues.append(f"{r.get('contract_code')} 到期日无效"); continue
        if t<=0:
            issues.append(f"{r.get('contract_code')} 到期待核对"); continue
        sigma=implied_sigma(spot,k,t,r['side'],mark)
        if sigma is None:
            issues.append(f"{r.get('contract_code')} 无法由市价校准波动率"); continue
        units=r['qty']*r.get('contract_size',100)
        d1=(math.log(spot/k)+(.03+sigma*sigma/2)*t)/(sigma*math.sqrt(t))
        pdf=math.exp(-d1*d1/2)/math.sqrt(2*math.pi)
        delta=NormalDist().cdf(d1)-(1 if r['side']=='PUT' else 0)
        greek['delta_dollars']-=delta*units*spot
        greek['gamma_1pct_dollars']-=.5*pdf/(spot*sigma*math.sqrt(t))*(spot*.01)**2*units
        greek['vega_1point_dollars']-=spot*pdf*math.sqrt(t)*.01*units
        legs.append((r,spot,k,mark,t,sigma,units))
    scenarios=[]
    for shock,iv_add,spread in SCENARIOS:
        stock=sum(r['mv']*(1+shock) for r in nav.get('stock_rows',[]))
        liability=0.
        for r,spot,k,mark,t,sigma,units in legs:
            price=option_price(spot*(1+shock),k,max(0,t-1/365),sigma+iv_add,r['side'])*(1+spread)
            liability+=price*units+fee*r['qty']
        equity=nav['cash']+stock-liability
        scenarios.append({'spot_shock_pct':shock*100,'iv_add_points':iv_add*100,'exit_spread_pct':spread*100,
                          'equity':round(equity,2) if not issues else None,
                          'loss':round(max(0,nav['equity']-equity),2) if not issues else None})
    expiries={}
    for r in nav.get('option_rows',[]):
        week=str(r.get('expiry') or '')[:10]
        expiries[week]=expiries.get(week,0)+float(r.get('strike') or 0)*r['qty']*r.get('contract_size',100)
    return {'ok':not issues,'issues':issues,'scenarios':scenarios,
            'worst_loss':max((s['loss'] for s in scenarios),default=0) if not issues else None,
            'greeks':{k:round(v,2) if not issues else None for k,v in greek.items()},
            'expiry_notional':expiries,'model':'European price + intrinsic floor, market-implied IV; +1 day, adverse exit spread',
            'limitations':'非亏损上界；不模拟美式提前行权、波动率曲面或杠杆ETF多日路径'}


def risk_gate(nav, config, opportunity=None, drawdown=None, asof=None):
    from copy import deepcopy
    cfg=settings(config);current=stress_nav(nav,asof, cfg['fee_per_contract']); projected=deepcopy(nav)
    violations=[]; max_qty=None;candidate_loss=None
    if nav['equity'] <= 0:
        violations.append('账户权益非正，无法分配风险预算')
    # Check each extant leg too: planned registration must not bypass candidate sizing.
    for leg in nav.get('option_rows', []):
        single_nav = {**nav, 'stock_rows': [], 'option_rows': [leg], 'cash': 0,
                      'equity': -float(leg.get('mark') or 0)*leg['qty']*leg.get('contract_size',100)}
        loss = stress_nav(single_nav, asof, cfg['fee_per_contract'])
        leveraged = leg['symbol'].removeprefix('US.') in cfg['leveraged_symbols']
        fraction = cfg['leveraged_trade_loss_pct'] if leveraged else cfg['per_trade_loss_pct']
        if loss['ok'] and loss['worst_loss'] > nav['equity']*fraction/100:
            violations.append(f"{leg.get('contract_code')} 在场合约压力损失超单笔预算")
        if leveraged and not cfg['leveraged_allowed']:
            violations.append('杠杆标的未获风险预算准入')
    for holding in nav.get('stock_rows', []):
        if holding['symbol'].removeprefix('US.') in cfg['leveraged_symbols'] and not cfg['leveraged_allowed']:
            violations.append('杠杆标的未获风险预算准入')
    if opportunity:
        o=opportunity;side=o.get('side','PUT');qty=o.get('suggest_qty', o.get('qty', 1));size=o.get('contract_size',100)
        code=o.get('contract_code') or 'candidate';symbol=o.get('symbol',''); bid=o.get('bid');ask=o.get('ask');strike=o.get('strike')
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (bid,ask,strike,qty,size)) or ask<bid or int(qty)!=qty or int(size)!=size or side not in ('PUT','CALL'):
            violations.append('候选报价/数量无效')
        else:
            unitnav={'cash':0,'equity':-ask*size,'stock_rows':[], 'option_rows':[dict(symbol=symbol,side=side,contract_code=code,qty=1,contract_size=size,strike=strike,expiry=o.get('expiry'),mark=ask)],
                     'spots_used':{symbol:o.get('spot') or o.get('spot_price') or (nav.get('spots_used') or {}).get(symbol)}}
            single=stress_nav(unitnav,asof,cfg['fee_per_contract'])
            candidate_loss=(single['worst_loss']+(ask-bid)*size+cfg['fee_per_contract']) if single['ok'] else None
            leveraged=symbol.removeprefix('US.') in cfg['leveraged_symbols']
            budget=nav['equity']*(cfg['leveraged_trade_loss_pct'] if leveraged else cfg['per_trade_loss_pct'])/100
            if candidate_loss is not None:
                max_qty=max(0,math.floor(budget/max(candidate_loss,.01)))
                if side=='PUT': max_qty=min(max_qty,max(0,math.floor((nav['cash']-nav.get('csp_collateral',0))/(strike*size+cfg['fee_per_contract']))))
                if side=='CALL':
                    owned=sum(float(r.get('shares') or 0) for r in nav.get('stock_rows',[]) if r['symbol']==symbol)
                    covered=sum(r['qty']*r.get('contract_size',100) for r in nav.get('option_rows',[]) if r['symbol']==symbol and r['side']=='CALL')
                    max_qty=min(max_qty,max(0,math.floor((owned-covered)/size)))
                # Conservative sizing uses sum of current worst loss and candidate
                # worst loss: it does not assume scenario diversification benefits.
                available=nav['equity']*cfg['max_stress_loss_pct']/100-(current['worst_loss'] or 0)
                max_qty=min(max_qty,max(0,math.floor(available/max(candidate_loss,.01)))) if current['ok'] else 0
                if qty>max_qty: violations.append('候选压力损失超过单笔或组合风险预算')
            else: violations.extend(single['issues'])
            if leveraged and not cfg['leveraged_allowed']: violations.append('杠杆标的未获风险预算准入')
            projected['cash']+=(bid*size-cfg['fee_per_contract'])*qty
            projected['equity']+=((bid-ask)*size-cfg['fee_per_contract'])*qty
            projected.setdefault('option_rows',[]).append({**unitnav['option_rows'][0],'qty':qty})
            projected.setdefault('spots_used',{}).update(unitnav['spots_used'])
    stress=stress_nav(projected,asof,cfg['fee_per_contract']) if opportunity else current
    if not stress['ok']:violations.extend(stress['issues'])
    elif stress['worst_loss']>nav['equity']*cfg['max_stress_loss_pct']/100:violations.append('交易后组合压力损失超预算')
    if drawdown and drawdown.get('drawdown_pct') is not None and drawdown['drawdown_pct']>=cfg['drawdown_stop_pct']:
        violations.append('账户回撤触发暂停新增风险')
    if nav.get('reconciliation_required'):violations.append('台账尚未核对')
    return {'enabled':cfg['enabled'],'ok':not violations if cfg['enabled'] else True,
            'violations':list(dict.fromkeys(violations)), 'suggested_max_qty':max_qty,
            'candidate_stress_loss_per_contract':candidate_loss,'stress':stress,
            'drawdown':drawdown, 'config':cfg}
