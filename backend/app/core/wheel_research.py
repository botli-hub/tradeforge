"""Paired, chronological experiments with frozen policies and saved provenance."""
from datetime import date
from app.core.wheel_backtest import run_on_bars, BTParams
from app.core.wheel_score import score_contract, DEFAULT_SCAN_CFG
from app.core.wheel_signal import SIGNAL_VERSION, ema_value


def compare_policies(bars, quotes, params=None, ema_period=50, score_threshold=15, test_start=None):
    if not quotes or not bars:
        raise ValueError('需要标的日线与历史期权报价；不自动合成缺失数据')
    if ema_period not in (50,200): raise ValueError('EMA 周期仅支持 50/200')
    p=dict(params or {})
    unknown=set(p)-set(BTParams.__dataclass_fields__)
    if unknown:raise ValueError('未知回测参数: '+', '.join(sorted(unknown)))
    BTParams(**p).validate()
    p['warmup_bars']=max(int(p.get('warmup_bars',60)),ema_period)
    ordered=[date.fromisoformat(str(b.get('date') or b.get('ts'))[:10]) for b in bars]
    if ordered!=sorted(set(ordered)):raise ValueError('标的日期必须严格递增')
    if len(bars)<=p['warmup_bars']:raise ValueError('数据不足完整预热')
    import math
    if not math.isfinite(float(score_threshold)) or score_threshold<0:raise ValueError('评分阈值无效')
    index={d:i for i,d in enumerate(ordered)}
    scores={};regimes={}
    for q in quotes:
        day=date.fromisoformat(str(q['date'])[:10]);expiry=date.fromisoformat(str(q['expiry'])[:10])
        for key in ('bid','ask','strike','delta'):
            if not math.isfinite(float(q[key])):raise ValueError(f'{key} 必须为有限数')
        if float(q['strike'])<=0 or float(q['bid'])<0 or float(q['ask'])<float(q['bid']) or abs(float(q['delta']))>1:
            raise ValueError('历史期权报价无效')
        dte=(expiry-day).days
        if dte<=0:continue
        scored=score_contract(float(q['bid'])/float(q['strike'])*365/dte*100,
                              str(q['side']).upper(),abs(float(q['delta'])),
                              (float(q['ask'])-float(q['bid']))/max((float(q['ask'])+float(q['bid']))/2,.0001)*100,
                              False, None,None,DEFAULT_SCAN_CFG)
        scores[(day,str(q['contract_code']))]=scored['score'] if scored else float('-inf')
    for i,d in enumerate(ordered):
        closes=[float(b['close']) for b in bars[:i+1]]
        regimes[d]=len(closes)>=200 and closes[-1]>=ema_value(closes,200)
    def policy(name):
        def accepts(day,q):
            if name=='fixed_schedule':return index.get(day,0)%5==0
            high=scores.get((day,q['contract_code']),float('-inf'))>=score_threshold
            return high and (name!='score_touch_regime' or regimes.get(day,False))
        return accepts
    def evaluate(rows):
        results={}
        for name in ('fixed_schedule','score_only','score_touch','score_touch_regime'):
            result=run_on_bars(rows,p,quotes=quotes,timing_only='touch' in name,ema_period=ema_period,
                               signal_mode='ema_touch_v1',entry_policy=policy(name))
            if result.get('ok'):
                result['worst_cashflow']=min((t['cashflow'] for t in result['trades']),default=0)
                result['exposure_days']=sum(x['stock_mv']>0 or x['option_liability']>0 for x in result['equity_curve'])
                result['evidence_status']='insufficient_sample' if result['opened_trade_count']<30 else 'requires_out_of_sample_validation'
            results[name]=result
        return results
    all_results=evaluate(bars)
    test=None
    if test_start:
        cutoff=date.fromisoformat(test_start)
        n=next((i for i,d in enumerate(ordered) if d>=cutoff),len(ordered))
        if n<p['warmup_bars'] or n>=len(bars)-1:raise ValueError('样本外起点需留出预热和至少两个测试交易日')
        test={'start':ordered[n].isoformat(),'results':evaluate(bars[n-p['warmup_bars']:]),
              'method':'独立资金、固定参数时间留出；不根据全样本结果优化参数'}
    return {'ok':all(r.get('ok') for r in all_results.values()) and (test is None or all(r.get('ok') for r in test['results'].values())),'results':all_results,'out_of_sample':test,
            'regime_ready_days':max(0,len(ordered)-199),
            'signal_version':SIGNAL_VERSION,'validated_edge':False,'score_threshold':score_threshold,
            'warnings':['历史候选池是否完整须由数据提供方保证；当前存续合约不能代表历史全池',
                        '无历史 OHLC 时使用中间价代理，无法复现 intraday high；bid 确认共用实时 EMA 定义',
                        '评分实验只使用当时 bid/ask、delta、DTE；未提供的趋势/事件/IV 因子不补造',
                        'fixed_schedule 每五个标的交易日允许开仓，所有组仅持有一条 Wheel 链']}
