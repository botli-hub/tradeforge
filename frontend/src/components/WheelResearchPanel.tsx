import WheelExecutionForm from './WheelExecutionForm'
import { useEffect, useState } from 'react'
import { getWheelResearch, saveWheelRiskSettings, compareWheelResearch, getWheelResearchEvent, captureWheelResearch, getWheelCaptureStatus } from '../services/api'

const num = (v: any, digits = 2) => typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US', {maximumFractionDigits: digits}) : '—'
const names: Record<string,string> = {fixed_schedule:'固定周期',score_only:'仅高分',score_touch:'高分＋触线',score_touch_regime:'高分＋触线＋趋势'}
const box = {padding: 14, marginBottom: 12, border:'1px solid var(--border)', borderRadius:8}
function download(data: any, name: string) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}))
  const a=document.createElement('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)
}

export default function WheelResearchPanel() {
 const [data,setData]=useState<any>(null), [settings,setSettings]=useState<any>(null)
 const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false)
 const [input,setInput]=useState<any>(null),[result,setResult]=useState<any>(null)
 const [period,setPeriod]=useState(50),[cutoff,setCutoff]=useState('')
 const [editing,setEditing]=useState<any>(null)
 const [capture,setCapture]=useState<any>(null)
 async function load() { const d=await getWheelResearch();setData(d);setSettings({...d.risk_budget.config,archive_interval_minutes:d.archive_interval_minutes}) }
 useEffect(()=>{load().catch(e=>setError(e.message));getWheelCaptureStatus().then(setCapture).catch(e=>setError(e.message))},[])
 useEffect(()=>{
  if(!capture?.running)return
  const timer=setInterval(()=>{getWheelCaptureStatus().then(s=>{setCapture(s);if(!s.running)load().catch(e=>setError(e.message))}).catch(e=>setError(e.message))},5000)
  return ()=>clearInterval(timer)
 },[capture?.running])
 async function act(fn:()=>Promise<void>) {setBusy(true);setError('');setMessage('');try{await fn()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 const risk=data?.risk_budget, stress=risk?.stress, pnl=data?.attribution
 return <section style={box}>
  <h3>策略验证与风险预算</h3>
  <p>行情事实、模型估计与经验规则分别展示。研究结果不自动授权下单。</p>
  <button className="btn" disabled={busy} onClick={()=>act(load)}>刷新账户与归档</button>
  {' '}<button className="btn" disabled={busy||capture?.running} onClick={()=>act(async()=>setCapture(await captureWheelResearch()))}>{capture?.running?'行情留档进行中…':'立即留档启用标的期权链'}</button>
  {capture?.error&&<p role="alert">{capture.error}</p>}
  {capture?.result&&<p>最近留档：{capture.result.ok?'完成':'部分失败，请查看归档'} · {capture.result.items?.length??0} 个到期日结果</p>}
  {error && <p role="alert" style={{color:'var(--red)'}}>{error}</p>}
  {message && <p role="status">{message}</p>}
  {settings && <details style={box}>
   <summary>风险闸门与定时留档 · {settings.enabled?'已启用':'仅观察'}</summary>
   <p>预算百分比以账户权益计算。仅观察模式显示风险，但不拦截交易；启用后拦截超预算的新风险计划，已发生的实际成交仍可登记。</p>
   <label><input type="checkbox" checked={settings.enabled} onChange={e=>setSettings({...settings,enabled:e.target.checked})}/>启用硬闸门</label>{' '}
   <label><input type="checkbox" checked={settings.leveraged_allowed} onChange={e=>setSettings({...settings,leveraged_allowed:e.target.checked})}/>允许杠杆标的（仍受单独预算限制）</label>
   <div style={{display:'flex',flexWrap:'wrap',gap:12,marginTop:12}}>
   {([['max_stress_loss_pct','组合压力损失上限 %'],['per_trade_loss_pct','单笔压力损失上限 %'],['leveraged_trade_loss_pct','杠杆标的单笔上限 %'],['drawdown_stop_pct','暂停开仓回撤 %'],['archive_interval_minutes','留档间隔分钟，0=关闭']] as const).map(([k,label])=><label key={k}>{label}<br/><input type="number" min={k==='archive_interval_minutes'?0:.1} max={k==='archive_interval_minutes'?1440:100} step="0.1" value={settings[k]} onChange={e=>setSettings({...settings,[k]:Number(e.target.value)})} style={{width:110}}/></label>)}
   </div>
   <p>留档覆盖启用标的当前可查询的全部到期日；可能占用 OpenD 行情额度。归档间隔至少 15 分钟，失败会留记录。</p>
   <button className="btn" disabled={busy} onClick={()=>act(async()=>{await saveWheelRiskSettings(settings);await load();setMessage('设置已保存')})}>保存预算与留档设置</button>
  </details>}
  {stress && <div style={box}>
   <b>组合联合压力</b><p>最差情景损失 ${num(stress.worst_loss)} · 已采样回撤 {num(risk.drawdown?.drawdown_pct)}% · 样本 {risk.drawdown?.samples ?? 0}</p>
   {risk.violations?.length>0 && <p style={{color:'var(--red)'}}>{risk.violations.join('；')}</p>}
   <p>Delta 美元敞口 ${num(stress.greeks.delta_dollars)} · 1% 波动 Gamma 项 ${num(stress.greeks.gamma_1pct_dollars)} · IV 增 1 点 Vega 项 ${num(stress.greeks.vega_1point_dollars)}</p>
   <table style={{width:'100%'}}><thead><tr><th>标的变化</th><th>IV 增加</th><th>退出价差</th><th>估算损失</th></tr></thead><tbody>{stress.scenarios.map((s:any,i:number)=><tr key={i}><td>{s.spot_shock_pct}%</td><td>{s.iv_add_points} 点</td><td>{s.exit_spread_pct}%</td><td>${num(s.loss)}</td></tr>)}</tbody></table>
   <p>按到期日集中名义金额：{Object.entries(stress.expiry_notional).map(([day,value])=>`${day} $${num(value)}`).join('；') || '无持仓'}</p>
   <small>{stress.limitations}。缺少有效行情时不输出完整估计。回撤仅覆盖开始采样后的有效估值。</small>
  </div>}
  {pnl && <div style={box}><b>账户收益归因</b>
   <p>已实现期权 ${num(pnl.option_realized_gross)} · 已实现股票 ${num(pnl.stock_realized_gross)} · 未实现期权 ${num(pnl.option_unrealized)} · 未实现股票 ${num(pnl.stock_unrealized)} · 费用 ${num(pnl.fees)}</p>
   <p>总盈亏 ${num(pnl.total_pnl)} · 核对差额 ${num(pnl.reconciliation_residual)} · {pnl.reconciled?'与账户权益核对一致':'估值或账务待核对'}</p><small>{pnl.cash_income_note}</small>
  </div>}
  <details style={box}><summary>同样本四组对照与时间留出验证</summary>
   <p>导入 JSON：bars 为严格递增的 date/close；quotes 含 date、contract_code、side、strike、expiry、delta、bid、ask，可附 high/close；params 可指定资金、费用和预热。没有期权 OHLC 时明确使用报价中间价代理。</p>
   <input aria-label="导入回测数据 JSON" type="file" accept=".json" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file)act(async()=>{setInput(JSON.parse(await file.text()));setResult(null);setMessage(`已载入 ${file.name}`)})}}/>
   <label>EMA <select value={period} onChange={e=>setPeriod(Number(e.target.value))}><option value={50}>50</option><option value={200}>200</option></select></label>{' '}
   <label>样本外起始日期 <input type="date" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label>{' '}
   <button className="btn" disabled={busy||!input} onClick={()=>act(async()=>setResult(await compareWheelResearch({...input,ema_period:period,test_start:cutoff||null})))}>运行并保存实验</button>
   {result && <><p>{result.ok?'运行完成':'数据未通过检查'} · 尚未验证策略优势</p>
    {[['全样本',result.results],['时间留出',result.out_of_sample?.results]].map(([title,rows]:any)=>rows&&<div key={title}><b>{title}</b><table style={{width:'100%'}}><thead><tr><th>组别</th><th>收益</th><th>回撤</th><th>开仓</th><th>持仓天数</th><th>状态</th></tr></thead><tbody>{Object.entries(rows).map(([k,r]:any)=><tr key={k}><td>{names[k]}</td><td>{num(r.total_return_pct)}%</td><td>{num(r.max_drawdown_pct)}%</td><td>{r.opened_trade_count??'—'}</td><td>{r.exposure_days??'—'}</td><td>{r.error || (r.evidence_status==='insufficient_sample'?'样本不足':'待进一步验证')}</td></tr>)}</tbody></table></div>)}
    <p>{result.warnings?.join('；')}</p><button className="btn" onClick={()=>download(result,'wheel-experiment.json')}>下载结果与逐笔交易</button></>}
  </details>
  <details style={box}><summary>成交质量 · 已记录 {data?.execution.records??0} 条</summary>
   <p>已完成委托未成交率 {num(data?.execution.unfilled_rate==null?null:data.execution.unfilled_rate*100)}% · 有参考价的成交 {data?.execution.measured_fills??0} 条 · 不利成交差额 ${num(data?.execution.shortfall_dollars)}</p>
   <WheelExecutionForm editing={editing} onSaved={load} onCancel={()=>setEditing(null)}/>
   <table style={{width:'100%'}}><thead><tr><th>合约</th><th>状态</th><th>成交率</th><th>不利价差</th><th>耗时秒</th><th>成交后盈亏</th><th>操作</th></tr></thead><tbody>{data?.execution.items.map((r:any)=><tr key={r.id}><td>{r.contract_code}</td><td>{r.status}</td><td>{num(r.fill_rate*100)}%</td><td>${num(r.shortfall_dollars)}</td><td>{num(r.fill_latency_seconds)}</td><td>${num(r.markout_dollars)}</td><td><button className="btn btn-sm" onClick={()=>setEditing(r)}>更新</button></td></tr>)}</tbody></table>
  </details>
  <details style={box}><summary>最近研究归档 · 原始输入与版本可下载</summary>
   {data?.events.map((e:any)=><div key={e.id} style={{margin:8}}>{e.observed_at} · {e.kind} · {e.symbol} · {e.candidate_count} 个候选 <button className="btn btn-sm" disabled={busy} onClick={()=>act(async()=>download(await getWheelResearchEvent(e.id),`${e.kind}-${e.id}.json`))}>查看完整证据</button></div>)}
  </details>
 </section>
}
