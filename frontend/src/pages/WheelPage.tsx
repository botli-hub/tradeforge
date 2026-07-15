import { useCallback, useEffect, useState } from 'react'
import {
  getAppSettings, subscribeSettings, type AppSettings,
  getWheelTargets, getWheelCandidates, addWheelTarget, updateWheelTarget, deleteWheelTarget,
  getWheelCycles, getWheelTrades, recordWheelTrade, updateWheelTrade, deleteWheelTrade,
  getWheelStats, getWheelSuggest, triggerWheelTimingScan, getWheelTimingSignals,
  getWheelScanStatus, getWheelTimingHistory, checkWheelOpenPositions, getWheelRollOptions,
  getWheelPoolScan, pushWheelPoolScan, WheelScanResult, getBackendConfig,
  getWheelOpportunities, type WheelOpportunitiesResult, type WheelOpportunity, type OppFilter,
  registerWheelRoll,
  WheelTarget, WheelCycle, WheelTrade, WheelStats, WheelTradeType,
  WheelSuggestResponse, LeapsCandidate, LeapsSignal, VolatilityProfile,
  WheelScanStatus, WheelTimingHistoryPage, WheelOpenPositionItem, WheelRollOptions,
} from '../services/api'
import WheelOptimizePanel from '../components/WheelOptimizePanel'

const STAGE_LABELS: Record<string, string> = {
  IDLE: '空仓', CSP_OPEN: '卖Put中', HOLDING: '持股', CC_OPEN: '卖Call中', CLOSED: '已结束',
}
// 轮子列表排序:在场合约(有风险要盯)优先,其次持股(该卖Call),空仓最后
const CYCLE_STATUS_ORDER: Record<string, number> = {
  CSP_OPEN: 0, CC_OPEN: 1, HOLDING: 2, IDLE: 3,
}
const STAGE_COLORS: Record<string, string> = {
  IDLE: 'var(--text-secondary)', CSP_OPEN: '#38bdf8', HOLDING: '#fbbf24', CC_OPEN: '#a78bfa', CLOSED: '#4ade80',
}
const TRADE_LABELS: Record<WheelTradeType, string> = {
  SELL_PUT: '卖出 Put', BUY_PUT_CLOSE: '买回 Put 平仓', SELL_CALL: '卖出 Call', BUY_CALL_CLOSE: '买回 Call 平仓',
  EXPIRE: '到期作废', ASSIGNED: '被行权接货', CALLED_AWAY: '被行权交货', SELL_SHARES: '卖出股票结束',
  BUY_SHARES: '已持正股入轮',
}
// 各状态允许的登记类型
const ALLOWED_TRADES: Record<string, WheelTradeType[]> = {
  NONE: ['SELL_PUT', 'BUY_SHARES'],
  IDLE: ['SELL_PUT', 'BUY_SHARES'],
  CSP_OPEN: ['EXPIRE', 'BUY_PUT_CLOSE', 'ASSIGNED'],
  HOLDING: ['SELL_CALL', 'SELL_SHARES'],
  CC_OPEN: ['EXPIRE', 'BUY_CALL_CLOSE', 'CALLED_AWAY'],
}

function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

// ── 颜色语义:绿=收益/安全 橙=注意 红=风险 蓝=信息 紫=中性标记 ──────────────────
const C = { green: '#4ade80', orange: '#fb923c', red: '#f87171', blue: '#38bdf8', purple: '#a78bfa' } as const
type SemColor = keyof typeof C

function Badge({ color, children, title }: { color: SemColor; children: any; title?: string }) {
  return (
    <span title={title} style={{
      padding: '0 7px', borderRadius: 8, fontSize: 10, fontWeight: 700,
      background: C[color] + '22', color: C[color], border: `1px solid ${C[color]}55`,
      whiteSpace: 'nowrap',
    }}>{children}</span>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: SemColor }) {
  return (
    <div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{label}</div>
      <div style={{ fontWeight: 600, fontSize: 12, color: color ? C[color] : 'var(--text)' }}>{value}</div>
    </div>
  )
}

function StatusDot({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-secondary)' }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%', display: 'inline-block',
        background: ok == null ? 'var(--border)' : ok ? C.green : C.red,
      }} />
      {label}
    </span>
  )
}

function fmtMoney(v: number) {
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e4) return (v / 1e3).toFixed(1) + 'k'
  return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

/** 单笔交易现金流(卖出为正) */
function tradeCashFlow(t: WheelTrade): number | null {
  if (t.trade_type === 'SELL_PUT' || t.trade_type === 'SELL_CALL')
    return t.qty * t.price * t.contract_size - t.fee
  if (t.trade_type === 'BUY_PUT_CLOSE' || t.trade_type === 'BUY_CALL_CLOSE')
    return -(t.qty * t.price * t.contract_size + t.fee)
  if (t.trade_type === 'SELL_SHARES') return t.qty * t.price - t.fee
  if (t.trade_type === 'BUY_SHARES') return -(t.qty * t.price + t.fee)
  return null
}

/** 标的资金占用 = Σ(CSP担保 + 持股成本) */
function targetCapital(cycles: WheelCycle[]): number {
  return cycles.reduce((sum, c) =>
    sum
    + (c.status === 'CSP_OPEN' ? (c.open_strike || 0) * (c.open_qty || 1) * (c.open_contract_size || 100) : 0)
    + (c.shares > 0 ? c.shares * c.share_cost : 0), 0)
}

function fmtDate(iso: string | null | undefined) {
  return iso ? iso.replace('T', ' ').slice(0, 16) : '--'
}

// ── 波动率行(期望 IV vs 实际 HV + IV Rank)────────────────────────────────────
function VolatilityBar({ v }: { v: VolatilityProfile }) {
  const spread = v.atm_iv != null && v.hv20 != null ? +(v.atm_iv - v.hv20).toFixed(1) : null
  return (
    <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12, alignItems: 'center' }}>
      <span>
        IV Rank{' '}
        <b style={{ color: (v.iv_rank ?? 0) >= 70 ? '#f87171' : (v.iv_rank ?? 0) >= 50 ? '#fb923c' : 'var(--text)' }}>
          {v.iv_rank != null ? v.iv_rank : '积累中'}
        </b>
        {v.iv_rank == null && <span style={{ color: 'var(--text-secondary)' }}>({v.iv_history_days}/60天)</span>}
      </span>
      <span>期望波动率(ATM IV) <b>{v.atm_iv != null ? v.atm_iv + '%' : '--'}</b></span>
      <span>实际波动率 HV20 <b>{v.hv20 != null ? v.hv20 + '%' : '--'}</b> / HV60 <b>{v.hv60 != null ? v.hv60 + '%' : '--'}</b></span>
      {spread != null && (
        <span>
          IV−HV{' '}
          <b style={{ color: spread > 0 ? '#4ade80' : '#f87171' }}>{spread > 0 ? '+' : ''}{spread}</b>
          <span style={{ color: 'var(--text-secondary)', marginLeft: 4 }}>
            {spread > 0 ? '权利金偏贵,利于卖方' : '权利金偏便宜'}
          </span>
        </span>
      )}
    </div>
  )
}

// ── 阶段指示器 ────────────────────────────────────────────────────────────────
function StageIndicator({ status }: { status: string }) {
  const stages = ['IDLE', 'CSP_OPEN', 'HOLDING', 'CC_OPEN']
  const idx = stages.indexOf(status)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      {stages.map((s, i) => (
        <span key={s} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{
            padding: '1px 7px', borderRadius: 9, fontSize: 10,
            background: i === idx ? STAGE_COLORS[s] + '33' : 'transparent',
            color: i === idx ? STAGE_COLORS[s] : 'var(--text-secondary)',
            border: `1px solid ${i === idx ? STAGE_COLORS[s] : 'var(--border)'}`,
            fontWeight: i === idx ? 700 : 400,
            whiteSpace: 'nowrap',
          }}>{STAGE_LABELS[s]}</span>
          {i < stages.length - 1 && <span style={{ color: 'var(--text-secondary)', fontSize: 9 }}>→</span>}
        </span>
      ))}
    </div>
  )
}

// ── 登记交易弹窗 ──────────────────────────────────────────────────────────────
interface TradeFormState {
  symbol: string
  trade_type: WheelTradeType
  contract_code: string
  strike: string
  expiry: string
  qty: string
  price: string
  fee: string
  contract_size: string
  note: string
  traded_at: string
}

/** 从今天起第 n 个周五(n=1 本周五或下周五) */
function nthFriday(n: number): string {
  const d = new Date()
  let days = (5 - d.getDay() + 7) % 7
  if (days === 0) days = 7 // 今天是周五则取下周五
  d.setDate(d.getDate() + days + (n - 1) * 7)
  return d.toISOString().slice(0, 10)
}

/** 距今约 targetDays 天的最近周五 */
function fridayNear(targetDays: number): string {
  const d = new Date()
  d.setDate(d.getDate() + targetDays)
  const dow = d.getDay()
  const toFri = dow <= 5 ? 5 - dow : 6 // 周六→+6到下周五
  d.setDate(d.getDate() + toFri)
  return d.toISOString().slice(0, 10)
}

function nowLocal(): string {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

/** 成交时间 datetime-local 字符串；保证不早于 minIso（开仓腿之后） */
function defaultTradedAt(minIso?: string | null): string {
  const now = nowLocal()
  if (!minIso) return now
  // minIso 可能是完整 ISO；截到分钟后 +1 分钟，避免与开仓同秒导致顺序歧义
  const min = minIso.replace('T', ' ').slice(0, 16).replace(' ', 'T')
  if (now >= min) return now
  try {
    const d = new Date(min.length === 16 ? min + ':00' : min)
    if (Number.isNaN(d.getTime())) return now
    d.setMinutes(d.getMinutes() + 1)
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
    return d.toISOString().slice(0, 16)
  } catch {
    return now
  }
}

function TradeModal({
  initial, cycleStatus, cycleId, newCycle, minTradedAt, onClose, onSaved,
}: {
  initial: Partial<TradeFormState> & { symbol: string }
  cycleStatus: string
  cycleId?: string
  newCycle?: boolean
  /** 本轮已有腿的最晚成交时间；新腿默认不得早于此 */
  minTradedAt?: string | null
  onClose: () => void
  onSaved: () => void
}) {
  const allowed = ALLOWED_TRADES[cycleStatus] || ['SELL_PUT']
  const [form, setForm] = useState<TradeFormState>({
    symbol: initial.symbol,
    trade_type: (initial.trade_type as WheelTradeType) || allowed[0],
    contract_code: initial.contract_code || '',
    strike: initial.strike || '',
    expiry: (initial.expiry || '').slice(0, 10),
    qty: initial.qty || '1',
    price: initial.price || '',
    fee: initial.fee || '0',
    contract_size: initial.contract_size || '100',
    note: initial.note || '',
    traded_at: defaultTradedAt(minTradedAt),
  })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const needContract = ['SELL_PUT', 'SELL_CALL'].includes(form.trade_type)
  const needPrice = !['EXPIRE', 'ASSIGNED', 'CALLED_AWAY'].includes(form.trade_type)
  const isShares = ['BUY_SHARES', 'SELL_SHARES'].includes(form.trade_type)

  // 实时预览:本笔现金流
  const qtyN = parseFloat(form.qty) || 0
  const priceN = parseFloat(form.price) || 0
  const feeN = parseFloat(form.fee) || 0
  const sizeN = parseInt(form.contract_size) || 100
  const isSell = ['SELL_PUT', 'SELL_CALL'].includes(form.trade_type)
  const isBuy = ['BUY_PUT_CLOSE', 'BUY_CALL_CLOSE'].includes(form.trade_type)
  const cashFlow = isSell ? qtyN * priceN * sizeN - feeN
    : isBuy ? -(qtyN * priceN * sizeN + feeN)
    : form.trade_type === 'SELL_SHARES' ? qtyN * priceN - feeN
    : form.trade_type === 'BUY_SHARES' ? -(qtyN * priceN + feeN)
    : null

  const expiryChips: [string, string][] = [
    ['本周五', nthFriday(1)], ['下周五', nthFriday(2)],
    ['~30天', fridayNear(30)], ['~45天', fridayNear(45)],
  ]

  async function submit() {
    setErr(null)
    setSaving(true)
    try {
      await recordWheelTrade({
        symbol: form.symbol,
        trade_type: form.trade_type,
        contract_code: form.contract_code || undefined,
        strike: form.strike ? parseFloat(form.strike) : undefined,
        expiry: form.expiry || undefined,
        qty: form.qty ? parseFloat(form.qty) : 1,
        price: form.price ? parseFloat(form.price) : 0,
        fee: form.fee ? parseFloat(form.fee) : 0,
        contract_size: form.contract_size ? parseInt(form.contract_size) : 100,
        note: form.note || undefined,
        traded_at: form.traded_at || undefined,
        cycle_id: cycleId,
        new_cycle: newCycle,
      })
      onSaved()
      onClose()
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  const inputStyle = {
    width: '100%', padding: '6px 8px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 13,
  } as const

  return (
    <div style={{
      position: 'fixed', inset: 0, background: '#0009', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div className="modal-card" style={{ width: 440, maxWidth: '100%', maxHeight: '85vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 16px', fontSize: 16 }}>登记交易 — {form.symbol}</h3>
        {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
        {cycleStatus !== 'NONE' && cycleStatus !== 'IDLE' && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.45 }}>
            当前轮子：{STAGE_LABELS[cycleStatus] || cycleStatus}
            {cycleId ? ` · 绑定 cycle` : ''}
            {minTradedAt ? ` · 开仓腿时间 ${fmtDate(minTradedAt)}，成交时间须不早于此（状态机按时间重放）` : ''}
          </div>
        )}
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            类型
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
              {allowed.map(t => (
                <button key={t} type="button" onClick={() => setForm(f => ({ ...f, trade_type: t }))}
                  style={{
                    padding: '5px 12px', borderRadius: 14, fontSize: 12, cursor: 'pointer',
                    border: `1px solid ${form.trade_type === t ? 'var(--accent)' : 'var(--border)'}`,
                    background: form.trade_type === t ? 'var(--accent)' : 'transparent',
                    color: form.trade_type === t ? '#fff' : 'var(--text)',
                    fontWeight: form.trade_type === t ? 700 : 400,
                  }}>
                  {TRADE_LABELS[t]}
                </button>
              ))}
            </div>
          </div>
          {needContract && (
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              合约代码(选填,留空将按 Strike+到期日自动补全)
              <input value={form.contract_code} style={inputStyle} placeholder="留空自动生成,如 US.AAPL260821P00200000"
                onChange={e => setForm(f => ({ ...f, contract_code: e.target.value }))} />
            </label>
          )}
          {(needContract || ['ASSIGNED', 'CALLED_AWAY'].includes(form.trade_type)) && (
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              行权价 Strike {['ASSIGNED', 'CALLED_AWAY'].includes(form.trade_type) ? '(留空则用在场合约的)' : ''}
              <input type="number" value={form.strike} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, strike: e.target.value }))} />
            </label>
          )}
          {needContract && (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              到期日
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
                <input type="date" value={form.expiry} style={{ ...inputStyle, width: 150, cursor: 'pointer' }}
                  onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                  onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
                {expiryChips.map(([label, val]) => (
                  <button key={label} type="button" onClick={() => setForm(f => ({ ...f, expiry: val }))}
                    title={val}
                    style={{
                      padding: '3px 8px', borderRadius: 10, fontSize: 11, cursor: 'pointer',
                      border: `1px solid ${form.expiry === val ? 'var(--accent)' : 'var(--border)'}`,
                      color: form.expiry === val ? 'var(--accent)' : 'var(--text-secondary)',
                      background: 'transparent',
                    }}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {(needContract || form.trade_type === 'BUY_SHARES') && (
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {form.trade_type === 'BUY_SHARES' ? '股数' : '张数'}
                <input type="number" value={form.qty} style={inputStyle}
                  placeholder={form.trade_type === 'BUY_SHARES' ? '如 100' : undefined}
                  onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
              </label>
            )}
            {needPrice && (
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {form.trade_type === 'SELL_SHARES' ? '每股卖价' : form.trade_type === 'BUY_SHARES' ? '每股成本' : '权利金/张'}
                <input type="number" value={form.price} style={inputStyle}
                  onChange={e => setForm(f => ({ ...f, price: e.target.value }))} />
              </label>
            )}
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              手续费
              <input type="number" value={form.fee} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, fee: e.target.value }))} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              成交时间
              <input type="datetime-local" value={form.traded_at} style={{ ...inputStyle, cursor: 'pointer' }}
                onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                onChange={e => setForm(f => ({ ...f, traded_at: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              备注
              <input value={form.note} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
            </label>
          </div>
          {cashFlow != null && priceN > 0 && (
            <div style={{
              padding: '8px 12px', borderRadius: 6, fontSize: 13,
              background: cashFlow >= 0 ? '#4ade8011' : '#f8717111',
              border: `1px solid ${cashFlow >= 0 ? '#4ade8044' : '#f8717144'}`,
            }}>
              本笔现金流:<b style={{ color: cashFlow >= 0 ? '#4ade80' : '#f87171' }}>
                {cashFlow >= 0 ? '+' : ''}{cashFlow.toLocaleString('en-US', { maximumFractionDigits: 2 })}
              </b>
              <span style={{ color: 'var(--text-secondary)', marginLeft: 8, fontSize: 11 }}>
                {isShares
                  ? `${qtyN} 股 × ${priceN}${form.trade_type === 'SELL_SHARES' ? ` − 手续费 ${feeN}` : ` + 手续费 ${feeN}`}`
                  : `${qtyN} 张 × ${priceN} × ${sizeN}${isSell ? ` − 手续费 ${feeN}` : ` + 手续费 ${feeN}`}`}
              </span>
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            <button className="btn" onClick={onClose}>取消</button>
            <button className="btn btn-primary" disabled={saving} onClick={submit}>
              {saving ? '保存中...' : '登记'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────────
export default function WheelPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [tab, setTab] = useState<'board' | 'scan' | 'ledger' | 'targets' | 'optimize'>('board')
  /** 机会页内视图:今日可做 | 全部机会 | 触线档案 */
  const [oppView, setOppView] = useState<'actionable' | 'all' | 'archive'>('actionable')
  const [targets, setTargets] = useState<WheelTarget[]>([])
  const [stats, setStats] = useState<WheelStats | null>(null)
  const [cycles, setCycles] = useState<WheelCycle[]>([])
  const [trades, setTrades] = useState<WheelTrade[]>([])
  const [candidates, setCandidates] = useState<LeapsCandidate[]>([])
  const [error, setError] = useState<string | null>(null)

  // 助手
  const [suggest, setSuggest] = useState<WheelSuggestResponse | null>(null)
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [suggestSide, setSuggestSide] = useState<'put' | 'call'>('put')
  const [suggestSymbol, setSuggestSymbol] = useState<string>('')

  // 登记弹窗
  const [tradeModal, setTradeModal] = useState<{
    initial: Partial<TradeFormState> & { symbol: string }
    status: string
    cycleId?: string
    newCycle?: boolean
  } | null>(null)
  // 台账编辑弹窗
  const [editTrade, setEditTrade] = useState<WheelTrade | null>(null)
  // 建议面板对应的 cycle
  const [suggestCycleId, setSuggestCycleId] = useState<string | undefined>(undefined)
  // 开仓时机信号
  const [timingSignals, setTimingSignals] = useState<LeapsSignal[]>([])
  const [timingScanning, setTimingScanning] = useState(false)
  const [scanStatus, setScanStatus] = useState<WheelScanStatus | null>(null)
  // 时机历史(分页)
  const [timingHistory, setTimingHistory] = useState<WheelTimingHistoryPage | null>(null)
  const [historyPage, setHistoryPage] = useState(1)
  // 在场合约体检(cycle_id → item)
  const [openChecks, setOpenChecks] = useState<Record<string, WheelOpenPositionItem>>({})
  const [profitTarget, setProfitTarget] = useState(50)
  // Roll 弹窗
  const [rollData, setRollData] = useState<WheelRollOptions | null>(null)
  const [rollLoading, setRollLoading] = useState(false)
  // 看板主从布局:当前选中标的
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  // 看板选中轮子(右侧交易明细)
  const [selectedCycleId, setSelectedCycleId] = useState<string | null>(null)
  // 数据新鲜度与连接状态
  const [checksAt, setChecksAt] = useState<Date | null>(null)
  const [opendOk, setOpendOk] = useState<boolean | null>(null)
  const [tgOk, setTgOk] = useState<boolean | null>(null)
  const [nowTick, setNowTick] = useState(Date.now())
  // 今日行动过滤
  const [actionFilter, setActionFilter] = useState<'close' | 'roll' | 'uncovered' | 'idle' | null>(null)
  // 删除撤销
  const [undoTrade, setUndoTrade] = useState<WheelTrade | null>(null)
  // 标的快速筛选
  const [symbolQuery, setSymbolQuery] = useState('')
  // 看板行内编辑标的参数
  const [editParams, setEditParams] = useState<{
    floor_price: string; max_capital: string
    delta_min: string; delta_max: string
    dte_min: string; dte_max: string; min_annualized: string
  } | null>(null)
  const [savingParams, setSavingParams] = useState(false)
  // 全池扫描(底层)
  const [poolScan, setPoolScan] = useState<WheelScanResult | null>(null)
  const [poolScanLoading, setPoolScanLoading] = useState(false)
  const [poolPushing, setPoolPushing] = useState(false)
  // 统一机会流
  const [opps, setOpps] = useState<WheelOpportunitiesResult | null>(null)
  const [oppsLoading, setOppsLoading] = useState(false)
  const [oppFilter, setOppFilter] = useState<OppFilter>('actionable')
  const [oppSide, setOppSide] = useState<'ALL' | 'PUT' | 'CALL'>('ALL')
  const [ignoredOppIds, setIgnoredOppIds] = useState<Record<string, number>>(() => {
    try {
      const raw = localStorage.getItem('tradeforge.wheel.ignored_opps')
      return raw ? JSON.parse(raw) : {}
    } catch { return {} }
  })

  // 添加标的表单
  const [addSymbol, setAddSymbol] = useState('')
  const [addFloor, setAddFloor] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => subscribeSettings(next => setSettings(next)), [])

  const loadAll = useCallback(async () => {
    setError(null)
    try {
      const [t, s, c, tr, cand, tim] = await Promise.all([
        getWheelTargets().catch(() => []),
        getWheelStats().catch(() => null),
        getWheelCycles().catch(() => []),
        getWheelTrades().catch(() => []),
        getWheelCandidates().catch(() => []),
        getWheelTimingSignals(10).catch(() => []),
      ])
      setTargets(t)
      setStats(s)
      setCycles(c)
      setTrades(tr)
      setCandidates(cand)
      setTimingSignals(tim)
      // 在场合约体检(需 OpenD,失败静默)
      refreshChecks()
      // Telegram 配置状态
      getBackendConfig().then(cfg => setTgOk(!!cfg.telegram?.bot_token)).catch(() => setTgOk(null))
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  const refreshChecks = useCallback(() => {
    const st = getAppSettings()
    return checkWheelOpenPositions(st.marketHost, st.marketPort).then(r => {
      const map: Record<string, WheelOpenPositionItem> = {}
      r.items.forEach(i => { map[i.cycle_id] = i })
      setOpenChecks(map)
      setProfitTarget(r.profit_target_pct)
      setChecksAt(new Date())
      setOpendOk(true)
    }).catch(() => { setOpenChecks({}); setOpendOk(false) })
  }, [])

  // 每 5 分钟自动刷新体检数据;每 30 秒重算新鲜度显示
  useEffect(() => {
    const t1 = setInterval(() => { refreshChecks() }, 5 * 60 * 1000)
    const t2 = setInterval(() => setNowTick(Date.now()), 30 * 1000)
    return () => { clearInterval(t1); clearInterval(t2) }
  }, [refreshChecks])

  async function handlePoolScan(refresh = false) {
    setPoolScanLoading(true)
    setError(null)
    try {
      const st = getAppSettings()
      setPoolScan(await getWheelPoolScan(st.marketHost, st.marketPort, refresh))
    } catch (e: any) {
      setError('全池扫描失败:' + e.message)
    } finally {
      setPoolScanLoading(false)
    }
  }

  async function loadOpportunities(refresh = false) {
    setOppsLoading(true)
    setError(null)
    try {
      const st = getAppSettings()
      const r = await getWheelOpportunities(st.marketHost, st.marketPort, {
        refresh,
        run_pool: true,
        filter: oppFilter,
        side: oppSide === 'ALL' ? undefined : oppSide,
        hide_blocked: oppFilter !== 'blocked' && oppFilter !== 'all',
      })
      setOpps(r)
    } catch (e: any) {
      setError('机会流加载失败:' + e.message)
    } finally {
      setOppsLoading(false)
    }
  }

  function ignoreOpp(id: string, days = 3) {
    const until = Date.now() + days * 86400000
    const next = { ...ignoredOppIds, [id]: until }
    setIgnoredOppIds(next)
    localStorage.setItem('tradeforge.wheel.ignored_opps', JSON.stringify(next))
  }

  function isOppIgnored(id: string) {
    const until = ignoredOppIds[id]
    return !!until && until > Date.now()
  }

  function goBoardSymbol(symbol: string) {
    setSelectedSymbol(symbol)
    setTab('board')
  }

  function registerFromOpp(o: WheelOpportunity) {
    setTradeModal({
      initial: {
        symbol: o.symbol,
        trade_type: o.side === 'CALL' ? 'SELL_CALL' : 'SELL_PUT',
        contract_code: o.contract_code || '',
        strike: o.strike != null ? String(o.strike) : '',
        expiry: (o.expiry || '').slice(0, 10),
        price: o.premium_used != null ? String(o.premium_used) : (o.bid != null ? String(o.bid) : ''),
        contract_size: '100',
      },
      status: o.side === 'CALL' ? 'HOLDING' : 'IDLE',
      cycleId: o.cycle_id || undefined,
    })
  }

  async function handlePoolPush() {
    setPoolPushing(true)
    setError(null)
    try {
      const st = getAppSettings()
      const r = await pushWheelPoolScan(st.marketHost, st.marketPort)
      setPoolScan(r)
      if (!r.telegram_sent) setError('扫描完成,但 Telegram 未配置或发送失败(前往「设置」页检查)')
    } catch (e: any) {
      setError('扫描推送失败:' + e.message)
    } finally {
      setPoolPushing(false)
    }
  }

  async function handleRoll(cycleId: string) {
    setRollLoading(true)
    setError(null)
    try {
      const st = getAppSettings()
      setRollData(await getWheelRollOptions(cycleId, st.marketHost, st.marketPort))
    } catch (e: any) {
      setError('Roll 对比获取失败:' + e.message)
    } finally {
      setRollLoading(false)
    }
  }

  useEffect(() => { loadAll() }, [loadAll])

  useEffect(() => {
    if (tab !== 'scan') return
    if (oppView === 'archive') {
      getWheelTimingHistory(historyPage, 20).then(setTimingHistory).catch(() => setTimingHistory(null))
      return
    }
    // 今日可做 / 全部 → 合流列表
    setOppFilter(oppView === 'actionable' ? 'actionable' : 'all')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, oppView, historyPage])

  useEffect(() => {
    if (tab !== 'scan' || oppView === 'archive') return
    loadOpportunities(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, oppView, oppFilter, oppSide])

  async function handleSuggest(symbol: string, side: 'put' | 'call', cycleId?: string) {
    setSelectedSymbol(symbol)
    setSuggestSymbol(symbol)
    setSuggestSide(side)
    setSuggestLoading(true)
    setSuggest(null)
    setSuggestCycleId(cycleId)
    setError(null)
    try {
      const r = await getWheelSuggest(symbol, side, settings.marketHost, settings.marketPort, cycleId)
      setSuggest(r)
    } catch (e: any) {
      setSuggest(null)
      setSuggestSymbol('')
      setError(`获取${side === 'put' ? 'Put' : 'Call'}建议失败:` + e.message)
    } finally {
      setSuggestLoading(false)
    }
  }

  function closeSuggestModal() {
    setSuggest(null)
    setSuggestLoading(false)
    setSuggestSymbol('')
  }

  async function handleTimingScan() {
    setTimingScanning(true)
    setScanStatus(null)
    setError(null)
    try {
      await triggerWheelTimingScan()
      // 轮询扫描状态,最长 5 分钟(首次扫描需为每张合约拉历史K线,较慢)
      const deadline = Date.now() + 5 * 60 * 1000
      const poll = async () => {
        const st = await getWheelScanStatus().catch(() => null)
        if (st) setScanStatus(st)
        if (st && !st.running && st.finished_at) {
          setTimingSignals(await getWheelTimingSignals(10).catch(() => []))
          setTimingScanning(false)
          return
        }
        if (Date.now() > deadline) { setTimingScanning(false); return }
        setTimeout(poll, 3000)
      }
      setTimeout(poll, 2000)
    } catch (e: any) {
      setError('时机扫描失败:' + e.message)
      setTimingScanning(false)
    }
  }

  async function handleDeleteTrade(t: WheelTrade) {
    try {
      await deleteWheelTrade(t.id)
      setUndoTrade(t)
      setTimeout(() => setUndoTrade(u => (u && u.id === t.id ? null : u)), 12000)
      await loadAll()
    } catch (e: any) {
      setError('删除失败:' + e.message)
    }
  }

  async function handleUndoDelete() {
    if (!undoTrade) return
    const t = undoTrade
    setUndoTrade(null)
    try {
      await recordWheelTrade({
        symbol: t.symbol, trade_type: t.trade_type,
        contract_code: t.contract_code || undefined,
        strike: t.strike ?? undefined, expiry: t.expiry || undefined,
        qty: t.qty, price: t.price, fee: t.fee, contract_size: t.contract_size,
        note: t.note || undefined, traded_at: t.traded_at, cycle_id: t.cycle_id,
      })
      await loadAll()
    } catch (e: any) {
      setError('撤销失败(周期状态可能已变化):' + e.message)
    }
  }

  async function handleAddTarget() {
    const symbol = addSymbol.trim().toUpperCase()
    const floor = parseFloat(addFloor)
    if (!symbol) { setError('请选择或输入标的代码'); return }
    if (isNaN(floor) || floor <= 0) { setError('请填写有效的接货底线价'); return }
    setAdding(true)
    setError(null)
    try {
      await addWheelTarget({ symbol, floor_price: floor })
      setAddSymbol('')
      setAddFloor('')
      await loadAll()
    } catch (e: any) {
      setError('添加失败:' + e.message)
    } finally {
      setAdding(false)
    }
  }

  async function handleDeleteTarget(symbol: string) {
    if (!confirm(`确定移除 wheel 标的 ${symbol}?(历史周期与台账保留)`)) return
    try {
      await deleteWheelTarget(symbol)
      await loadAll()
    } catch (e: any) {
      setError('删除失败:' + e.message)
    }
  }

  async function handleToggleTarget(t: WheelTarget) {
    try {
      await updateWheelTarget(t.symbol, { enabled: !t.enabled })
      await loadAll()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const profitHits = Object.values(openChecks).filter(i => i.profit_hit)
  const statCards = [
    { label: '活跃轮子', value: stats?.active_cycles ?? '--', sub: `已完成 ${stats?.closed_cycles ?? 0} 轮` },
    { label: '本月净权利金', value: `$${fmt(stats?.premium_month)}`, sub: '卖出−买回,含费' },
    { label: '累计净权利金', value: `$${fmt(stats?.premium_total)}`, sub: '全部历史' },
    { label: '已实现盈亏', value: `$${fmt(stats?.realized_pnl_total)}`, sub: '已结束周期合计' },
    {
      label: '资金占用', value: `$${fmt(stats?.capital?.total_committed, 0)}`,
      sub: `担保 $${fmt(stats?.capital?.csp_collateral, 0)} + 持股 $${fmt(stats?.capital?.holding_cost, 0)}`,
    },
    {
      label: '压力测试', value: `$${fmt(stats?.capital?.assignment_stress, 0)}`,
      sub: '若在场Put全部被行权的总占用',
    },
    { label: '⚠ 待处理', value: (stats?.expiring_soon.length ?? 0) + profitHits.length, sub: `临期 ${stats?.expiring_soon.length ?? 0} · 利润达标 ${profitHits.length}` },
  ]

  const inputStyle = {
    padding: '5px 8px', background: 'var(--bg-secondary)', border: '1px solid var(--border)',
    borderRadius: 4, color: 'var(--text)', fontSize: 13,
  } as const

  return (
    <div style={{ padding: '20px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>Wheel 车轮策略</h2>
        <button className="btn" style={{ fontSize: 13, padding: '5px 12px' }} onClick={loadAll}>刷新</button>
        <span style={{ display: 'flex', gap: 12, alignItems: 'center', marginLeft: 'auto' }}>
          <StatusDot ok={opendOk} label="富途行情" />
          <StatusDot ok={tgOk} label="Telegram" />
          {(() => {
            void nowTick
            if (!checksAt) return <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>实时数据未加载</span>
            const mins = Math.floor((Date.now() - checksAt.getTime()) / 60000)
            const stale = mins >= 10
            return (
              <span style={{ fontSize: 11, color: stale ? C.orange : 'var(--text-secondary)' }}>
                数据截至 {checksAt.toTimeString().slice(0, 5)}{mins > 0 ? `(${mins}分钟前)` : ''}
                {stale && ' ⚠'}
              </span>
            )
          })()}
          <button className="btn" style={{ fontSize: 11, padding: '2px 10px' }} onClick={() => refreshChecks()}>↻ 刷新行情</button>
        </span>
      </div>

      {opendOk === false && (
        <div style={{ marginBottom: 12, border: `1px solid ${C.orange}55`, background: C.orange + '11', padding: '8px 14px', borderRadius: 6, fontSize: 12 }}>
          ⚠ 富途 OpenD 未连接:现价/浮盈/Δ/θ 等实时数据不可用,当前显示的是登记数据。启动 OpenD 后点「↻ 刷新行情」。
        </div>
      )}
      {undoTrade && (
        <div style={{ marginBottom: 12, border: '1px solid var(--border)', background: 'var(--bg-secondary)', padding: '8px 14px', borderRadius: 6, fontSize: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          已删除「{TRADE_LABELS[undoTrade.trade_type]}」({undoTrade.symbol})
          <button className="btn" style={{ fontSize: 11, padding: '2px 10px', color: 'var(--accent)' }} onClick={handleUndoDelete}>撤销</button>
        </div>
      )}
      {error && (
        <div className="alert alert-error" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ flex: 1 }}>{error}</span>
          <button className="btn" style={{ fontSize: 11, padding: '2px 10px', flexShrink: 0 }} onClick={loadAll}>重试</button>
        </div>
      )}

      {/* 统计卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 20 }}>
        {statCards.map(({ label, value, sub }) => (
          <div key={label} className="card" style={{ padding: '14px 18px' }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>{label}</div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{sub}</div>
          </div>
        ))}
      </div>

      {/* 利润达标提醒 */}
      {profitHits.length > 0 && (
        <div style={{ marginBottom: 16, border: '1px solid #4ade8055', background: '#4ade8011', padding: '10px 16px', borderRadius: 6, fontSize: 13 }}>
          💰 利润达标(≥{profitTarget}%):{profitHits.map(i =>
            `${i.symbol} ${i.side} $${i.strike}(浮盈 ${i.profit_pct}%)`).join('、')}
          —— 可平仓锁定再开新轮,提高资金周转
        </div>
      )}

      {/* 临期提醒 */}
      {(stats?.expiring_soon.length ?? 0) > 0 && (
        <div className="alert" style={{ marginBottom: 16, border: '1px solid #fb923c55', background: '#fb923c11', padding: '10px 16px', borderRadius: 6, fontSize: 13 }}>
          ⚠ 临期:{stats!.expiring_soon.map(e => `${e.symbol} ${e.open_option_type} $${e.open_strike}(${e.dte}天)`).join('、')}
          —— 尽快决定放任到期 / 买回 / Roll
        </div>
      )}

      {/* Tab 导航 */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid var(--border)' }}>
        {([
          ['board', '标的看板'],
          ['scan', `机会${opps?.summary?.actionable ? `(${opps.summary.actionable})` : ''}`],
          ['optimize', '组合优化'],
          ['ledger', '台账'],
          ['targets', `标的设置(${targets.length})`],
        ] as const).map(([k, label]) => (
          <div key={k} onClick={() => setTab(k as typeof tab)} style={{
            padding: '8px 16px', cursor: 'pointer', fontSize: 13,
            borderBottom: tab === k ? '2px solid var(--accent)' : '2px solid transparent',
            color: tab === k ? 'var(--accent)' : 'var(--text-secondary)',
          }}>{label}</div>
        ))}
      </div>

      {/* ── 看板：今日行动 + 标的主从 ── */}
      {tab === 'board' && (
        <div>
          {/* 今日行动 */}
          {(() => {
            const checks = Object.values(openChecks)
            const closeSyms = new Set(checks.filter(i => i.profit_hit || i.action_hint === '平仓换仓(剩余年化低)').map(i => i.symbol))
            const rollSyms = new Set(checks.filter(i => (i.action_hint || '').includes('Roll')).map(i => i.symbol))
            const uncovSyms = new Set(targets.filter(t =>
              (t.active_cycles || []).some(c => c.status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3)).map(t => t.symbol))
            const idleSyms = new Set(targets.filter(t => t.enabled && (t.idle_days ?? 0) >= 5).map(t => t.symbol))
            const items: [typeof actionFilter, string, number, SemColor][] = [
              ['close', '💰 该平仓', closeSyms.size, 'green'],
              ['roll', '🔄 该Roll', rollSyms.size, 'orange'],
              ['uncovered', '🪑 裸奔', uncovSyms.size, 'orange'],
              ['idle', '⏸ 空转', idleSyms.size, 'blue'],
            ]
            const total = items.reduce((a, [, , n]) => a + n, 0)
            if (total === 0) return null
            return (
              <div className="card" style={{ padding: '10px 16px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>⚡ 今日行动</span>
                {items.filter(([, , n]) => n > 0).map(([key, label, n, color]) => (
                  <button key={key} onClick={() => setActionFilter(f => f === key ? null : key)}
                    style={{
                      padding: '3px 12px', borderRadius: 12, fontSize: 12, cursor: 'pointer', fontWeight: 600,
                      background: actionFilter === key ? C[color] : C[color] + '18',
                      color: actionFilter === key ? '#111' : C[color],
                      border: `1px solid ${C[color]}66`,
                    }}>
                    {label} {n}
                  </button>
                ))}
                {actionFilter && (
                  <button className="btn" style={{ fontSize: 11, padding: '2px 10px' }} onClick={() => setActionFilter(null)}>清除过滤</button>
                )}
                <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>点击类别过滤左侧标的</span>
                <button className="btn" style={{ fontSize: 11, padding: '2px 10px', marginLeft: 'auto' }}
                  onClick={() => { setOppView('actionable'); setTab('scan') }}>
                  去机会 →
                </button>
              </div>
            )
          })()}

          {targets.filter(t => t.enabled).length === 0 && (
            <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '20px 0' }}>
              还没有启用的 wheel 标的,去「标的设置」添加(候选来自股票池美股/港股)
            </div>
          )}
          {(() => {
            const premiumBySymbol: Record<string, number> = {}
            cycles.forEach(c => {
              premiumBySymbol[c.symbol] = (premiumBySymbol[c.symbol] || 0) + (c.total_premium || 0)
            })
            let enabled = [...targets.filter(t => t.enabled)].sort(
              (a, b) => targetCapital(b.active_cycles || []) - targetCapital(a.active_cycles || []))
            if (symbolQuery.trim()) {
              const q = symbolQuery.trim().toUpperCase()
              enabled = enabled.filter(t => t.symbol.includes(q))
            }
            if (actionFilter) {
              const checks = Object.values(openChecks)
              const match = (t: WheelTarget): boolean => {
                if (actionFilter === 'close') return checks.some(i => i.symbol === t.symbol && (i.profit_hit || i.action_hint === '平仓换仓(剩余年化低)'))
                if (actionFilter === 'roll') return checks.some(i => i.symbol === t.symbol && (i.action_hint || '').includes('Roll'))
                if (actionFilter === 'uncovered') return (t.active_cycles || []).some(c => c.status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3)
                if (actionFilter === 'idle') return (t.idle_days ?? 0) >= 5
                return true
              }
              enabled = enabled.filter(match)
            }
            if (enabled.length === 0) return (
              <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '16px 0' }}>
                没有匹配的标的
                {(actionFilter || symbolQuery) && (
                  <button className="btn" style={{ fontSize: 11, padding: '2px 10px', marginLeft: 8 }}
                    onClick={() => { setActionFilter(null); setSymbolQuery('') }}>清除筛选</button>
                )}
              </div>
            )
            const sel = enabled.find(t => t.symbol === selectedSymbol) || enabled[0]
            const selCycles = [...(sel.active_cycles || [])].sort(
              (a, b) => (CYCLE_STATUS_ORDER[a.status] ?? 9) - (CYCLE_STATUS_ORDER[b.status] ?? 9))
            return (
              <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                {/* ── 左:标的列表(窄屏自动占满整行堆叠) ── */}
                <div className="card" style={{ flex: '1 0 250px', maxWidth: 320, minWidth: 220, padding: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px 8px' }}>
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)', flexShrink: 0 }}>标的({enabled.length})</span>
                    <input value={symbolQuery} onChange={e => setSymbolQuery(e.target.value)} placeholder="筛选…"
                      style={{
                        width: '100%', minWidth: 0, padding: '2px 8px', fontSize: 11,
                        background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                        borderRadius: 4, color: 'var(--text)',
                      }} />
                  </div>
                  {enabled.map(t => {
                    const cs = t.active_cycles || []
                    const isSel = t.symbol === sel.symbol
                    const hasWarn = cs.some(c => (c.open_dte ?? 99) <= 7 || openChecks[c.id]?.itm)
                    const hasProfit = cs.some(c => openChecks[c.id]?.profit_hit)
                    const isIdle = t.idle_days != null && t.idle_days >= 5
                    return (
                      <div key={t.symbol} onClick={() => { setSelectedSymbol(t.symbol); setEditParams(null); setSelectedCycleId(null) }} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '9px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 2,
                        background: isSel ? 'var(--accent)22' : 'transparent',
                        borderLeft: `3px solid ${isSel ? 'var(--accent)' : 'transparent'}`,
                      }}>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 700, fontSize: 14, display: 'flex', alignItems: 'center', gap: 5 }}>
                            {t.symbol}
                            {hasWarn && <span title="临期或ITM" style={{ fontSize: 11 }}>⚠</span>}
                            {hasProfit && <span title="浮盈达标,可平仓" style={{ fontSize: 11 }}>💰</span>}
                            {isIdle && <span title={`空转 ${t.idle_days} 天`} style={{ fontSize: 11 }}>⏸</span>}
                          </div>
                          <div style={{ fontSize: 10, marginTop: 1 }}>
                            <span style={{ color: targetCapital(cs) > 0 ? '#38bdf8' : 'var(--text-secondary)' }}>
                              占用 ${fmtMoney(targetCapital(cs))}
                              {(t.max_capital ?? 0) > 0 && <span style={{ color: 'var(--text-secondary)' }}>/{fmtMoney(t.max_capital)}</span>}
                            </span>
                            <span style={{ color: (premiumBySymbol[t.symbol] || 0) > 0 ? '#4ade80' : 'var(--text-secondary)', marginLeft: 8 }}>
                              权利金 ${fmtMoney(premiumBySymbol[t.symbol] || 0)}
                            </span>
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
                          {cs.length === 0 ? (
                            <span style={{ fontSize: 10, color: 'var(--text-secondary)' }}>未开轮</span>
                          ) : cs.map(c => (
                            <span key={c.id} title={STAGE_LABELS[c.status]} style={{
                              width: 9, height: 9, borderRadius: '50%',
                              background: STAGE_COLORS[c.status], display: 'inline-block',
                            }} />
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* ── 右:选中标的详情 ── */}
                <div style={{ flex: '999 1 480px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {/* 标的头部(参数可行内编辑) */}
                  <div className="card" style={{ padding: '10px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: 17 }}>{sel.symbol}</span>
                        {sel.volatility_brief && (sel.volatility_brief.atm_iv != null || sel.volatility_brief.hv20 != null) && (
                          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}
                            title={`预期IV:最近一次ATM隐含波动率快照(${sel.volatility_brief.iv_date || '--'});实际IV:20日历史波动率;IVR:IV在自身252日历史中的百分位${sel.volatility_brief.iv_rank_source === 'hv_proxy' ? '(IV历史不足,HV近似)' : ''}`}>
                            IV <b style={{ color: 'var(--text)' }}>{sel.volatility_brief.atm_iv != null ? sel.volatility_brief.atm_iv.toFixed(1) : '--'}</b>
                            {' / HV '}<b style={{ color: 'var(--text)' }}>{sel.volatility_brief.hv20 != null ? sel.volatility_brief.hv20.toFixed(1) : '--'}</b>
                            {sel.volatility_brief.iv_rank != null && <>
                              {' · IVR '}
                              <b style={{ color: sel.volatility_brief.iv_rank >= 70 ? '#f87171' : sel.volatility_brief.iv_rank >= 50 ? '#fb923c' : 'var(--text)' }}>
                                {sel.volatility_brief.iv_rank.toFixed(0)}
                              </b>
                              {sel.volatility_brief.iv_rank_source === 'hv_proxy' && <span style={{ fontSize: 10 }}>≈</span>}
                            </>}
                          </span>
                        )}
                        {!editParams && (
                          <>
                            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                              底线 <b style={{ color: 'var(--text)' }}>${fmt(sel.floor_price)}</b>
                              {' · '}资金上限 <b style={{ color: 'var(--text)' }}>
                                {(sel.max_capital ?? 0) > 0 ? `$${fmt(sel.max_capital)}` : '未设'}
                              </b>
                              {' · '}Δ <b style={{ color: 'var(--text)' }}>{sel.delta_min}~{sel.delta_max}</b>
                              {' · '}DTE <b style={{ color: 'var(--text)' }}>{sel.dte_min}~{sel.dte_max}</b>
                              {' · '}年化≥<b style={{ color: 'var(--text)' }}>{sel.min_annualized}%</b>
                            </span>
                            <button title="修改找货参数" onClick={() => setEditParams({
                              floor_price: String(sel.floor_price),
                              max_capital: String(sel.max_capital ?? 0),
                              delta_min: String(sel.delta_min),
                              delta_max: String(sel.delta_max), dte_min: String(sel.dte_min),
                              dte_max: String(sel.dte_max), min_annualized: String(sel.min_annualized),
                            })} style={{
                              border: '1px solid var(--border)', background: 'transparent', cursor: 'pointer',
                              borderRadius: 6, padding: '1px 8px', fontSize: 11, color: 'var(--accent)',
                            }}>✎ 编辑</button>
                          </>
                        )}
                        {sel.idle_days != null && sel.idle_days >= 5 && !editParams && (
                          <span style={{ fontSize: 12, color: '#fb923c' }}>⏸ 空转 {sel.idle_days} 天</span>
                        )}
                      </div>
                      {!editParams && (
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 14px' }}
                            disabled={suggestLoading} onClick={() => handleSuggest(sel.symbol, 'put')}>找 Put</button>
                          <button className="btn" style={{ fontSize: 12, padding: '4px 14px', color: 'var(--accent)' }}
                            onClick={() => setTradeModal({
                              initial: { symbol: sel.symbol, trade_type: 'SELL_PUT' },
                              status: 'NONE', newCycle: true,
                            })}>
                            + 新开轮子
                          </button>
                        </div>
                      )}
                    </div>
                    {editParams && (
                      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 8 }}>
                        {([
                          ['底线$', 'floor_price', 90],
                          ['资金上限$', 'max_capital', 100],
                          ['Δ min', 'delta_min', 64], ['Δ max', 'delta_max', 64],
                          ['DTE min', 'dte_min', 64], ['DTE max', 'dte_max', 64], ['年化≥%', 'min_annualized', 64],
                        ] as [string, keyof NonNullable<typeof editParams>, number][]).map(([lab, key, w]) => (
                          <label key={key} style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                            {lab}
                            <input type="number" value={editParams[key]} style={{
                              display: 'block', width: w, padding: '4px 6px', marginTop: 2,
                              background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                              borderRadius: 4, color: 'var(--text)', fontSize: 12,
                            }} onChange={e => setEditParams(f => f ? { ...f, [key]: e.target.value } : f)} />
                          </label>
                        ))}
                        <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 14px' }}
                          disabled={savingParams} onClick={async () => {
                            setSavingParams(true)
                            setError(null)
                            try {
                              await updateWheelTarget(sel.symbol, {
                                floor_price: parseFloat(editParams.floor_price),
                                max_capital: parseFloat(editParams.max_capital) || 0,
                                delta_min: parseFloat(editParams.delta_min),
                                delta_max: parseFloat(editParams.delta_max),
                                dte_min: parseInt(editParams.dte_min),
                                dte_max: parseInt(editParams.dte_max),
                                min_annualized: parseFloat(editParams.min_annualized),
                              })
                              setEditParams(null)
                              await loadAll()
                            } catch (e: any) {
                              setError('参数保存失败:' + e.message)
                            } finally {
                              setSavingParams(false)
                            }
                          }}>{savingParams ? '保存中...' : '保存'}</button>
                        <button className="btn" style={{ fontSize: 12, padding: '4px 14px' }}
                          onClick={() => setEditParams(null)}>取消</button>
                      </div>
                    )}
                  </div>

                  {/* 轮子列表(窄列) + 交易明细(右) */}
                  <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <div style={{ flex: '1 1 420px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {selCycles.length === 0 && (
                    <div className="card" style={{ padding: '20px 16px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
                      该标的还没有进行中的轮子 —— 点「找 Put」筛选合约,成交后回来登记开轮
                    </div>
                  )}
                  {selCycles.map((c, idx) => {
                    const status = c.status
                    const check = openChecks[c.id]
                    const profitPct = check?.profit_pct ?? null
                    const dteVal = c.open_dte ?? null
                    const hasOpen = status === 'CSP_OPEN' || status === 'CC_OPEN'
                    return (
                      <div key={c.id} className="card"
                        onClick={() => setSelectedCycleId(id => id === c.id ? null : c.id)}
                        style={{
                          padding: '8px 12px', display: 'flex', gap: 12, alignItems: 'center',
                          borderLeft: `3px solid ${STAGE_COLORS[status]}`,
                          cursor: 'pointer',
                          outline: selectedCycleId === c.id ? '1px solid var(--accent)' : 'none',
                          background: selectedCycleId === c.id ? 'var(--accent)11' : undefined,
                        }}>
                        {/* 左:信息区 */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          {/* 行1:流程 + 徽章 + 汇总 chips */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {selCycles.length > 1 && (
                              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>#{idx + 1}</span>
                            )}
                            <StageIndicator status={status} />
                            {check?.itm && <Badge color="red">ITM</Badge>}
                            {dteVal != null && dteVal <= 7 && <Badge color="orange">临期</Badge>}
                            {check?.profit_hit && <Badge color="green">达标</Badge>}
                            {check?.action_hint && !check.profit_hit && (
                              <Badge color={check.deep_itm ? 'red' : check.low_yield && !check.roll_21dte ? 'blue' : 'orange'}
                                title={(check.reasons || []).join(';')}>
                                👉 {check.action_hint}
                              </Badge>
                            )}
                            {status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3 && (
                              <Badge color="orange" title="持股但未挂 Call,theta 收入在流失">🪑 裸奔 {c.uncovered_days} 天</Badge>
                            )}
                            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                              {c.shares > 0 && <>持股 {c.shares} @ ${fmt(c.share_cost)}{' · '}</>}
                              {c.cost_basis != null && <>CB <b style={{ color: '#4ade80' }}>${fmt(c.cost_basis)}</b>{' · '}</>}
                              累计权利金 <b style={{ color: (c.total_premium ?? 0) > 0 ? '#4ade80' : 'var(--text)' }}>${fmt(c.total_premium)}</b>
                            </span>
                          </div>
                          {/* 行2:在场合约(标签网格) */}
                          {hasOpen && (
                            <div style={{ background: 'var(--bg-secondary)', borderRadius: 6, padding: '8px 10px', marginTop: 6 }}
                              title={c.open_contract_code || undefined}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
                                <span style={{
                                  padding: '0 7px', borderRadius: 8, fontSize: 10, fontWeight: 700,
                                  background: (c.open_option_type === 'PUT' ? '#38bdf8' : '#a78bfa') + '22',
                                  color: c.open_option_type === 'PUT' ? '#38bdf8' : '#a78bfa',
                                  border: `1px solid ${c.open_option_type === 'PUT' ? '#38bdf8' : '#a78bfa'}55`,
                                }}>{c.open_option_type}</span>
                                <b style={{ fontSize: 13 }}>${fmt(c.open_strike)}</b>
                                <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                                  {(c.open_expiry || '').slice(5)} · <b style={{ color: dteVal != null && dteVal <= 7 ? '#fb923c' : 'var(--text)' }}>{dteVal != null ? `${dteVal}天` : '--'}</b>
                                  {(c.open_qty || 1) > 1 && <> · {c.open_qty}张</>}
                                </span>
                              </div>
                              <div style={{ display: 'flex', gap: '6px 22px', flexWrap: 'wrap' }}>
                                {([
                                  ['开仓', `$${fmt(c.open_price)}`, undefined],
                                  ['现价', check ? `$${fmt(check.current_price)}` : '--', undefined],
                                  ['价值', check ? `$${fmtMoney(check.current_price * (c.open_qty || 1) * (c.open_contract_size || 100))}` : '--', undefined],
                                  ['浮盈', profitPct != null ? `${profitPct}%` : '--',
                                    profitPct == null ? undefined : profitPct >= profitTarget ? 'green' : profitPct < 0 ? 'red' : undefined],
                                  ['Δ', check && (check.delta ?? 0) > 0 ? check.delta!.toFixed(2) : '--', undefined],
                                  ['θ/天', check && (check.theta ?? 0) > 0
                                    ? `$${fmt(check.theta! * (c.open_qty || 1) * (c.open_contract_size || 100), 0)}` : '--',
                                    check && (check.theta ?? 0) > 0 ? 'green' : undefined],
                                  ['剩余年化', check?.remaining_annualized != null ? `${check.remaining_annualized}%` : '--',
                                    check?.low_yield ? 'blue' : undefined],
                                ] as [string, string, SemColor | undefined][]).map(([lab, val, color]) => (
                                  <Stat key={lab} label={lab} value={val} color={color} />
                                ))}
                              </div>
                              {profitPct != null && profitPct > 0 && (
                                <div style={{ marginTop: 7, display: 'flex', alignItems: 'center', gap: 8 }}>
                                  <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'var(--border)', overflow: 'hidden' }}>
                                    <div style={{
                                      height: '100%', borderRadius: 2,
                                      width: `${Math.min(profitPct / profitTarget * 100, 100)}%`,
                                      background: profitPct >= profitTarget ? '#4ade80' : '#38bdf8',
                                    }} />
                                  </div>
                                  <span style={{ fontSize: 10, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>止盈 {profitPct}/{profitTarget}%</span>
                                </div>
                              )}
                            </div>
                          )}
                        </div>

                        {/* 右:操作按钮列 */}
                        <div style={{
                          display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center',
                          flexShrink: 0, width: 84, borderLeft: '1px solid var(--border)', paddingLeft: 10,
                        }}>
                          {status === 'IDLE' && (
                            <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 0', width: '100%' }}
                              disabled={suggestLoading} onClick={() => handleSuggest(sel.symbol, 'put', c.id)}>找 Put</button>
                          )}
                          {status === 'HOLDING' && (
                            <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 0', width: '100%' }}
                              disabled={suggestLoading} onClick={() => handleSuggest(sel.symbol, 'call', c.id)}>找 Call</button>
                          )}
                          {hasOpen && check?.profit_hit && (
                            <button className="btn" style={{ fontSize: 11, padding: '3px 0', width: '100%', color: '#4ade80', fontWeight: 700 }}
                              onClick={() => setTradeModal({
                                initial: {
                                  symbol: sel.symbol,
                                  trade_type: status === 'CSP_OPEN' ? 'BUY_PUT_CLOSE' : 'BUY_CALL_CLOSE',
                                  price: String(check.buyback_ask || ''),
                                  qty: String(c.open_qty || 1),
                                  contract_size: String(c.open_contract_size || 100),
                                },
                                status, cycleId: c.id,
                              })}>
                              💰 平仓
                            </button>
                          )}
                          {hasOpen && (
                            <button className="btn" style={{ fontSize: 11, padding: '3px 0', width: '100%' }}
                              disabled={rollLoading} onClick={() => handleRoll(c.id)}>看 Roll</button>
                          )}
                          <button className="btn" style={{ fontSize: 11, padding: '3px 0', width: '100%' }}
                            onClick={() => setTradeModal({ initial: { symbol: sel.symbol }, status, cycleId: c.id })}>
                            登记交易
                          </button>
                        </div>
                      </div>
                    )
                  })}
                  </div>

                  {/* 交易明细面板 */}
                  <div style={{ flex: selectedCycleId ? '0 0 400px' : '0 0 200px', minWidth: 0 }}>
                    {(() => {
                      const dc = selCycles.find(c => c.id === selectedCycleId)
                      if (selCycles.length === 0) return null
                      if (!dc) return (
                        <div className="card" style={{ padding: '20px 14px', color: 'var(--text-secondary)', fontSize: 12, textAlign: 'center' }}>
                          ← 点击轮子查看交易明细
                        </div>
                      )
                      const cycleTrades = trades
                        .filter(t => t.cycle_id === dc.id)
                        .sort((a, b) => (a.traded_at < b.traded_at ? -1 : 1))
                      return (
                        <div className="card" style={{ padding: '10px 14px' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 700 }}>
                              交易明细
                              <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-secondary)', marginLeft: 8 }}>
                                {STAGE_LABELS[dc.status]} · 始于 {fmtDate(dc.started_at)} · {cycleTrades.length} 笔
                              </span>
                            </span>
                            <button className="btn" style={{ fontSize: 11, padding: '1px 8px' }}
                              onClick={() => setSelectedCycleId(null)}>关闭</button>
                          </div>
                          {cycleTrades.length === 0 && (
                            <div style={{ color: 'var(--text-secondary)', fontSize: 12, padding: '8px 0' }}>暂无交易记录</div>
                          )}
                          {cycleTrades.map(t => {
                            const cf = tradeCashFlow(t)
                            return (
                              <div key={t.id} style={{
                                display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8,
                                padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 12,
                              }}>
                                <div style={{ minWidth: 0 }}>
                                  <div>
                                    <b>{TRADE_LABELS[t.trade_type]}</b>
                                    {t.is_roll && (
                                      <span style={{ padding: '0 6px', borderRadius: 7, fontSize: 9, fontWeight: 700, background: '#a78bfa22', color: '#a78bfa', border: '1px solid #a78bfa55', marginLeft: 6 }}
                                        title="同日买回+再卖出,识别为一次 Roll">Roll</span>
                                    )}
                                    <span style={{ color: 'var(--text-secondary)', fontSize: 11, marginLeft: 8 }}>{fmtDate(t.traded_at)}</span>
                                  </div>
                                  <div style={{ color: 'var(--text-secondary)', fontSize: 11, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {t.strike != null && <>${fmt(t.strike)}</>}
                                    {t.expiry && <>{' · '}{String(t.expiry).slice(5, 10)}到期</>}
                                    {t.qty > 0 && t.price > 0 && <>{' · '}{t.qty}{['BUY_SHARES', 'SELL_SHARES'].includes(t.trade_type) ? '股' : '张'} × ${fmt(t.price)}</>}
                                    {t.fee > 0 && <>{' · '}费 {t.fee}</>}
                                    {t.note && <>{' · '}{t.note}</>}
                                  </div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                                  {cf != null && (
                                    <span style={{ fontSize: 12, fontWeight: 700, color: cf >= 0 ? '#4ade80' : '#f87171', whiteSpace: 'nowrap' }}>
                                      {cf >= 0 ? '+' : ''}{fmtMoney(Math.abs(cf)) === '0' ? cf.toFixed(2) : (cf < 0 ? '-' : '') + fmtMoney(Math.abs(cf))}
                                    </span>
                                  )}
                                  <button className="btn" style={{ fontSize: 11, padding: '1px 7px' }}
                                    onClick={() => setEditTrade(t)}>✎</button>
                                  <button className="btn" style={{ fontSize: 11, padding: '1px 7px', color: '#f87171' }}
                                    onClick={() => handleDeleteTrade(t)}>🗑</button>
                                </div>
                              </div>
                            )
                          })}
                          <div style={{ display: 'flex', gap: 14, fontSize: 11, color: 'var(--text-secondary)', paddingTop: 8 }}>
                            <span>累计权利金 <b style={{ color: (dc.total_premium ?? 0) > 0 ? '#4ade80' : 'var(--text)' }}>${fmt(dc.total_premium)}</b></span>
                            <span>手续费 ${fmt(dc.total_fees)}</span>
                            {dc.cost_basis != null && <span>Cost Basis ${fmt(dc.cost_basis)}</span>}
                            {dc.realized_pnl != null && <span>已实现 ${fmt(dc.realized_pnl)}</span>}
                          </div>
                        </div>
                      )
                    })()}
                  </div>
                  </div>
                </div>
              </div>
            )
          })()}

        </div>
      )}

      {/* ── 机会：今日可做 / 全部 / 触线档案 ── */}
      {tab === 'scan' && (() => {
        const sum = opps?.summary
        // 时间倒序
        const visible = [...(opps?.items || [])]
          .filter(o => !isOppIgnored(o.id))
          .sort((a, b) => {
            const ta = a.timing?.last_seen || a.event_at || ''
            const tb = b.timing?.last_seen || b.event_at || ''
            if (ta !== tb) return ta < tb ? 1 : -1 // 倒序
            return (b.score || 0) - (a.score || 0)
          })

        return (
          <div>
            {/* 摘要 + 操作 */}
            <div className="card" style={{ padding: '14px 16px', marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>
                {oppView === 'archive'
                  ? '触线档案 · 按最近发现倒序'
                  : (opps?.headline || (oppsLoading ? '正在合流…' : '点「刷新」生成清单'))}
              </div>
              {oppView !== 'archive' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(100px,1fr))', gap: 10, marginBottom: 12 }}>
                  {[
                    { label: '可做', v: sum?.actionable ?? '—', c: C.green },
                    { label: 'Put/Call', v: sum ? `${sum.actionable_put}/${sum.actionable_call}` : '—', c: C.blue },
                    { label: '★双满足', v: sum?.dual ?? '—', c: C.purple },
                    { label: '观察', v: sum?.watch ?? '—', c: C.orange },
                    { label: '资金空档', v: sum?.idle_slots ?? '—', c: C.blue },
                  ].map(x => (
                    <div key={x.label} style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-secondary)' }}>
                      <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{x.label}</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: x.c }}>{x.v}</div>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', fontSize: 12 }}>
                {oppView !== 'archive' && (
                  <>
                    <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }}
                      disabled={oppsLoading} onClick={() => loadOpportunities(false)}>
                      {oppsLoading ? '加载中…' : '刷新机会'}
                    </button>
                    <button className="btn" style={{ fontSize: 12, padding: '4px 12px' }}
                      disabled={oppsLoading} onClick={() => loadOpportunities(true)}>
                      强制重扫全池
                    </button>
                  </>
                )}
                <button className="btn" style={{ fontSize: 12, padding: '4px 12px' }}
                  disabled={timingScanning} onClick={async () => {
                    await handleTimingScan()
                    setTimeout(() => {
                      if (oppView === 'archive') {
                        getWheelTimingHistory(1, 20).then(setTimingHistory)
                        setHistoryPage(1)
                      } else loadOpportunities(false)
                    }, 2000)
                  }}>
                  {timingScanning ? '时机扫描中…' : '跑开仓时机'}
                </button>
                {oppView !== 'archive' && (
                  <button className="btn" style={{ fontSize: 12, padding: '4px 12px' }}
                    disabled={poolPushing} onClick={handlePoolPush}>
                    {poolPushing ? '推送中…' : '全池推 TG'}
                  </button>
                )}
                <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                  列表按时间倒序 · 默认今日可做
                </span>
              </div>
            </div>

            {/* Segment: 今日可做 | 全部机会 | 触线档案 */}
            <div style={{ display: 'flex', gap: 0, marginBottom: 14, borderBottom: '1px solid var(--border)' }}>
              {([
                ['actionable', '今日可做'],
                ['all', '全部机会'],
                ['archive', '触线档案'],
              ] as const).map(([k, lab]) => (
                <div key={k} onClick={() => { setOppView(k); if (k === 'archive') setHistoryPage(1) }}
                  style={{
                    padding: '8px 18px', cursor: 'pointer', fontSize: 13, fontWeight: oppView === k ? 700 : 400,
                    borderBottom: oppView === k ? '2px solid var(--accent)' : '2px solid transparent',
                    color: oppView === k ? 'var(--accent)' : 'var(--text-secondary)',
                  }}>
                  {lab}
                  {k === 'actionable' && sum?.actionable != null ? ` (${sum.actionable})` : ''}
                </div>
              ))}
            </div>

            {/* ── 触线档案 ── */}
            {oppView === 'archive' && (
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
                  触线落库档案（按合约去重合并）· 最近发现倒序 · 完整复盘用
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      {['最近发现', '方向', '标的', '合约', 'Strike', '到期(DTE)', '触发价', 'Δ', '年化%', '触及均线', 'IV分位', '标的价', '次数', '首次发现', '操作'].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(!timingHistory || timingHistory.items.length === 0) && (
                      <tr><td colSpan={15} style={{ padding: '20px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>
                        暂无档案，点上方「跑开仓时机」
                      </td></tr>
                    )}
                    {[...(timingHistory?.items || [])]
                      .sort((a, b) => (a.last_seen < b.last_seen ? 1 : -1))
                      .map(item => (
                      <tr key={item.contract_code} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{fmtDate(item.last_seen)}</td>
                        <td style={{ padding: '7px 10px' }}>
                          <span style={{
                            padding: '1px 8px', borderRadius: 4, fontWeight: 700, fontSize: 11,
                            background: item.side === 'PUT' ? '#38bdf822' : '#fbbf2422',
                            color: item.side === 'PUT' ? '#38bdf8' : '#fbbf24',
                          }}>{item.side === 'PUT' ? '卖Put' : '卖Call'}</span>
                        </td>
                        <td style={{ padding: '7px 10px', fontWeight: 600 }}>{item.symbol}</td>
                        <td style={{ padding: '7px 10px', fontFamily: 'monospace', fontSize: 11 }}>{item.contract_code}</td>
                        <td style={{ padding: '7px 10px' }}>{item.strike != null ? `$${fmt(item.strike)}` : '--'}</td>
                        <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{item.expiry || '--'}{item.dte != null ? `(${item.dte}天)` : ''}</td>
                        <td style={{ padding: '7px 10px' }}>
                          ${fmt(item.trigger_price)}
                          {!!item.below_floor && <span title="低于接货底线" style={{ color: '#f87171', fontSize: 10, marginLeft: 4 }}>低于底线</span>}
                        </td>
                        <td style={{ padding: '7px 10px' }}>{item.delta != null ? item.delta.toFixed(2) : '--'}</td>
                        <td style={{ padding: '7px 10px', color: '#4ade80', fontWeight: 600 }}>{item.annualized != null ? fmt(item.annualized, 1) : '--'}</td>
                        <td style={{ padding: '7px 10px' }}>
                          {item.ema_type === 'EMA200' ? '🔥' : ''}{item.ema_type}({fmt(item.ema_value)})
                        </td>
                        <td style={{ padding: '7px 10px' }}>{item.iv_rank != null ? fmt(item.iv_rank, 0) : '--'}</td>
                        <td style={{ padding: '7px 10px' }}>${fmt(item.underlying_price)}</td>
                        <td style={{ padding: '7px 10px', textAlign: 'center' }}>{item.times_triggered}</td>
                        <td style={{ padding: '7px 10px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{fmtDate(item.first_seen)}</td>
                        <td style={{ padding: '7px 10px' }}>
                          <button className="btn" style={{ fontSize: 11, padding: '2px 8px', marginRight: 4 }}
                            disabled={suggestLoading}
                            onClick={() => handleSuggest(item.symbol, item.side === 'PUT' ? 'put' : 'call')}>
                            详情
                          </button>
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '2px 8px' }}
                            onClick={() => setTradeModal({
                              initial: {
                                symbol: item.symbol,
                                trade_type: item.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
                                contract_code: item.contract_code,
                                strike: item.strike != null ? String(item.strike) : '',
                                expiry: item.expiry || '',
                                price: item.trigger_price != null ? String(item.trigger_price) : '',
                              },
                              status: item.side === 'PUT' ? 'IDLE' : 'HOLDING',
                            })}>
                            已下单,登记
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {timingHistory && timingHistory.total > timingHistory.page_size && (
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14, fontSize: 13 }}>
                    <button className="btn" style={{ fontSize: 12, padding: '3px 12px' }}
                      disabled={historyPage <= 1} onClick={() => setHistoryPage(p => p - 1)}>上一页</button>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      第 {timingHistory.page} / {Math.ceil(timingHistory.total / timingHistory.page_size)} 页 · 共 {timingHistory.total} 条
                    </span>
                    <button className="btn" style={{ fontSize: 12, padding: '3px 12px' }}
                      disabled={historyPage >= Math.ceil(timingHistory.total / timingHistory.page_size)}
                      onClick={() => setHistoryPage(p => p + 1)}>下一页</button>
                  </div>
                )}
              </div>
            )}

            {/* ── 今日可做 / 全部机会 ── */}
            {oppView !== 'archive' && (
              <div>
                {/* 方向 + 细过滤 */}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
                  {(['ALL', 'PUT', 'CALL'] as const).map(s => (
                    <button key={s} type="button" className="btn" style={{
                      fontSize: 11, padding: '3px 10px',
                      borderColor: oppSide === s ? C.green : undefined,
                      color: oppSide === s ? C.green : undefined,
                      fontWeight: oppSide === s ? 700 : 400,
                    }} onClick={() => setOppSide(s)}>{s === 'ALL' ? '全部方向' : s}</button>
                  ))}
                  {oppView === 'all' && (
                    <>
                      <span style={{ width: 1, height: 16, background: 'var(--border)' }} />
                      {([
                        ['all', '不过滤'],
                        ['dual', '★双满足'],
                        ['timing', '含触线'],
                        ['score', '含打分'],
                        ['watch', '观察'],
                        ['blocked', '红线'],
                      ] as [OppFilter, string][]).map(([k, lab]) => (
                        <button key={k} type="button" className="btn" style={{
                          fontSize: 11, padding: '3px 10px',
                          borderColor: oppFilter === k ? C.blue : undefined,
                          color: oppFilter === k ? C.blue : undefined,
                          fontWeight: oppFilter === k ? 700 : 400,
                        }} onClick={() => setOppFilter(k)}>{lab}</button>
                      ))}
                    </>
                  )}
                </div>

                {(opps?.idle_slots?.length ?? 0) > 0 && (
                  <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 12 }}>
                    <b>资金空档标的</b>（有余量但无可做 Put）:{' '}
                    {opps!.idle_slots.map(s => (
                      <button key={s.symbol} type="button" className="btn" style={{ fontSize: 11, padding: '1px 8px', marginRight: 6 }}
                        onClick={() => goBoardSymbol(s.symbol)}>
                        {s.symbol}{s.headroom != null ? `(余${fmt(s.headroom, 0)})` : ''}
                      </button>
                    ))}
                  </div>
                )}

                <div className="card" style={{ padding: '12px 14px' }}>
                  {oppsLoading && <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>合流中…</div>}
                  {!oppsLoading && visible.length === 0 && (
                    <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                      {oppView === 'actionable'
                        ? '今日暂无可做。可切「全部机会」、跑「开仓时机」或「强制重扫全池」(需 OpenD)。'
                        : '当前过滤下无机会。可切「不过滤」、跑「开仓时机」或「强制重扫全池」。'}
                    </div>
                  )}
                  {visible.length > 0 && (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                      <thead>
                        <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>
                          {['来源', '时间', '标的', '阶段', '方向', 'Strike', '到期', 'DTE', 'δ', '权利金', '年化', '分', '时机', '标签', '动作'].map(h => (
                            <th key={h} style={{ textAlign: 'left', padding: '6px 6px', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map(o => {
                          const srcLabel = o.source === 'dual' ? '★双' : o.source === 'timing' ? '触线' : '打分'
                          const srcColor = o.source === 'dual' ? C.purple : o.source === 'timing' ? C.blue : C.green
                          const seen = o.timing?.last_seen || o.event_at
                          const seenLabel = seen ? fmtDate(seen) : '—'
                          return (
                            <tr key={o.id} style={{
                              borderTop: '1px solid var(--border)',
                              background: o.actionable ? C.green + '08' : undefined,
                            }}>
                              <td style={{ padding: '6px', fontWeight: 700, color: srcColor }} title={o.contract_code || o.id}>
                                {srcLabel}
                              </td>
                              <td style={{ padding: '6px', fontSize: 11, whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}
                                title={seen || ''}>
                                {seenLabel}
                              </td>
                              <td style={{ padding: '6px', fontWeight: 600 }}>
                                <button type="button" onClick={() => goBoardSymbol(o.symbol)}
                                  style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontWeight: 700, padding: 0 }}>
                                  {o.symbol}
                                </button>
                                {o.contract_code && (
                                  <div style={{ fontSize: 10, color: 'var(--text-secondary)', fontFamily: 'monospace' }}
                                    title={o.contract_code}>
                                    {(o as any).contract_short || o.contract_code.replace(/^US\./, '')}
                                  </div>
                                )}
                              </td>
                              <td style={{ padding: '6px', fontSize: 11, color: 'var(--text-secondary)' }}>
                                {STAGE_LABELS[o.context?.stage || ''] || o.context?.stage || '—'}
                                {o.context?.headroom != null && <div>余{fmt(o.context.headroom, 0)}</div>}
                              </td>
                              <td style={{ padding: '6px', color: o.side === 'PUT' ? C.green : C.purple }}>
                                {o.side === 'PUT' ? 'Put' : 'Call'}
                              </td>
                              <td style={{ padding: '6px', fontWeight: 600 }}>{o.strike != null ? `$${fmt(o.strike)}` : '—'}</td>
                              <td style={{ padding: '6px', whiteSpace: 'nowrap' }}>{o.expiry || '—'}</td>
                              <td style={{ padding: '6px' }}>{o.dte ?? '—'}</td>
                              <td style={{ padding: '6px' }}>{o.delta != null ? Number(o.delta).toFixed(2) : '—'}</td>
                              <td style={{ padding: '6px' }}>{o.premium_used != null ? fmt(o.premium_used) : o.bid != null ? fmt(o.bid) : '—'}</td>
                              <td style={{ padding: '6px', color: C.green }}>{o.annualized != null ? fmt(o.annualized, 1) : '—'}</td>
                              <td style={{ padding: '6px', fontWeight: 700 }}
                                title={o.score_factors ? Object.entries(o.score_factors).map(([k, v]) => `${k}=${v}`).join(' × ') : '需全池扫描匹配后才有综合分'}>
                                {o.score != null ? fmt(o.score, 1) : '—'}
                              </td>
                              <td style={{ padding: '6px', fontSize: 11 }}>
                                {o.timing ? (
                                  <>
                                    {o.timing.ema_type}
                                    {o.timing.strength === 'STRONG' && ' 🔥'}
                                    {o.timing.strength === 'READY' && ' ✓'}
                                    {o.timing.strength === 'WATCH' && ' 👀'}
                                    {o.timing.trigger_price != null && (
                                      <div style={{ color: 'var(--text-secondary)' }}>@{fmt(o.timing.trigger_price)}</div>
                                    )}
                                  </>
                                ) : '—'}
                              </td>
                              <td style={{ padding: '6px', fontSize: 10 }}>
                                {(o.flags || []).map(f => (
                                  <span key={f} style={{ color: C.orange, marginRight: 4 }}>{f}</span>
                                ))}
                                {o.actionable && <span style={{ color: C.green }}>可做</span>}
                              </td>
                              <td style={{ padding: '6px', whiteSpace: 'nowrap' }}>
                                <button className="btn" style={{ fontSize: 10, padding: '1px 6px', marginRight: 4 }}
                                  disabled={suggestLoading}
                                  onClick={() => handleSuggest(o.symbol, o.side === 'CALL' ? 'call' : 'put', o.cycle_id || undefined)}>
                                  详情
                                </button>
                                <button className="btn btn-primary" style={{ fontSize: 10, padding: '1px 6px', marginRight: 4 }}
                                  onClick={() => registerFromOpp(o)}>
                                  登记
                                </button>
                                <button className="btn" style={{ fontSize: 10, padding: '1px 6px', marginRight: 4 }}
                                  onClick={() => goBoardSymbol(o.symbol)}>
                                  看板
                                </button>
                                <button className="btn" style={{ fontSize: 10, padding: '1px 6px' }}
                                  onClick={() => ignoreOpp(o.id, 3)} title="忽略 3 天">
                                  忽略
                                </button>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )}
                </div>

                {(timingScanning || scanStatus?.finished_at) && (
                  <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
                    {timingScanning && '时机扫描进行中…完成后点刷新机会合流。'}
                    {!timingScanning && scanStatus?.finished_at && (
                      <>最近时机扫描 {fmtDate(scanStatus.finished_at)} · 触发 {scanStatus.signals_found} 条
                        {scanStatus.telegram_sent ? ` · TG ${scanStatus.telegram_sent}` : ''}</>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })()}

      {tab === 'optimize' && (
        <div className="card" style={{ padding: 16 }}>
          <WheelOptimizePanel />
        </div>
      )}

      {/* ── 台账 ── */}
      {tab === 'ledger' && (
        <div>
          {/* 绩效复盘 */}
          {stats?.monthly_premium && stats.monthly_premium.length > 0 && (
            <div style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div className="card" style={{ padding: '12px 16px', flex: '1 1 320px', minWidth: 0 }}>
                <h3 style={{ fontSize: 13, margin: '0 0 10px' }}>月度净权利金</h3>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 90 }}>
                  {(() => {
                    const mp = stats.monthly_premium!
                    const maxAbs = Math.max(...mp.map(m => Math.abs(m.premium)), 1)
                    return mp.map(m => (
                      <div key={m.ym} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 0 }}
                        title={`${m.ym}:$${fmt(m.premium)}`}>
                        <span style={{ fontSize: 9, color: m.premium >= 0 ? C.green : C.red }}>{fmtMoney(Math.abs(m.premium))}</span>
                        <div style={{
                          width: '70%', borderRadius: '3px 3px 0 0',
                          height: Math.max(Math.abs(m.premium) / maxAbs * 60, 2),
                          background: m.premium >= 0 ? C.green : C.red, opacity: 0.85,
                        }} />
                        <span style={{ fontSize: 9, color: 'var(--text-secondary)' }}>{m.ym.slice(5)}</span>
                      </div>
                    ))
                  })()}
                </div>
              </div>
              {stats.symbol_ranking && stats.symbol_ranking.length > 0 && (
                <div className="card" style={{ padding: '12px 16px', flex: '1 1 380px', minWidth: 0 }}>
                  <h3 style={{ fontSize: 13, margin: '0 0 8px' }}>标的收益排行<span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-secondary)', marginLeft: 8 }}>谁值得继续轮,谁该踢出池子</span></h3>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ color: 'var(--text-secondary)' }}>
                        {['标的', '净权利金', '已实现盈亏', '完成轮数', '参与天数'].map(h => (
                          <th key={h} style={{ textAlign: 'left', padding: '3px 8px', fontWeight: 500 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {stats.symbol_ranking.map(r => (
                        <tr key={r.symbol} style={{ borderTop: '1px solid var(--border)' }}>
                          <td style={{ padding: '4px 8px', fontWeight: 600 }}>{r.symbol}</td>
                          <td style={{ padding: '4px 8px', color: r.premium > 0 ? C.green : r.premium < 0 ? C.red : undefined }}>${fmt(r.premium)}</td>
                          <td style={{ padding: '4px 8px', color: r.realized_pnl > 0 ? C.green : r.realized_pnl < 0 ? C.red : undefined }}>${fmt(r.realized_pnl)}</td>
                          <td style={{ padding: '4px 8px' }}>{r.closed_cycles}</td>
                          <td style={{ padding: '4px 8px', color: 'var(--text-secondary)' }}>{r.active_days ?? '--'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          <h3 style={{ fontSize: 14, marginBottom: 10 }}>周期({cycles.length})</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 28 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['标的', '状态', '持股', 'Cost Basis', '净权利金', '已实现盈亏', '开始', '结束', '天数'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cycles.length === 0 && <tr><td colSpan={9} style={{ padding: '16px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无周期</td></tr>}
              {cycles.map(c => (
                <tr key={c.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 10px', fontWeight: 600 }}>{c.symbol}</td>
                  <td style={{ padding: '7px 10px', color: STAGE_COLORS[c.status] }}>{STAGE_LABELS[c.status]}</td>
                  <td style={{ padding: '7px 10px' }}>{c.shares > 0 ? `${c.shares} @ $${fmt(c.share_cost)}` : '--'}</td>
                  <td style={{ padding: '7px 10px' }}>{c.cost_basis != null ? `$${fmt(c.cost_basis)}` : '--'}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(c.total_premium)}</td>
                  <td style={{ padding: '7px 10px', color: (c.realized_pnl ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                    {c.realized_pnl != null ? `$${fmt(c.realized_pnl)}` : '--'}
                  </td>
                  <td style={{ padding: '7px 10px', color: 'var(--text-secondary)' }}>{fmtDate(c.started_at)}</td>
                  <td style={{ padding: '7px 10px', color: 'var(--text-secondary)' }}>{fmtDate(c.closed_at)}</td>
                  <td style={{ padding: '7px 10px' }}>{c.duration_days ?? '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3 style={{ fontSize: 14, marginBottom: 10 }}>交易明细({trades.length})</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['时间', '标的', '类型', '合约/Strike', '张数', '价格', '手续费', '备注', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 && <tr><td colSpan={9} style={{ padding: '16px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无交易记录</td></tr>}
              {trades.map(t => (
                <tr key={t.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 10px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{fmtDate(t.traded_at)}</td>
                  <td style={{ padding: '7px 10px', fontWeight: 600 }}>{t.symbol}</td>
                  <td style={{ padding: '7px 10px' }}>
                    {TRADE_LABELS[t.trade_type]}
                    {t.is_roll && (
                      <span style={{ padding: '0 6px', borderRadius: 7, fontSize: 9, fontWeight: 700, background: '#a78bfa22', color: '#a78bfa', border: '1px solid #a78bfa55', marginLeft: 6 }}
                        title="同日买回+再卖出,识别为一次 Roll">Roll</span>
                    )}
                  </td>
                  <td style={{ padding: '7px 10px', fontFamily: 'monospace', fontSize: 11 }}>
                    {t.contract_code || (t.strike ? `$${fmt(t.strike)}` : '--')}
                  </td>
                  <td style={{ padding: '7px 10px' }}>{t.qty}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(t.price)}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(t.fee)}</td>
                  <td style={{ padding: '7px 10px', color: 'var(--text-secondary)' }}>{t.note || ''}</td>
                  <td style={{ padding: '7px 10px' }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => setEditTrade(t)}>编辑</button>
                      <button className="btn" style={{ fontSize: 11, padding: '2px 8px', color: '#f87171' }} onClick={() => handleDeleteTrade(t)}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8 }}>
            修改或删除任意一笔后,所属周期会按剩余交易按时间顺序重新计算;若剩余序列不合法(如删掉卖出腿但保留行权腿)会拒绝并保持原样
          </div>
        </div>
      )}

      {/* ── 标的设置 ── */}
      {tab === 'targets' && (
        <div>
          <div className="card" style={{ padding: '14px 18px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>添加标的</span>
            <input list="wheel-candidates" value={addSymbol} onChange={e => setAddSymbol(e.target.value)}
              placeholder="选择或输入,如 AAPL / 00700.HK" style={{ ...inputStyle, width: 220 }} />
            <datalist id="wheel-candidates">
              {candidates.map(c => (
                <option key={c.symbol} value={c.symbol}>{`${c.name}(${c.market === 'US' ? '美股' : '港股'})`}</option>
              ))}
            </datalist>
            <input type="number" value={addFloor} onChange={e => setAddFloor(e.target.value)}
              placeholder="接货底线价" style={{ ...inputStyle, width: 110 }} />
            <button className="btn btn-primary" style={{ fontSize: 13, padding: '5px 14px' }} disabled={adding} onClick={handleAddTarget}>
              {adding ? '添加中...' : '添加'}
            </button>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>候选与股票池美股/港股打通;delta/DTE/年化参数添加后可逐项调整</span>
          </div>

          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['标的', '底线价', '资金上限', 'Delta 区间', 'DTE 区间', '最低年化%', '最低OI', '状态', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {targets.length === 0 && <tr><td colSpan={9} style={{ padding: '20px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无标的,从上方添加</td></tr>}
              {targets.map(t => (
                <TargetRow key={t.symbol} target={t} onSaved={loadAll}
                  onToggle={() => handleToggleTarget(t)} onDelete={() => handleDeleteTarget(t.symbol)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 找 Put / 找 Call 结果弹窗 */}
      {(suggestLoading || suggest) && (
        <div
          style={{
            position: 'fixed', inset: 0, background: '#000a', zIndex: 110,
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
          }}
          onClick={closeSuggestModal}
        >
          <div
            className="modal-card"
            style={{
              width: 'min(960px, 100%)', maxHeight: '90vh', overflowY: 'auto',
              padding: '16px 20px',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 12 }}>
              <h3 style={{ margin: 0, fontSize: 16 }}>
                {suggest
                  ? `${suggest.symbol} 卖 ${suggest.side} 建议`
                  : `${suggestSymbol || '…'} 找 ${suggestSide === 'put' ? 'Put' : 'Call'}`}
                {suggest && (
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)', marginLeft: 10, fontWeight: 400 }}>
                    现价 ${fmt(suggest.spot_price)}
                    {suggest.cost_basis != null ? ` · Cost Basis $${fmt(suggest.cost_basis)}` : ''}
                  </span>
                )}
              </h3>
              <button className="btn" style={{ fontSize: 12, padding: '4px 12px', flexShrink: 0 }} onClick={closeSuggestModal}>
                关闭
              </button>
            </div>

            {suggestLoading && (
              <div style={{ padding: '28px 12px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
                正在拉取期权链并筛选（需富途 OpenD）…
              </div>
            )}

            {!suggestLoading && suggest && (
              <>
                {suggest.volatility && (
                  <div style={{ padding: '8px 12px', background: 'var(--bg-secondary)', borderRadius: 6, marginBottom: 10 }}>
                    <VolatilityBar v={suggest.volatility} />
                  </div>
                )}
                {(suggest.earnings_warn || suggest.delta_preference || suggest.trend_warning || suggest.dividend_warn || suggest.floor_suggest || suggest.term_structure || suggest.skew) && (
                  <div style={{ display: 'flex', gap: 16, fontSize: 12, marginBottom: 10, flexWrap: 'wrap' }}>
                    {suggest.earnings_warn && (
                      <span style={{ color: '#fb923c' }}>
                        ⚠ 财报 {suggest.earnings_date}(距今 {suggest.days_to_earnings} 天)
                        {(suggest.earnings_filtered_count || 0) > 0 && ` · 已硬过滤 ${suggest.earnings_filtered_count} 个含财报合约`}
                      </span>
                    )}
                    {suggest.dividend_warn && (
                      <span style={{ color: '#fb923c' }}>
                        ⚠ 除息 {suggest.dividend_warn.date}(剩{suggest.dividend_warn.days_to_ex}天)
                      </span>
                    )}
                    {suggest.trend_warning && <span style={{ color: '#f87171' }}>⚠ {suggest.trend_warning}</span>}
                    {suggest.delta_preference && <span style={{ color: '#38bdf8' }}>ℹ {suggest.delta_preference}</span>}
                    {suggest.term_structure?.shape && (
                      <span style={{ color: '#a78bfa' }}>
                        期限结构 {suggest.term_structure.shape}
                        {suggest.term_structure.term_spread != null ? ` (ΔIV ${suggest.term_structure.term_spread})` : ''}
                      </span>
                    )}
                    {suggest.skew?.warn && <span style={{ color: '#fb923c' }}>⚠ {suggest.skew.warn}</span>}
                    {suggest.floor_suggest && (
                      <span style={{ color: '#4ade80' }}>
                        智能 floor 建议 ${fmt(suggest.floor_suggest.suggested_floor)}
                        {suggest.floor_suggest.rationale ? ` · ${suggest.floor_suggest.rationale}` : ''}
                      </span>
                    )}
                  </div>
                )}
                {suggest.suggestions.length === 0 ? (
                  <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '16px 0' }}>
                    {suggest.message || '没有符合筛选条件的合约(delta/DTE/年化/流动性/财报硬过滤),可在「标的设置」或设置页放宽参数'}
                  </div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                          {['合约编号', 'Strike', 'Delta', 'POP', 'DTE', '权利金', '点差%', '年化%(现金)', ...(suggest.side === 'PUT' ? ['年化%(保证金)'] : []), '评分', '缓冲ATR', '虚值%', 'OI', suggest.side === 'PUT' ? '接货成本' : '若被行权赚', ''].map(h => (
                            <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {suggest.suggestions.map(s => (
                          <tr key={s.contract_code} style={{ borderBottom: '1px solid var(--border)' }}>
                            <td style={{ padding: '7px 10px', fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)' }}>{s.contract_code}</td>
                            <td style={{ padding: '7px 10px', fontWeight: 600 }}>
                              ${fmt(s.strike)}{s.covers_earnings && <span style={{ color: '#fb923c', fontSize: 10, marginLeft: 4 }}>含财报</span>}
                            </td>
                            <td style={{ padding: '7px 10px' }}>{s.delta}</td>
                            <td style={{ padding: '7px 10px', color: '#38bdf8' }}>{s.pop != null ? `${(s.pop * 100).toFixed(0)}%` : '--'}</td>
                            <td style={{ padding: '7px 10px' }}>{s.dte}</td>
                            <td style={{ padding: '7px 10px' }} title={`bid ${s.bid} / mid ${s.premium_used}`}>
                              ${fmt(s.premium_used ?? s.bid)}
                              <span style={{ fontSize: 10, color: 'var(--text-secondary)' }}> {s.premium_pricing || ''}</span>
                            </td>
                            <td style={{ padding: '7px 10px', color: (s.spread_pct ?? 0) > 6 ? '#fb923c' : undefined }}>{s.spread_pct != null ? s.spread_pct : '--'}</td>
                            <td style={{ padding: '7px 10px', color: '#4ade80', fontWeight: 700 }}>{fmt(s.annualized, 1)}</td>
                            {suggest.side === 'PUT' && (
                              <td style={{ padding: '7px 10px', color: '#38bdf8' }}>{s.annualized_margin != null ? fmt(s.annualized_margin, 1) : '--'}</td>
                            )}
                            <td style={{ padding: '7px 10px', fontWeight: 700 }}
                              title={s.score_factors ? Object.entries(s.score_factors).map(([k, v]) => `${k}=${v}`).join(' × ') : undefined}>
                              {s.score != null ? fmt(s.score, 1) : '--'}
                            </td>
                            <td style={{ padding: '7px 10px' }}>{s.buffer_atr != null ? fmt(s.buffer_atr, 2) : '--'}</td>
                            <td style={{ padding: '7px 10px' }}>{fmt(s.otm_pct, 1)}</td>
                            <td style={{ padding: '7px 10px' }}>{s.open_interest}</td>
                            <td style={{ padding: '7px 10px' }}>
                              {suggest.side === 'PUT' ? `$${fmt(s.assigned_cost)}` : `$${fmt(s.if_called_total)}`}
                            </td>
                            <td style={{ padding: '7px 10px' }}>
                              <button className="btn btn-primary" style={{ fontSize: 11, padding: '2px 10px', whiteSpace: 'nowrap' }}
                                onClick={() => {
                                  setTradeModal({
                                    initial: {
                                      symbol: suggest.symbol,
                                      trade_type: suggest.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
                                      contract_code: s.contract_code,
                                      strike: String(s.strike),
                                      expiry: s.expiry,
                                      price: String(s.limit_price_hint ?? s.premium_used ?? s.bid),
                                      contract_size: String(s.contract_size),
                                    },
                                    status: suggest.side === 'PUT' ? 'IDLE' : 'HOLDING',
                                    cycleId: suggestCycleId,
                                  })
                                }}>
                                已下单,登记
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 12 }}>
                  权利金默认 mid 估价；年化=权利金/现金担保×365/DTE；POP≈1−|Δ|。先在富途下单后，点「已下单,登记」填实际成交价。
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {tradeModal && (
        <TradeModal initial={tradeModal.initial} cycleStatus={tradeModal.status}
          cycleId={tradeModal.cycleId} newCycle={tradeModal.newCycle}
          minTradedAt={tradeModal.cycleId
            ? trades.filter(t => t.cycle_id === tradeModal.cycleId)
                .reduce<string | null>((m, t) => (!m || t.traded_at > m ? t.traded_at : m), null)
            : null}
          onClose={() => setTradeModal(null)} onSaved={() => { closeSuggestModal(); loadAll() }} />
      )}
      {editTrade && (
        <EditTradeModal trade={editTrade}
          onClose={() => setEditTrade(null)} onSaved={loadAll} />
      )}
      {rollData && (
        <RollModal data={rollData} onClose={() => setRollData(null)} onSaved={loadAll} />
      )}
    </div>
  )
}

// ── Roll 决策台弹窗 ───────────────────────────────────────────────────────────
function RollModal({ data: initial, onClose, onSaved }: {
  data: WheelRollOptions
  onClose: () => void
  onSaved: () => void
}) {
  const [data, setData] = useState(initial)
  const [allowDown, setAllowDown] = useState(!!initial.allow_down_strike)
  const [showTable, setShowTable] = useState(false)
  const [pricingMode, setPricingMode] = useState<'optimistic' | 'default' | 'conservative'>('default')
  const defaultCode = initial.default_candidate?.contract_code
    || initial.candidates[0]?.contract_code
    || null
  const [selected, setSelected] = useState<string | null>(defaultCode)
  const [buyback, setBuyback] = useState(String(
    initial.default_candidate?.limit_hints?.close_limit
    ?? initial.current.buyback_ask ?? '',
  ))
  const [newPrice, setNewPrice] = useState(String(
    initial.default_candidate?.limit_hints?.open_limit
    ?? initial.default_candidate?.bid
    ?? initial.candidates[0]?.bid
    ?? '',
  ))
  const [fee, setFee] = useState('0')
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const cand = data.candidates.find(c => c.contract_code === selected)
    || (data.default_candidate?.contract_code === selected ? data.default_candidate : null)
  const size = data.current.contract_size || 100
  const qty = data.qty || 1
  const netCredit = cand && buyback && newPrice
    ? ((parseFloat(newPrice) - parseFloat(buyback)) * size * qty).toFixed(0) : null

  const inputStyle = {
    width: 90, padding: '4px 6px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 12,
  } as const

  function pickCandidate(c: NonNullable<typeof cand>) {
    setSelected(c.contract_code)
    const lim = c.limit_hints
    const pr = c.pricing?.[pricingMode]
    setBuyback(String(lim?.close_limit ?? pr?.close_price ?? data.current.buyback_ask ?? ''))
    setNewPrice(String(lim?.open_limit ?? pr?.open_price ?? c.bid ?? ''))
  }

  async function reload(nextAllow = allowDown) {
    setReloading(true)
    setErr(null)
    try {
      const st = getAppSettings()
      const r = await getWheelRollOptions(data.cycle_id, st.marketHost, st.marketPort, {
        allow_down_strike: nextAllow,
        qty: data.qty,
      })
      setData(r)
      const code = r.default_candidate?.contract_code || r.candidates[0]?.contract_code || null
      setSelected(code)
      const dc = r.default_candidate || r.candidates[0]
      if (dc) {
        setBuyback(String(dc.limit_hints?.close_limit ?? r.current.buyback_ask ?? ''))
        setNewPrice(String(dc.limit_hints?.open_limit ?? dc.bid ?? ''))
      }
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setReloading(false)
    }
  }

  async function submitRoll() {
    if (!cand) { setErr('请选择新合约(或选择「不 Roll」的平仓路径)'); return }
    const bb = parseFloat(buyback); const np = parseFloat(newPrice); const f = parseFloat(fee) || 0
    if (isNaN(bb) || bb < 0 || isNaN(np) || np <= 0) { setErr('请填写有效价格'); return }
    setSaving(true)
    setErr(null)
    try {
      await registerWheelRoll({
        cycle_id: data.cycle_id,
        buyback_price: bb,
        sell_contract_code: cand.contract_code,
        sell_strike: cand.strike,
        sell_expiry: cand.expiry,
        sell_price: np,
        qty,
        fee_close: f,
        fee_open: f,
        contract_size: size,
      })
      onSaved()
      onClose()
    } catch (e: any) {
      setErr('Roll 登记失败(若买回已成功,请在台账检查后手动登记卖出腿):' + e.message)
    } finally {
      setSaving(false)
    }
  }

  // 兼容旧接口:没有 cards 时用候选拼一个最小决策台,避免看起来像「只有明细表」
  const cards = data.cards || {
    roll_out: {
      key: 'roll_out',
      title: 'Roll Out',
      available: !!data.candidates?.find(c => c.branch === 'out' || c.same_strike),
      summary: data.candidates?.find(c => c.branch === 'out' || c.same_strike)
        ? `K$${data.candidates.find(c => c.branch === 'out' || c.same_strike)!.strike}`
        : '无候选(需 OpenD 拉链)',
      candidate: data.candidates?.find(c => c.branch === 'out' || c.same_strike) || null,
      blurb: '同 strike 换更远到期',
    },
    adjust_strike: {
      key: 'adjust_strike',
      title: '调 strike',
      available: !!data.candidates?.find(c => c.branch && c.branch !== 'out'),
      summary: data.candidates?.find(c => c.branch && c.branch !== 'out')
        ? `K$${data.candidates.find(c => c.branch && c.branch !== 'out')!.strike}`
        : '无候选(需 OpenD 拉链)',
      candidate: data.candidates?.find(c => c.branch && c.branch !== 'out') || null,
      blurb: 'Call 上移 / Put 下移',
    },
    no_roll: {
      key: 'no_roll',
      title: '不 Roll',
      available: true,
      options: {
        close_now: {
          action: 'close_now',
          buyback_cost_per_contract: (data.current.buyback_ask || 0) * (data.current.contract_size || 100),
          when: '浮盈达标或需要资金时',
        },
        let_expire: { action: 'let_expire', buyback_cost_per_contract: 0, when: 'OTM 临期可持有' },
      },
      recommended_sub: 'close_now',
    },
  }
  const hl = data.highlighted_card || (data.candidates?.length ? 'roll_out' : 'no_roll')
  const cardOrder: Array<'roll_out' | 'adjust_strike' | 'no_roll'> = ['roll_out', 'adjust_strike', 'no_roll']
  const decision = data.decision || {
    headline: data.candidates?.length
      ? '已加载 Roll 候选,请在下方三卡片中选择'
      : '暂无链上候选 — 先看「不 Roll」建议,或启动 OpenD 后刷新',
    detail: '主界面是三张决策卡;明细表默认折叠,仅作备选浏览',
    profit_pct: data.current.profit_pct,
    remaining_annualized: data.current.remaining_annualized,
    itm: data.current.itm,
    deep_itm: false,
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: '#0009', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div className="modal-card" style={{ width: 780, maxWidth: '100%', maxHeight: '90vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 4px', fontSize: 16 }}>Roll 决策台 — {data.symbol} {data.side}</h3>
        <div style={{ fontSize: 11, color: C.blue, marginBottom: 10, fontWeight: 600 }}>
          ① 顶部结论 → ② 三张决策卡(主) → ③ 需要时再展开明细表
        </div>

        {/* 顶部场景结论 — 始终显示 */}
        <div style={{
          marginBottom: 12, padding: '10px 12px', borderRadius: 8,
          border: `1px solid ${decision.itm ? C.orange : C.blue}55`,
          background: (decision.itm ? C.orange : C.blue) + '14',
        }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>{decision.headline}</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{decision.detail}</div>
          <div style={{ fontSize: 11, marginTop: 6, color: 'var(--text-secondary)' }}>
            浮盈 {decision.profit_pct != null ? `${decision.profit_pct}%` : '--'}
            {' · '}剩余年化 {decision.remaining_annualized != null ? `${decision.remaining_annualized}%` : '--'}
            {' · '}{decision.itm ? (decision.deep_itm ? '深度 ITM' : 'ITM') : 'OTM'}
            {' · '}DTE {data.current.dte ?? '--'} · δ{data.current.delta}
            {data.spot_price != null && <> · 现价 ${fmt(data.spot_price)}</>}
          </div>
        </div>

        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
          当前 K${fmt(data.current.strike)} {data.current.expiry}
          {' · '}开仓 ${fmt(data.current.open_price)}
          {' · '}买回 bid/ask ${fmt(data.current.buyback_bid)}/{fmt(data.current.buyback_ask)}
        </div>

        {/* 硬约束 */}
        <div style={{ fontSize: 11, marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 3 }}>
          {data.side === 'CALL' && data.strike_floor?.call_min_strike != null && (
            <span style={{ color: C.green, fontWeight: 600 }}>
              硬底线 CALL strike ≥ 成本 ${fmt(data.strike_floor.call_min_strike)}（低于成本已全部过滤）
            </span>
          )}
          {data.side === 'PUT' && data.strike_floor?.put_max_strike != null && (
            <span style={{ color: C.green, fontWeight: 600 }}>
              硬上限 PUT strike ≤ floor ${fmt(data.strike_floor.put_max_strike)}
            </span>
          )}
          {data.delta_filter && (
            <span style={{ color: C.blue }}>
              δ {data.delta_filter.preferred[0].toFixed(2)}~{data.delta_filter.preferred[1].toFixed(2)}
              （目标 {data.delta_filter.target[0]}~{data.delta_filter.target[1]}）
              {data.liquidity && ` · 点差≤${data.liquidity.max_spread_pct}%`}
            </span>
          )}
          {data.events?.earnings_date && (
            <span style={{ color: C.orange }}>财报 {data.events.earnings_date}</span>
          )}
          {data.events?.dividend?.date && (
            <span style={{ color: C.orange }}>除息 {data.events.dividend.date}</span>
          )}
        </div>

        {/* 工具条 */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12, fontSize: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={allowDown} onChange={e => {
              const v = e.target.checked
              setAllowDown(v)
              reload(v)
            }} />
            允许不利方向调 strike
          </label>
          <span style={{ color: 'var(--text-secondary)' }}>报价情景</span>
          {(['default', 'conservative', 'optimistic'] as const).map(m => (
            <button key={m} type="button" className="btn" style={{
              fontSize: 11, padding: '2px 8px',
              borderColor: pricingMode === m ? C.blue : undefined,
              color: pricingMode === m ? C.blue : undefined,
            }} onClick={() => {
              setPricingMode(m)
              if (cand?.pricing?.[m]) {
                setBuyback(String(cand.pricing[m].close_price))
                setNewPrice(String(cand.pricing[m].open_price))
              }
            }}>{m === 'default' ? '默认 ask/bid' : m === 'conservative' ? '保守' : '乐观 mid'}</button>
          ))}
          <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }} disabled={reloading} onClick={() => reload()}>
            {reloading ? '刷新中…' : '刷新'}
          </button>
        </div>
        {data.pricing_legend && (
          <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 10 }}>
            默认={data.pricing_legend.default} · 保守={data.pricing_legend.conservative} · 乐观={data.pricing_legend.optimistic}
          </div>
        )}

        {err && <div className="alert alert-error" style={{ marginBottom: 10 }}>{err}</div>}
        {(data.warnings?.length ?? 0) > 0 && (
          <div style={{ marginBottom: 10, padding: '6px 10px', background: '#fb923c11', border: '1px solid #fb923c55', borderRadius: 6, fontSize: 12, color: '#fb923c' }}>
            {data.warnings!.map((w, i) => <div key={i}>⚠ {w}</div>)}
          </div>
        )}

        {/* 三卡片 — 主界面 */}
        <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>三张决策卡</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 10, marginBottom: 14 }}>
          {cardOrder.map(key => {
            const card = cards[key]
            if (!card) return null
            const active = hl === key
            const isNoRoll = key === 'no_roll'
            return (
              <div key={key} style={{
                padding: 12, borderRadius: 8,
                border: `2px solid ${active ? C.blue : 'var(--border)'}`,
                background: active ? C.blue + '12' : 'var(--bg-secondary)',
                opacity: card.available === false && !isNoRoll ? 0.65 : 1,
              }}>
                <div style={{ fontSize: 11, color: active ? C.blue : 'var(--text-secondary)', fontWeight: 700, marginBottom: 4 }}>
                  {active ? '★ 推荐 · ' : ''}{card.title}
                </div>
                {card.blurb && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>{card.blurb}</div>}
                {isNoRoll && card.options ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {(['close_now', 'let_expire'] as const).map(sub => {
                      const opt = card.options![sub]
                      if (!opt) return null
                      const rec = card.recommended_sub === sub
                      return (
                        <div key={sub} style={{
                          padding: 8, borderRadius: 6, fontSize: 11,
                          border: `1px solid ${rec ? C.green : 'var(--border)'}`,
                          background: rec ? C.green + '12' : 'transparent',
                        }}>
                          <div style={{ fontWeight: 700 }}>{sub === 'close_now' ? '止盈/平仓' : '放任到期'}{rec ? ' · 建议' : ''}</div>
                          <div>成本 ${fmt(opt.buyback_cost_per_contract, 0)}/张
                            {opt.locked_premium_est != null && ` · 锁定约 $${fmt(opt.locked_premium_est, 0)}`}</div>
                          <div style={{ color: 'var(--text-secondary)' }}>{opt.when}</div>
                        </div>
                      )
                    })}
                  </div>
                ) : card.available && card.candidate ? (
                  <>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{card.summary}</div>
                    <div style={{ fontSize: 11, marginBottom: 4 }}>
                      {(card.pros || []).slice(0, 2).map((p, i) => <div key={i} style={{ color: C.green }}>✓ {p}</div>)}
                      {(card.cons || []).slice(0, 2).map((p, i) => <div key={i} style={{ color: C.orange }}>· {p}</div>)}
                    </div>
                    {card.candidate.preview && (
                      <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 6 }}>
                        预览: 新K{String(card.candidate.preview.new_strike)} · DTE{String(card.candidate.preview.new_dte)}
                        {card.candidate.new_cost_basis_est != null && ` · 新成本≈$${fmt(card.candidate.new_cost_basis_est)}`}
                        {card.candidate.if_called_total != null && ` · 被call≈$${fmt(card.candidate.if_called_total, 0)}`}
                      </div>
                    )}
                    <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 10px', width: '100%' }}
                      onClick={() => pickCandidate(card.candidate!)}>
                      选用此方案
                    </button>
                  </>
                ) : (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{card.summary || '无候选'}</div>
                )}
              </div>
            )
          })}
        </div>

        {/* 同 strike 高亮 */}
        {(data.same_strike_highlights?.length ?? 0) > 0 && (
          <div style={{ marginBottom: 12, fontSize: 12 }}>
            <span style={{ fontWeight: 600, marginRight: 8 }}>同 strike Roll out:</span>
            {data.same_strike_highlights!.map(c => (
              <button key={c.contract_code} type="button" className="btn" style={{ fontSize: 11, padding: '2px 8px', marginRight: 6 }}
                onClick={() => pickCandidate(c)}>
                {String(c.expiry).slice(0, 10)} δ{c.delta ?? '—'} net {c.net_credit_per_contract >= 0 ? '+' : ''}{fmt(c.net_credit_per_contract, 0)}
              </button>
            ))}
          </div>
        )}

        {/* 明细表折叠 — 默认收起,不是主界面 */}
        <div style={{ marginBottom: 12, paddingTop: 8, borderTop: '1px dashed var(--border)' }}>
          <button type="button" className="btn" style={{ fontSize: 11, padding: '2px 10px' }}
            onClick={() => setShowTable(s => !s)}>
            {showTable ? '收起明细表' : `备选：展开候选明细表 (${data.candidates?.length || 0})`}
          </button>
          <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>
            明细仅供挑合约,优先用上面三张卡
          </span>
        </div>
        {showTable && (
          data.candidates.length === 0 ? (
            <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '10px 0' }}>
              没有符合成本/δ/流动性的候选
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, marginBottom: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                  {['', '档', '分支', 'Strike', '到期', 'DTE', 'δ', '点差%', 'default净', '保守净', '$/天', '年化', 'OI', ''].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '4px 6px' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.candidates.map(c => {
                  const pr = c.pricing?.[pricingMode]
                  const net = pr?.net_credit_per_contract ?? c.net_credit_per_contract
                  return (
                    <tr key={c.contract_code} style={{
                      borderBottom: '1px solid var(--border)', cursor: 'pointer',
                      background: selected === c.contract_code ? C.blue + '18' : undefined,
                    }} onClick={() => pickCandidate(c)}>
                      <td style={{ padding: '4px 6px' }}><input type="radio" checked={selected === c.contract_code} readOnly /></td>
                      <td style={{ padding: '4px 6px', color: c.band === 'preferred' ? C.green : C.orange }}>
                        {c.same_strike ? '同K' : c.band === 'preferred' ? '优' : '宽'}
                      </td>
                      <td style={{ padding: '4px 6px', color: C.purple }}>{c.branch}</td>
                      <td style={{ padding: '4px 6px', fontWeight: 600 }}>${fmt(c.strike)}</td>
                      <td style={{ padding: '4px 6px' }}>{String(c.expiry).slice(0, 10)}</td>
                      <td style={{ padding: '4px 6px' }}>{c.dte}</td>
                      <td style={{ padding: '4px 6px' }}>{c.delta ?? '—'}</td>
                      <td style={{ padding: '4px 6px', color: (c.spread_pct ?? 0) > 8 ? C.orange : undefined }}>{c.spread_pct ?? '—'}</td>
                      <td style={{ padding: '4px 6px', fontWeight: 700, color: net >= 0 ? C.green : C.red }}>
                        {net >= 0 ? '+' : ''}{fmt(net, 0)}
                      </td>
                      <td style={{ padding: '4px 6px', color: (c.net_credit_conservative ?? 0) >= 0 ? C.green : C.red }}>
                        {c.net_credit_conservative != null ? `${c.net_credit_conservative >= 0 ? '+' : ''}${fmt(c.net_credit_conservative, 0)}` : '—'}
                      </td>
                      <td style={{ padding: '4px 6px' }}>{c.credit_per_day ?? '—'}</td>
                      <td style={{ padding: '4px 6px' }}>{c.annualized != null ? fmt(c.annualized, 1) : '—'}</td>
                      <td style={{ padding: '4px 6px' }}>{c.open_interest ?? '—'}</td>
                      <td style={{ padding: '4px 6px', fontSize: 10 }}>
                        {c.covers_earnings && <span style={{ color: C.orange }}>财报 </span>}
                        {c.covers_dividend && <span style={{ color: C.orange }}>除息</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )
        )}

        {/* 限价建议 + 登记 */}
        {cand && (
          <div style={{
            marginBottom: 12, padding: 10, borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--bg-secondary)', fontSize: 12,
          }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>
              已选: K${fmt(cand.strike)} {String(cand.expiry).slice(0, 10)} δ{cand.delta ?? '—'} · {cand.branch}
            </div>
            {cand.limit_hints && (
              <div style={{ fontSize: 11, color: C.blue, marginBottom: 6 }}>
                富途限价建议: 平仓买 {cand.limit_hints.close_limit} / 开仓卖 {cand.limit_hints.open_limit}
                （净目标 {cand.limit_hints.net_credit_target >= 0 ? '+' : ''}{cand.limit_hints.net_credit_target}/股）
              </div>
            )}
            {cand.preview && (
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Roll 后预览: 新成本≈{cand.new_cost_basis_est != null ? `$${fmt(cand.new_cost_basis_est)}` : '—'}
                {cand.if_called_total != null && ` · 若被 call 估 $${fmt(cand.if_called_total, 0)}`}
                {cand.if_assigned_cost != null && ` · 若接货成本 $${fmt(cand.if_assigned_cost)}`}
                {' · '}效率 {cand.credit_per_day ?? '—'}$/天
              </div>
            )}
          </div>
        )}

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, marginBottom: 10 }}>
          <label>买回价 <input type="number" style={inputStyle} value={buyback} onChange={e => setBuyback(e.target.value)} /></label>
          <label>新卖价 <input type="number" style={inputStyle} value={newPrice} onChange={e => setNewPrice(e.target.value)} /></label>
          <label>手续费/腿 <input type="number" style={inputStyle} value={fee} onChange={e => setFee(e.target.value)} /></label>
          {netCredit != null && (
            <span>本次净{parseFloat(netCredit) >= 0 ? '收' : '付'} <b style={{ color: parseFloat(netCredit) >= 0 ? C.green : C.red }}>${netCredit}</b>
              {qty !== 1 ? ` (${qty}张)` : '/张'}</span>
          )}
        </div>

        {/* Roll 历史 */}
        {(data.roll_history?.length ?? 0) > 0 && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
            <b>历史 Roll:</b>{' '}
            {data.roll_history!.map((h, i) => (
              <span key={i} style={{ marginRight: 10 }}>
                {h.date} K{h.close_strike}→{h.open_strike} {h.net_credit >= 0 ? '+' : ''}{fmt(h.net_credit, 0)}
              </span>
            ))}
          </div>
        )}

        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
          在富途按限价建议完成两笔后,按实际成交价改数字再登记;同一 cycle 记买回+卖出两腿
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn btn-primary" disabled={saving || !cand} onClick={submitRoll}>
            {saving ? '登记中...' : '已在富途 Roll,登记两腿'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 台账编辑弹窗 ──────────────────────────────────────────────────────────────
function EditTradeModal({ trade, onClose, onSaved }: {
  trade: WheelTrade
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState({
    trade_type: trade.trade_type,
    contract_code: trade.contract_code || '',
    strike: trade.strike != null ? String(trade.strike) : '',
    expiry: trade.expiry || '',
    qty: String(trade.qty),
    price: String(trade.price),
    fee: String(trade.fee),
    contract_size: String(trade.contract_size),
    note: trade.note || '',
    traded_at: trade.traded_at || '',
  })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const inputStyle = {
    width: '100%', padding: '6px 8px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 13,
  } as const

  async function submit() {
    setErr(null)
    setSaving(true)
    try {
      await updateWheelTrade(trade.id, {
        trade_type: form.trade_type,
        contract_code: form.contract_code || undefined,
        strike: form.strike ? parseFloat(form.strike) : undefined,
        expiry: form.expiry || undefined,
        qty: form.qty ? parseFloat(form.qty) : undefined,
        price: form.price !== '' ? parseFloat(form.price) : undefined,
        fee: form.fee !== '' ? parseFloat(form.fee) : undefined,
        contract_size: form.contract_size ? parseInt(form.contract_size) : undefined,
        note: form.note || undefined,
        traded_at: form.traded_at || undefined,
      })
      onSaved()
      onClose()
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: '#0009', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div className="modal-card" style={{ width: 460, maxWidth: '100%', maxHeight: '85vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 6px', fontSize: 16 }}>编辑交易 — {trade.symbol}</h3>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 14 }}>
          保存后所属周期将按时间顺序重放重算;不合法的修改会被拒绝
        </div>
        {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
        <div style={{ display: 'grid', gap: 12 }}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            类型
            <select value={form.trade_type} style={inputStyle}
              onChange={e => setForm(f => ({ ...f, trade_type: e.target.value as WheelTradeType }))}>
              {(Object.keys(TRADE_LABELS) as WheelTradeType[]).map(t => (
                <option key={t} value={t}>{TRADE_LABELS[t]}</option>
              ))}
            </select>
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              合约代码
              <input value={form.contract_code} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, contract_code: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Strike
              <input type="number" value={form.strike} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, strike: e.target.value }))} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              到期日
              <input type="date" value={(form.expiry || '').slice(0, 10)} style={{ ...inputStyle, cursor: 'pointer' }}
                onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              成交时间
              <input type="datetime-local" value={(form.traded_at || '').slice(0, 16)} style={{ ...inputStyle, cursor: 'pointer' }}
                onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                onChange={e => setForm(f => ({ ...f, traded_at: e.target.value }))} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              张数
              <input type="number" value={form.qty} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              价格
              <input type="number" value={form.price} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, price: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              手续费
              <input type="number" value={form.fee} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, fee: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              合约乘数
              <input type="number" value={form.contract_size} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, contract_size: e.target.value }))} />
            </label>
          </div>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            备注
            <input value={form.note} style={inputStyle}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            <button className="btn" onClick={onClose}>取消</button>
            <button className="btn btn-primary" disabled={saving} onClick={submit}>
              {saving ? '保存中...' : '保存并重算'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 标的行(行内编辑)──────────────────────────────────────────────────────────
function TargetRow({ target, onSaved, onToggle, onDelete }: {
  target: WheelTarget
  onSaved: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({
    floor_price: String(target.floor_price),
    max_capital: String(target.max_capital ?? 0),
    delta_min: String(target.delta_min), delta_max: String(target.delta_max),
    dte_min: String(target.dte_min), dte_max: String(target.dte_max),
    min_annualized: String(target.min_annualized),
    min_open_interest: String(target.min_open_interest),
  })
  const [err, setErr] = useState<string | null>(null)

  const inputStyle = {
    width: 64, padding: '3px 6px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 12,
  } as const

  async function save() {
    setErr(null)
    try {
      await updateWheelTarget(target.symbol, {
        floor_price: parseFloat(form.floor_price),
        max_capital: parseFloat(form.max_capital) || 0,
        delta_min: parseFloat(form.delta_min), delta_max: parseFloat(form.delta_max),
        dte_min: parseInt(form.dte_min), dte_max: parseInt(form.dte_max),
        min_annualized: parseFloat(form.min_annualized),
        min_open_interest: parseInt(form.min_open_interest),
      })
      setEditing(false)
      onSaved()
    } catch (e: any) {
      setErr(e.message)
    }
  }

  if (!editing) {
    return (
      <tr style={{ borderBottom: '1px solid var(--border)' }}>
        <td style={{ padding: '8px 10px' }}>
          <span style={{ fontWeight: 600 }}>{target.symbol}</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 11, marginLeft: 6 }}>{target.name}</span>
        </td>
        <td style={{ padding: '8px 10px' }}>${fmt(target.floor_price)}</td>
        <td style={{ padding: '8px 10px' }}>
          {(target.max_capital ?? 0) > 0 ? `$${fmt(target.max_capital)}` : <span style={{ color: 'var(--text-secondary)' }}>未设</span>}
        </td>
        <td style={{ padding: '8px 10px' }}>{target.delta_min} ~ {target.delta_max}</td>
        <td style={{ padding: '8px 10px' }}>{target.dte_min} ~ {target.dte_max} 天</td>
        <td style={{ padding: '8px 10px' }}>{target.min_annualized}</td>
        <td style={{ padding: '8px 10px' }}>{target.min_open_interest}</td>
        <td style={{ padding: '8px 10px', color: target.enabled ? '#4ade80' : 'var(--text-secondary)' }}>
          {target.enabled ? '启用' : '停用'}
        </td>
        <td style={{ padding: '8px 10px' }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn" style={{ fontSize: 12, padding: '2px 8px' }} onClick={() => setEditing(true)}>编辑</button>
            <button className="btn" style={{ fontSize: 12, padding: '2px 8px' }} onClick={onToggle}>{target.enabled ? '停用' : '启用'}</button>
            <button className="btn" style={{ fontSize: 12, padding: '2px 8px', color: '#f87171' }} onClick={onDelete}>删除</button>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-secondary)' }}>
      <td style={{ padding: '8px 10px', fontWeight: 600 }}>{target.symbol}{err && <div style={{ color: '#f87171', fontSize: 11 }}>{err}</div>}</td>
      <td style={{ padding: '8px 10px' }}>
        <input type="number" style={inputStyle} value={form.floor_price} onChange={e => setForm(f => ({ ...f, floor_price: e.target.value }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <input type="number" style={{ ...inputStyle, width: 88 }} value={form.max_capital}
          title="该标的最大占用资金(0=不限)"
          onChange={e => setForm(f => ({ ...f, max_capital: e.target.value }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <input type="number" step="0.05" style={inputStyle} value={form.delta_min} onChange={e => setForm(f => ({ ...f, delta_min: e.target.value }))} />
        {' ~ '}
        <input type="number" step="0.05" style={inputStyle} value={form.delta_max} onChange={e => setForm(f => ({ ...f, delta_max: e.target.value }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <input type="number" style={inputStyle} value={form.dte_min} onChange={e => setForm(f => ({ ...f, dte_min: e.target.value }))} />
        {' ~ '}
        <input type="number" style={inputStyle} value={form.dte_max} onChange={e => setForm(f => ({ ...f, dte_max: e.target.value }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <input type="number" style={inputStyle} value={form.min_annualized} onChange={e => setForm(f => ({ ...f, min_annualized: e.target.value }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <input type="number" style={inputStyle} value={form.min_open_interest} onChange={e => setForm(f => ({ ...f, min_open_interest: e.target.value }))} />
      </td>
      <td style={{ padding: '8px 10px' }}></td>
      <td style={{ padding: '8px 10px' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-primary" style={{ fontSize: 12, padding: '2px 8px' }} onClick={save}>保存</button>
          <button className="btn" style={{ fontSize: 12, padding: '2px 8px' }} onClick={() => setEditing(false)}>取消</button>
        </div>
      </td>
    </tr>
  )
}
