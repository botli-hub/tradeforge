import { useEffect, useState } from 'react'
import { recordWheelExecutionQuality } from '../services/api'

const empty = () => ({symbol:'', contract_code:'', side:'SELL', status:'filled', requested_qty:'1', filled_qty:'1', contract_size:'100', reference_price:'', limit_price:'', fill_price:'', submitted_at:'', filled_at:'', mark_after:'', mark_after_at:'', execution_id:'', research_event_id:''})
const numeric = new Set(['requested_qty','filled_qty','contract_size','reference_price','limit_price','fill_price','mark_after'])
const dates = new Set(['submitted_at','filled_at','mark_after_at'])
const labels: Record<string,string> = {symbol:'标的',contract_code:'合约代码',requested_qty:'委托张数',filled_qty:'累计成交张数',contract_size:'每张乘数',reference_price:'决策参考价',limit_price:'委托限价',fill_price:'累计成交均价',submitted_at:'委托时间（本地时区）',filled_at:'成交时间（本地时区）',mark_after:'成交后观察价',mark_after_at:'观察时间（本地时区）',execution_id:'台账执行 ID（可选）',research_event_id:'研究归档 ID（可选）'}
function localTime(value: string) {
 if(!value)return ''
 const d=new Date(value)
 return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19)
}
export default function WheelExecutionForm({editing,onSaved,onCancel}:{editing:any,onSaved:()=>Promise<void>,onCancel:()=>void}) {
 const [form,setForm]=useState<Record<string,string>>(empty),[busy,setBusy]=useState(false),[error,setError]=useState('')
 const [newId,setNewId]=useState(()=>crypto.randomUUID())
 useEffect(()=>{
  setError('')
  if(!editing){setForm(empty());setNewId(crypto.randomUUID());return}
  setForm(Object.fromEntries(Object.keys(empty()).map(k=>[k,dates.has(k)?localTime(editing[k]||''):String(editing[k]??'')])))
 },[editing])
 async function save(e:React.FormEvent) {
  e.preventDefault();setBusy(true);setError('')
  try {
   const body:Record<string,unknown>={id:editing?.id||newId}
   if(editing)body.expected_revision=editing.revision
   Object.entries(form).forEach(([k,v])=>{body[k]=v===''?null:numeric.has(k)?Number(v):dates.has(k)?new Date(v).toISOString():v.trim()})
   await recordWheelExecutionQuality(body);await onSaved();onCancel();setForm(empty());setNewId(crypto.randomUUID())
  }catch(e:any){setError(e.message)}finally{setBusy(false)}
 }
 return <form onSubmit={save}>
  <p>{editing?'更新该委托的累计成交与观察结果；旧版本保留在研究归档。':'记录一笔委托的执行质量。这里只保存观测，不会下单或重复登记台账。'}</p>
  <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:10}}>
   <label>买卖方向<br/><select value={form.side} disabled={!!editing} onChange={e=>setForm({...form,side:e.target.value})}><option value="SELL">卖出</option><option value="BUY">买入</option></select></label>
   <label>委托状态<br/><select value={form.status} onChange={e=>setForm({...form,status:e.target.value})}><option value="pending">等待成交</option><option value="partial">部分成交</option><option value="filled">全部成交</option><option value="cancelled">已撤销</option></select></label>
   {Object.entries(labels).map(([key,label])=><label key={key}>{label}<br/><input aria-label={label} style={{width:'100%',boxSizing:'border-box'}} type={dates.has(key)?'datetime-local':numeric.has(key)?'number':'text'} step={dates.has(key)?'1':'any'} min={numeric.has(key)?0:undefined} required={['symbol','contract_code','requested_qty','filled_qty','contract_size','submitted_at'].includes(key)} disabled={!!editing&&['symbol','contract_code'].includes(key)} value={form[key]} onChange={e=>setForm({...form,[key]:e.target.value})}/></label>)}
  </div>
  {error&&<p role="alert" style={{color:'var(--red)'}}>{error}</p>}
  <button type="submit" className="btn" disabled={busy} style={{marginTop:10}}>{busy?'保存中…':editing?'更新执行观测':'保存执行观测'}</button>{' '}
  {editing&&<button type="button" className="btn" disabled={busy} onClick={onCancel}>取消编辑</button>}
 </form>
}
