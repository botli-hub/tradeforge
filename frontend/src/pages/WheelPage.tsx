import { useCallback, useEffect, useState } from 'react'
import {
  getAppSettings, subscribeSettings, type AppSettings,
  getWheelTargets, getWheelCandidates, addWheelTarget, updateWheelTarget, deleteWheelTarget,
  getWheelCycles, getWheelTrades, recordWheelTrade, updateWheelTrade, deleteWheelTrade,
  getWheelStats, getWheelSuggest, triggerWheelTimingScan, getWheelTimingSignals,
  getWheelScanStatus, getWheelTimingHistory, checkWheelOpenPositions, getWheelRollOptions,
  WheelTarget, WheelCycle, WheelTrade, WheelStats, WheelTradeType,
  WheelSuggestResponse, LeapsCandidate, LeapsSignal, VolatilityProfile,
  WheelScanStatus, WheelTimingHistoryPage, WheelOpenPositionItem, WheelRollOptions,
} from '../services/api'

const STAGE_LABELS: Record<string, string> = {
  IDLE: '空仓', CSP_OPEN: '卖Put中', HOLDING: '持股', CC_OPEN: '卖Call中', CLOSED: '已结束',
}
const STAGE_COLORS: Record<string, string> = {
  IDLE: 'var(--text-secondary)', CSP_OPEN: '#38bdf8', HOLDING: '#fbbf24', CC_OPEN: '#a78bfa', CLOSED: '#4ade80',
}
const TRADE_LABELS: Record<WheelTradeType, string> = {
  SELL_PUT: '卖出 Put', BUY_PUT_CLOSE: '买回 Put 平仓', SELL_CALL: '卖出 Call', BUY_CALL_CLOSE: '买回 Call 平仓',
  EXPIRE: '到期作废', ASSIGNED: '被行权接货', CALLED_AWAY: '被行权交货', SELL_SHARES: '卖出股票结束',
}
// 各状态允许的登记类型
const ALLOWED_TRADES: Record<string, WheelTradeType[]> = {
  NONE: ['SELL_PUT'],
  IDLE: ['SELL_PUT'],
  CSP_OPEN: ['EXPIRE', 'BUY_PUT_CLOSE', 'ASSIGNED'],
  HOLDING: ['SELL_CALL', 'SELL_SHARES'],
  CC_OPEN: ['EXPIRE', 'BUY_CALL_CLOSE', 'CALLED_AWAY'],
}

function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
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
            padding: '2px 8px', borderRadius: 10, fontSize: 11,
            background: i === idx ? STAGE_COLORS[s] + '33' : 'transparent',
            color: i === idx ? STAGE_COLORS[s] : 'var(--text-secondary)',
            border: `1px solid ${i === idx ? STAGE_COLORS[s] : 'var(--border)'}`,
            fontWeight: i === idx ? 700 : 400,
          }}>{STAGE_LABELS[s]}</span>
          {i < stages.length - 1 && <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}>→</span>}
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
}

function TradeModal({
  initial, cycleStatus, cycleId, newCycle, onClose, onSaved,
}: {
  initial: Partial<TradeFormState> & { symbol: string }
  cycleStatus: string
  cycleId?: string
  newCycle?: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const allowed = ALLOWED_TRADES[cycleStatus] || ['SELL_PUT']
  const [form, setForm] = useState<TradeFormState>({
    symbol: initial.symbol,
    trade_type: (initial.trade_type as WheelTradeType) || allowed[0],
    contract_code: initial.contract_code || '',
    strike: initial.strike || '',
    expiry: initial.expiry || '',
    qty: initial.qty || '1',
    price: initial.price || '',
    fee: initial.fee || '0',
    contract_size: initial.contract_size || '100',
    note: initial.note || '',
  })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const needContract = ['SELL_PUT', 'SELL_CALL'].includes(form.trade_type)
  const needPrice = !['EXPIRE', 'ASSIGNED', 'CALLED_AWAY'].includes(form.trade_type)

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
        <div style={{ display: 'grid', gap: 12 }}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            类型
            <select value={form.trade_type} style={inputStyle}
              onChange={e => setForm(f => ({ ...f, trade_type: e.target.value as WheelTradeType }))}>
              {allowed.map(t => <option key={t} value={t}>{TRADE_LABELS[t]}</option>)}
            </select>
          </label>
          {needContract && (
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              合约代码(选填)
              <input value={form.contract_code} style={inputStyle}
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
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              到期日(YYYY-MM-DD)
              <input value={form.expiry} placeholder="2026-08-21" style={inputStyle}
                onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
            </label>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {needContract && (
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                张数
                <input type="number" value={form.qty} style={inputStyle}
                  onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} />
              </label>
            )}
            {needPrice && (
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {form.trade_type === 'SELL_SHARES' ? '每股卖价' : '权利金/张'}
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
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            备注
            <input value={form.note} style={inputStyle}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
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
  const [tab, setTab] = useState<'board' | 'timing' | 'ledger' | 'targets'>('board')
  const [targets, setTargets] = useState<WheelTarget[]>([])
  const [stats, setStats] = useState<WheelStats | null>(null)
  const [cycles, setCycles] = useState<WheelCycle[]>([])
  const [trades, setTrades] = useState<WheelTrade[]>([])
  const [candidates, setCandidates] = useState<LeapsCandidate[]>([])
  const [error, setError] = useState<string | null>(null)

  // 助手
  const [suggest, setSuggest] = useState<WheelSuggestResponse | null>(null)
  const [suggestLoading, setSuggestLoading] = useState(false)

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
      const st = getAppSettings()
      checkWheelOpenPositions(st.marketHost, st.marketPort).then(r => {
        const map: Record<string, WheelOpenPositionItem> = {}
        r.items.forEach(i => { map[i.cycle_id] = i })
        setOpenChecks(map)
        setProfitTarget(r.profit_target_pct)
      }).catch(() => setOpenChecks({}))
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

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
    if (tab === 'timing') {
      getWheelTimingHistory(historyPage, 20).then(setTimingHistory).catch(() => setTimingHistory(null))
    }
  }, [tab, historyPage])

  async function handleSuggest(symbol: string, side: 'put' | 'call', cycleId?: string) {
    setSuggestLoading(true)
    setSuggest(null)
    setSuggestCycleId(cycleId)
    setError(null)
    try {
      const r = await getWheelSuggest(symbol, side, settings.marketHost, settings.marketPort, cycleId)
      setSuggest(r)
    } catch (e: any) {
      setError(`获取${side === 'put' ? 'Put' : 'Call'}建议失败:` + e.message)
    } finally {
      setSuggestLoading(false)
    }
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
    if (!confirm(`确定删除这笔「${TRADE_LABELS[t.trade_type]}」记录?周期状态会重新计算`)) return
    try {
      await deleteWheelTrade(t.id)
      await loadAll()
    } catch (e: any) {
      setError('删除失败:' + e.message)
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>Wheel 车轮策略</h2>
        <button className="btn" style={{ fontSize: 13, padding: '5px 12px' }} onClick={loadAll}>刷新</button>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

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
        {([['board', '标的看板'], ['timing', '时机历史'], ['ledger', '台账'], ['targets', `标的设置(${targets.length})`]] as const).map(([k, label]) => (
          <div key={k} onClick={() => setTab(k)} style={{
            padding: '8px 16px', cursor: 'pointer', fontSize: 13,
            borderBottom: tab === k ? '2px solid var(--accent)' : '2px solid transparent',
            color: tab === k ? 'var(--accent)' : 'var(--text-secondary)',
          }}>{label}</div>
        ))}
      </div>

      {/* ── 看板 ── */}
      {tab === 'board' && (
        <div>
          {/* 开仓时机 */}
          <div className="card" style={{ padding: '12px 18px', marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: timingSignals.length ? 8 : 0 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>⏰ 开仓时机</span>
              <button className="btn" style={{ fontSize: 12, padding: '3px 12px' }}
                disabled={timingScanning} onClick={handleTimingScan}>
                {timingScanning ? '扫描中...' : '立即扫描'}
              </button>
              <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                合约价触及自身 EMA50/EMA200(期权价受长期均线压制,触线即卖点)。后台每30分钟自动扫描,触发推 Telegram
              </span>
            </div>
            {timingScanning && (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '6px 0' }}>
                扫描进行中...首次扫描需为每张合约拉取历史K线,可能需要几分钟,请勿重复点击
              </div>
            )}
            {scanStatus && !scanStatus.running && scanStatus.finished_at && (
              <div style={{ margin: '8px 0', padding: '8px 12px', background: 'var(--bg-secondary)', borderRadius: 6, fontSize: 12 }}>
                <div style={{ marginBottom: 6 }}>
                  ✅ 扫描完成({fmtDate(scanStatus.finished_at)}),触发 <b>{scanStatus.signals_found}</b> 条信号
                  {scanStatus.signals_found > 0 && (
                    scanStatus.telegram_configured
                      ? <span style={{ color: '#4ade80' }}> · Telegram 已推送 {scanStatus.telegram_sent} 条</span>
                      : <span style={{ color: '#fb923c' }}> · Telegram 未配置,未推送(前往「设置」页填写 Bot Token 和 Chat ID 并保存,立即生效)</span>
                  )}
                  {scanStatus.error && <span style={{ color: '#f87171' }}> · 异常:{scanStatus.error}</span>}
                </div>
                {scanStatus.report.map((r, i) => (
                  <div key={i} style={{ color: 'var(--text-secondary)', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <b style={{ color: 'var(--text)' }}>{r.symbol} {r.side}</b>
                    {r.note ? (
                      <span>{r.note}</span>
                    ) : (
                      <>
                        <span>现价 {r.spot ?? '--'}</span>
                        <span>合约 {r.contracts ?? 0} 张</span>
                        {(r.signals ?? 0) > 0 && <span style={{ color: '#4ade80' }}>触发 {r.signals}</span>}
                        {(r.not_touching ?? 0) > 0 && <span>未触线 {r.not_touching}</span>}
                        {(r.bars_insufficient ?? 0) > 0 && <span>K线不足 {r.bars_insufficient}</span>}
                        {(r.in_cooldown ?? 0) > 0 && <span>冷却中 {r.in_cooldown}</span>}
                        {(r.no_history ?? 0) > 0 && <span>无历史 {r.no_history}</span>}
                        {(r.iv_filtered ?? 0) > 0 && <span>IV过滤 {r.iv_filtered}</span>}
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
            {timingSignals.length > 0 && (
              <div style={{ display: 'grid', gap: 6 }}>
                {timingSignals.map(sig => (
                  <div key={sig.id} style={{ display: 'flex', gap: 12, fontSize: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{
                      padding: '1px 8px', borderRadius: 4, fontWeight: 700, fontSize: 11,
                      background: sig.signal_level === 'WHEEL_PUT' ? '#38bdf822' : '#fbbf2422',
                      color: sig.signal_level === 'WHEEL_PUT' ? '#38bdf8' : '#fbbf24',
                    }}>{sig.signal_level === 'WHEEL_PUT' ? '卖Put时机' : '卖Call时机'}</span>
                    <b>{sig.symbol}</b>
                    {sig.ema_type === 'EMA200' && <span>🔥</span>}
                    <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{sig.contract_code}</span>
                    <span>
                      合约价 <b>{sig.trigger_price?.toFixed?.(2) ?? sig.trigger_price}</b> 触及 {sig.ema_type}({sig.ema_value?.toFixed?.(2) ?? sig.ema_value})
                    </span>
                    <span style={{ color: 'var(--text-secondary)' }}>IV分位 {sig.iv_rank}</span>
                    <span style={{ color: 'var(--text-secondary)' }}>标的 ${sig.underlying_price}</span>
                    <span style={{ color: 'var(--text-secondary)' }}>{fmtDate(sig.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {targets.filter(t => t.enabled).length === 0 && (
            <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '20px 0' }}>
              还没有启用的 wheel 标的,去「标的设置」添加(候选来自股票池美股/港股)
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
            {targets.filter(t => t.enabled).flatMap(t => {
              const cycles = t.active_cycles || []
              const cards: { cycle: WheelCycle | null; idx: number }[] =
                cycles.length > 0 ? cycles.map((c, i) => ({ cycle: c, idx: i })) : [{ cycle: null, idx: 0 }]
              return cards.map(({ cycle: c, idx }) => {
                const status = c?.status || 'IDLE'
                return (
                  <div key={`${t.symbol}-${c?.id || 'empty'}`} className="card" style={{ padding: '16px 20px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                      <div>
                        <span style={{ fontWeight: 700, fontSize: 16 }}>{t.symbol}</span>
                        {cycles.length > 1 && <span style={{ color: 'var(--accent)', fontSize: 12, marginLeft: 6 }}>轮 #{idx + 1}</span>}
                        <span style={{ color: 'var(--text-secondary)', fontSize: 12, marginLeft: 8 }}>{t.name}</span>
                      </div>
                      <span style={{ color: STAGE_COLORS[status], fontWeight: 700, fontSize: 13 }}>
                        {c ? STAGE_LABELS[status] : '未开轮'}
                      </span>
                    </div>
                    <div style={{ marginBottom: 12 }}><StageIndicator status={status} /></div>

                    {/* 在场合约 */}
                    {c && (c.status === 'CSP_OPEN' || c.status === 'CC_OPEN') && (
                      <div style={{ background: 'var(--bg-secondary)', borderRadius: 6, padding: '8px 12px', fontSize: 12, marginBottom: 10 }}>
                        <div style={{ fontFamily: 'monospace', marginBottom: 4 }}>{c.open_contract_code || `${c.open_option_type} $${c.open_strike}`}</div>
                        <div style={{ display: 'flex', gap: 14, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
                          <span>Strike ${fmt(c.open_strike)}</span>
                          <span>到期 {c.open_expiry}</span>
                          <span style={{ color: (c.open_dte ?? 99) <= 7 ? '#fb923c' : undefined }}>DTE {c.open_dte ?? '--'}</span>
                          <span>开仓 ${fmt(c.open_price)}</span>
                          {openChecks[c.id] && (
                            <>
                              <span>现价 ${fmt(openChecks[c.id].current_price)}</span>
                              <span style={{
                                fontWeight: 700,
                                color: (openChecks[c.id].profit_pct ?? 0) >= profitTarget ? '#4ade80'
                                  : (openChecks[c.id].profit_pct ?? 0) < 0 ? '#f87171' : 'var(--text)',
                              }}>
                                浮盈 {openChecks[c.id].profit_pct ?? '--'}%
                              </span>
                              {openChecks[c.id].itm && <span style={{ color: '#f87171', fontWeight: 700 }}>ITM</span>}
                            </>
                          )}
                        </div>
                      </div>
                    )}

                    {/* 数据行 */}
                    <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12, flexWrap: 'wrap' }}>
                      <span>底线 ${fmt(t.floor_price)}</span>
                      {t.idle_days != null && t.idle_days >= 5 && (
                        <span style={{ color: '#fb923c' }}>⏸ 空转 {t.idle_days} 天(资金闲置)</span>
                      )}
                      {c && c.shares > 0 && <span>持股 {c.shares} @ ${fmt(c.share_cost)}</span>}
                      {c && c.cost_basis != null && <span style={{ color: '#4ade80' }}>Cost Basis ${fmt(c.cost_basis)}</span>}
                      {c && <span>本轮权利金 ${fmt(c.total_premium)}</span>}
                    </div>

                    {/* 操作按钮 */}
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {status === 'IDLE' && (
                        <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }}
                          disabled={suggestLoading} onClick={() => handleSuggest(t.symbol, 'put', c?.id)}>找 Put</button>
                      )}
                      {status === 'HOLDING' && (
                        <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }}
                          disabled={suggestLoading} onClick={() => handleSuggest(t.symbol, 'call', c?.id)}>找 Call</button>
                      )}
                      {c && (c.status === 'CSP_OPEN' || c.status === 'CC_OPEN') && openChecks[c.id]?.profit_hit && (
                        <button className="btn" style={{ fontSize: 12, padding: '4px 12px', color: '#4ade80', fontWeight: 700 }}
                          onClick={() => setTradeModal({
                            initial: {
                              symbol: t.symbol,
                              trade_type: c.status === 'CSP_OPEN' ? 'BUY_PUT_CLOSE' : 'BUY_CALL_CLOSE',
                              price: String(openChecks[c.id].buyback_ask || ''),
                              qty: String(c.open_qty || 1),
                              contract_size: String(c.open_contract_size || 100),
                            },
                            status, cycleId: c.id,
                          })}>
                          💰 平仓锁定
                        </button>
                      )}
                      {c && (c.status === 'CSP_OPEN' || c.status === 'CC_OPEN') && (
                        <button className="btn" style={{ fontSize: 12, padding: '4px 12px' }}
                          disabled={rollLoading} onClick={() => handleRoll(c.id)}>看 Roll</button>
                      )}
                      <button className="btn" style={{ fontSize: 12, padding: '4px 12px' }}
                        onClick={() => setTradeModal({ initial: { symbol: t.symbol }, status, cycleId: c?.id })}>
                        登记交易
                      </button>
                      {idx === cards.length - 1 && c && (
                        <button className="btn" style={{ fontSize: 12, padding: '4px 12px', color: 'var(--accent)' }}
                          onClick={() => setTradeModal({
                            initial: { symbol: t.symbol, trade_type: 'SELL_PUT' },
                            status: 'NONE', newCycle: true,
                          })}>
                          + 新开轮子
                        </button>
                      )}
                    </div>
                  </div>
                )
              })
            })}
          </div>

          {/* 建议面板 */}
          {suggestLoading && <div style={{ marginTop: 20, color: 'var(--text-secondary)', fontSize: 13 }}>正在拉取期权链并筛选(需富途 OpenD)...</div>}
          {suggest && (
            <div className="card" style={{ marginTop: 20, padding: '16px 20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <h3 style={{ margin: 0, fontSize: 15 }}>
                  {suggest.symbol} 卖 {suggest.side} 建议
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)', marginLeft: 10 }}>
                    现价 ${fmt(suggest.spot_price)}{suggest.cost_basis != null ? ` · Cost Basis $${fmt(suggest.cost_basis)}` : ''}
                  </span>
                </h3>
                <button className="btn" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => setSuggest(null)}>关闭</button>
              </div>
              {suggest.volatility && (
                <div style={{ padding: '8px 12px', background: 'var(--bg-secondary)', borderRadius: 6, marginBottom: 10 }}>
                  <VolatilityBar v={suggest.volatility} />
                </div>
              )}
              {(suggest.earnings_warn || suggest.delta_preference) && (
                <div style={{ display: 'flex', gap: 16, fontSize: 12, marginBottom: 10, flexWrap: 'wrap' }}>
                  {suggest.earnings_warn && (
                    <span style={{ color: '#fb923c' }}>
                      ⚠ 财报 {suggest.earnings_date}(距今 {suggest.days_to_earnings} 天),标"含财报"的合约到期前将经历财报,权利金高但风险大
                    </span>
                  )}
                  {suggest.delta_preference && <span style={{ color: '#38bdf8' }}>ℹ {suggest.delta_preference}</span>}
                </div>
              )}
              {suggest.suggestions.length === 0 ? (
                <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '12px 0' }}>
                  {suggest.message || '没有符合筛选条件的合约(delta/DTE/年化/流动性),可在「标的设置」放宽参数'}
                </div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      {['合约编号', 'Strike', 'Delta', 'DTE', 'Bid', '年化%(现金)', ...(suggest.side === 'PUT' ? ['年化%(保证金)'] : []), '虚值%', 'OI', suggest.side === 'PUT' ? '接货成本' : '若被行权赚', ''].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500 }}>{h}</th>
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
                        <td style={{ padding: '7px 10px' }}>{s.dte}</td>
                        <td style={{ padding: '7px 10px' }}>${fmt(s.bid)}</td>
                        <td style={{ padding: '7px 10px', color: '#4ade80', fontWeight: 700 }}>{fmt(s.annualized, 1)}</td>
                        {suggest.side === 'PUT' && (
                          <td style={{ padding: '7px 10px', color: '#38bdf8' }}>{s.annualized_margin != null ? fmt(s.annualized_margin, 1) : '--'}</td>
                        )}
                        <td style={{ padding: '7px 10px' }}>{fmt(s.otm_pct, 1)}</td>
                        <td style={{ padding: '7px 10px' }}>{s.open_interest}</td>
                        <td style={{ padding: '7px 10px' }}>
                          {suggest.side === 'PUT' ? `$${fmt(s.assigned_cost)}` : `$${fmt(s.if_called_total)}`}
                        </td>
                        <td style={{ padding: '7px 10px' }}>
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: '2px 10px' }}
                            onClick={() => setTradeModal({
                              initial: {
                                symbol: suggest.symbol,
                                trade_type: suggest.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
                                contract_code: s.contract_code,
                                strike: String(s.strike),
                                expiry: s.expiry,
                                price: String(s.bid),
                                contract_size: String(s.contract_size),
                              },
                              status: suggest.side === 'PUT' ? 'IDLE' : 'HOLDING',
                              cycleId: suggestCycleId,
                            })}>
                            已下单,登记
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8 }}>
                筛选条件来自标的设置;年化 = 权利金/占用资金×365/DTE;先在富途下单成交后,回来点「已下单,登记」填实际成交价
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 时机历史 ── */}
      {tab === 'timing' && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
            扫描发现的全部开仓时机,按合约代码去重合并(同一合约再次触发只更新数据并累计次数),按最近发现时间倒序
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['最近发现', '方向', '标的', '合约', 'Strike', '到期', '触发价', '触及均线', 'IV分位', '标的价', '次数', '首次发现', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(!timingHistory || timingHistory.items.length === 0) && (
                <tr><td colSpan={13} style={{ padding: '20px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无历史时机,去看板点「立即扫描」</td></tr>
              )}
              {timingHistory?.items.map(item => (
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
                  <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{item.expiry || '--'}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(item.trigger_price)}</td>
                  <td style={{ padding: '7px 10px' }}>
                    {item.ema_type === 'EMA200' ? '🔥' : ''}{item.ema_type}({fmt(item.ema_value)})
                  </td>
                  <td style={{ padding: '7px 10px' }}>{item.iv_rank != null ? fmt(item.iv_rank, 0) : '--'}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(item.underlying_price)}</td>
                  <td style={{ padding: '7px 10px', textAlign: 'center' }}>{item.times_triggered}</td>
                  <td style={{ padding: '7px 10px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{fmtDate(item.first_seen)}</td>
                  <td style={{ padding: '7px 10px' }}>
                    <button className="btn btn-primary" style={{ fontSize: 11, padding: '2px 10px' }}
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

      {/* ── 台账 ── */}
      {tab === 'ledger' && (
        <div>
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
                  <td style={{ padding: '7px 10px' }}>{TRADE_LABELS[t.trade_type]}</td>
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
                {['标的', '底线价', 'Delta 区间', 'DTE 区间', '最低年化%', '最低OI', '状态', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {targets.length === 0 && <tr><td colSpan={8} style={{ padding: '20px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无标的,从上方添加</td></tr>}
              {targets.map(t => (
                <TargetRow key={t.symbol} target={t} onSaved={loadAll}
                  onToggle={() => handleToggleTarget(t)} onDelete={() => handleDeleteTarget(t.symbol)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tradeModal && (
        <TradeModal initial={tradeModal.initial} cycleStatus={tradeModal.status}
          cycleId={tradeModal.cycleId} newCycle={tradeModal.newCycle}
          onClose={() => setTradeModal(null)} onSaved={() => { setSuggest(null); loadAll() }} />
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

// ── Roll 对比弹窗 ─────────────────────────────────────────────────────────────
function RollModal({ data, onClose, onSaved }: {
  data: WheelRollOptions
  onClose: () => void
  onSaved: () => void
}) {
  const [selected, setSelected] = useState<string | null>(data.candidates[0]?.contract_code || null)
  const [buyback, setBuyback] = useState(String(data.current.buyback_ask || ''))
  const [newPrice, setNewPrice] = useState(String(data.candidates[0]?.bid ?? ''))
  const [fee, setFee] = useState('0')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const cand = data.candidates.find(c => c.contract_code === selected)
  const size = data.current.contract_size || 100
  const netCredit = cand && buyback && newPrice
    ? ((parseFloat(newPrice) - parseFloat(buyback)) * size).toFixed(0) : null

  const inputStyle = {
    width: 90, padding: '4px 6px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 12,
  } as const

  async function submitRoll() {
    if (!cand) { setErr('请选择新合约'); return }
    const bb = parseFloat(buyback); const np = parseFloat(newPrice); const f = parseFloat(fee) || 0
    if (isNaN(bb) || bb < 0 || isNaN(np) || np <= 0) { setErr('请填写有效价格'); return }
    setSaving(true)
    setErr(null)
    try {
      // 两腿:买回旧 + 卖出新,同一 cycle
      await recordWheelTrade({
        symbol: data.symbol,
        trade_type: data.side === 'PUT' ? 'BUY_PUT_CLOSE' : 'BUY_CALL_CLOSE',
        contract_code: data.current.contract_code,
        price: bb, fee: f, contract_size: size,
        cycle_id: data.cycle_id, note: 'Roll 买回',
      })
      await recordWheelTrade({
        symbol: data.symbol,
        trade_type: data.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
        contract_code: cand.contract_code, strike: cand.strike, expiry: cand.expiry,
        price: np, fee: f, contract_size: size,
        cycle_id: data.cycle_id, note: 'Roll 卖出',
      })
      onSaved()
      onClose()
    } catch (e: any) {
      setErr('Roll 登记失败(若买回已成功,请在台账检查后手动登记卖出腿):' + e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: '#0009', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div className="modal-card" style={{ width: 640, maxWidth: '100%', maxHeight: '85vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 6px', fontSize: 16 }}>Roll 对比 — {data.symbol} {data.side}</h3>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
          当前 ${fmt(data.current.strike)} {data.current.expiry}(DTE {data.current.dte ?? '--'}) ·
          开仓 ${fmt(data.current.open_price)} · 买回约 ${fmt(data.current.buyback_ask)} · δ{data.current.delta}
        </div>
        {err && <div className="alert alert-error" style={{ marginBottom: 10 }}>{err}</div>}
        {(data.warnings?.length ?? 0) > 0 && (
          <div style={{ marginBottom: 10, padding: '6px 10px', background: '#fb923c11', border: '1px solid #fb923c55', borderRadius: 6, fontSize: 12, color: '#fb923c' }}>
            {data.warnings!.map((w, i) => <div key={i}>⚠ {w}</div>)}
          </div>
        )}

        {data.candidates.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '10px 0' }}>
            没有找到相近 delta 的下期合约(可先手动平仓,再用助手找新合约)
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginBottom: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['', '合约编号', 'Strike', '到期', 'DTE', 'δ', 'Bid', '净收权利金/张', '新仓年化%'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '5px 8px' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.candidates.map(c => (
                <tr key={c.contract_code} style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                  onClick={() => { setSelected(c.contract_code); setNewPrice(String(c.bid)) }}>
                  <td style={{ padding: '6px 8px' }}>
                    <input type="radio" checked={selected === c.contract_code} readOnly />
                  </td>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)' }}>{c.contract_code}</td>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>${fmt(c.strike)}</td>
                  <td style={{ padding: '6px 8px' }}>{c.expiry}</td>
                  <td style={{ padding: '6px 8px' }}>{c.dte}</td>
                  <td style={{ padding: '6px 8px' }}>{c.delta}</td>
                  <td style={{ padding: '6px 8px' }}>${fmt(c.bid)}</td>
                  <td style={{ padding: '6px 8px', fontWeight: 700, color: c.net_credit_per_contract >= 0 ? '#4ade80' : '#f87171' }}>
                    {c.net_credit_per_contract >= 0 ? '+' : ''}{fmt(c.net_credit_per_contract, 0)}
                  </td>
                  <td style={{ padding: '6px 8px' }}>{c.annualized != null ? fmt(c.annualized, 1) : '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, marginBottom: 14 }}>
          <label>买回价 <input type="number" style={inputStyle} value={buyback} onChange={e => setBuyback(e.target.value)} /></label>
          <label>新卖价 <input type="number" style={inputStyle} value={newPrice} onChange={e => setNewPrice(e.target.value)} /></label>
          <label>手续费/腿 <input type="number" style={inputStyle} value={fee} onChange={e => setFee(e.target.value)} /></label>
          {netCredit != null && (
            <span>本次 Roll 净{parseFloat(netCredit) >= 0 ? '收' : '付'} <b style={{ color: parseFloat(netCredit) >= 0 ? '#4ade80' : '#f87171' }}>${netCredit}</b>/张</span>
          )}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
          在富途完成两笔交易后,按实际成交价修改上方数值再点登记;将在同一轮内记两条腿(买回+卖出)
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
              到期日(YYYY-MM-DD)
              <input value={form.expiry} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              交易时间
              <input value={form.traded_at} style={inputStyle}
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
