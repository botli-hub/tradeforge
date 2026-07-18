import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getAppSettings, subscribeSettings, type AppSettings,
  getWheelTargets, getWheelCandidates, addWheelTarget, updateWheelTarget, deleteWheelTarget,
  getWheelFloorSuggest,
  getWheelCycles, getWheelTrades, recordWheelTrade, updateWheelTrade, deleteWheelTrade,
  getWheelStats, getWheelSuggest, triggerWheelTimingScan, getWheelTimingSignals,
  getWheelScanStatus, getWheelTimingHistory, checkWheelOpenPositions, getWheelRollOptions,
  getWheelPoolScan, pushWheelPoolScan, WheelScanResult, WheelScanOpportunity, getBackendConfig,
  getWheelOpportunities, type WheelOpportunitiesResult, type WheelOpportunity,
  getWheelScanProgress, getWheelContractQuote, type WheelScanProgress,
  getWheelToday, executeWheelDraft, type WheelTodayBoard,
  WheelTarget, WheelCycle, WheelTrade, WheelStats, WheelTradeType,
  WheelSuggestResponse, LeapsCandidate, LeapsSignal, VolatilityProfile,
  WheelScanStatus, WheelTimingHistoryPage, WheelTimingHistoryItem, WheelOpenPositionItem, WheelRollOptions,
  type WheelPortfolioContext,
} from '../services/api'
import WheelOptimizePanel from '../components/WheelOptimizePanel'
import {
  addPendingReg, annPerDelta, buildFutuOrderMemo, computeOpsMetrics, contractCodeWarning, copyText,
  dailyRentPer10k, dteBucket, DTE_BUCKET_META, estimateAnnualized, evaluateTradeability, explainOpenOpp,
  fmtRelativeTime, getPortfolioBudget, getPortfolioBudgetSource, getRiskTier, isOnboardDone, loadPendingQueue,
  normalizeContractCode, removePendingReg, resolveOppSellPrice, resolveTradeTier, savePendingQueue, scanFailureHint,
  setOnboardDone, setRiskTier, STRATEGY_TEMPLATES, stressBlocksNewPuts, suggestQty,
  subscribePortfolioBudget, syncPortfolioBudgetFromConfig,
  type DteBucket, type PendingRegItem, type RiskTier, type TradeTier,
} from '../services/wheelProduct'
import Drawer from '../components/ui/Drawer'
import EmptyState from '../components/ui/EmptyState'
import {
  Badge, Stat, StatusDot, TargetPriceStrip, RiskMarks, C,
  type SemColor,
} from '../components/wheel/WheelUi'
import ManageDecisionModal from '../components/wheel/ManageDecisionModal'
import { useToast } from '../components/ui/Toast'

/** 离散选项下拉：当前值不在列表里时自动补上，避免丢历史数据 */
/** 高分/触线统一进度文案：标的 方向 · 到期日 · 合约 n/m · 标的 i/N */
function ScanProgressDetail({
  prefix = '正在扫描：',
  symbol,
  side,
  expiry,
  contract_i,
  contract_n,
  target_i,
  target_n,
  fallback,
}: {
  prefix?: string
  symbol?: string | null
  side?: string | null
  expiry?: string | null
  contract_i?: number | null
  contract_n?: number | null
  target_i?: number | null
  target_n?: number | null
  fallback?: string
}) {
  if (!symbol) {
    return <>{fallback || '准备扫描…'}</>
  }
  const expText = expiry ? String(expiry).slice(0, 10) : '…'
  const hasCn = contract_n != null && contract_n > 0
  return (
    <>
      {prefix}
      <b style={{ color: 'var(--text)' }}>{symbol}</b>
      {side ? ` ${side}` : ''}
      {' · 到期日 '}
      <b style={{ color: 'var(--text)' }}>{expText}</b>
      {' · 合约 '}
      <b style={{ color: 'var(--green)' }}>
        {hasCn ? `${contract_i || 0}/${contract_n}` : '…'}
      </b>
      {(target_n ?? 0) > 0 && (
        <span style={{ color: 'var(--text-tertiary)', marginLeft: 8 }}>
          标的 {target_i || 0}/{target_n}
        </span>
      )}
    </>
  )
}

function SelectNum({
  value, onChange, options, style, emptyLabel,
}: {
  value: string | number
  onChange: (v: string) => void
  options: number[]
  style?: React.CSSProperties
  emptyLabel?: string
}) {
  const str = value === '' || value == null ? '' : String(value)
  const num = str === '' ? NaN : Number(str)
  const opts = [...options]
  if (str !== '' && !Number.isNaN(num) && !opts.some(o => o === num || String(o) === str)) {
    opts.push(num)
    opts.sort((a, b) => a - b)
  }
  return (
    <select value={str} onChange={e => onChange(e.target.value)} style={style}>
      {emptyLabel != null && <option value="">{emptyLabel}</option>}
      {opts.map(o => (
        <option key={o} value={String(o)}>{o}</option>
      ))}
    </select>
  )
}

const QTY_CONTRACT_OPTS = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20]
const QTY_SHARE_OPTS = [100, 200, 300, 400, 500, 1000]
const FEE_OPTS = [0, 0.65, 1, 1.5, 2, 3, 5, 10]
const CONTRACT_SIZE_OPTS = [100]

const DELTA_OPTS = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4]
const DTE_OPTS = [7, 14, 21, 30, 35, 45, 60, 90]
const ANN_OPTS = [8, 10, 12, 15, 18, 20, 25, 30, 40]
const OI_OPTS = [10, 50, 100, 200, 500, 1000]

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

// Badge / Stat / TargetPriceStrip / C → components/wheel/WheelUi (U5/U6)

// ── 机会扫描 M1:可交易性/三档/DTE桶/日租/事件 ──────────────────────────────
type OppCategory =
  | 'ACTIONABLE' | 'RANKED' | 'EMA_TOUCH'
  | 'CLOSE' | 'ROLL' | 'LOW_YIELD' | 'UNCOVERED' | 'IDLE'
type OppCategoryFilter =
  | 'all' | 'PRIORITY' | 'QUEUE' | 'WATCH' | 'KILLED' | 'ACTIONABLE' | 'RANKED' | 'EMA_TOUCH'
  | 'MANAGE' | 'CLOSE' | 'ROLL' | 'UNCOVERED' | 'IDLE'
type OppSideFilter = 'all' | 'PUT' | 'CALL'
type OppBucketFilter = 'core_extend' | 'core' | 'all_buckets' | 'far'
/** legacy strength 仍用于 Badge 色;真正档位看 trade_tier */
type OppStrength = 'WATCH' | 'READY' | 'STRONG' | 'MANAGE'

type OppRow = {
  id: string
  kind: 'OPEN' | 'MANAGE'
  categories: OppCategory[]
  strength: OppStrength
  /** M1 双轨确认档:观察/可排单/优先 */
  trade_tier: TradeTier
  tradeable: boolean
  kill_reasons: string[]
  dte_bucket: DteBucket
  /** 日租 $/万保/天 — 同桶排序主键 */
  daily_rent: number | null
  /** 年化/|Δ| */
  ann_per_delta: number | null
  covers_earnings: boolean
  open_interest?: number | null
  actionable: boolean
  symbol: string
  side?: 'PUT' | 'CALL'
  contract_code?: string
  strike?: number | null
  expiry?: string | null
  dte?: number | null
  score?: number | null
  annualized?: number | null
  remaining_annualized?: number | null
  delta?: number | null
  bid?: number | null
  spread_pct?: number | null
  tags: string[]
  risk_hard: string[]
  risk_soft: string[]
  risk_block: boolean
  efficiency: number
  capital?: number | null
  cycle_id?: string | null
  ranked?: WheelScanOpportunity
  ema_type?: string | null
  ema_value?: number | null
  trigger_price?: number | null
  iv_rank?: number | null
  underlying_price?: number | null
  times_triggered?: number
  profit_pct?: number | null
  action_hint?: string | null
  /** 后端决策码:一 cycle 一主建议 */
  action_code?: string | null
  action_priority?: number
  prefer_card?: string | null
  decision_why?: string[]
  /** 同标的同方向主推 */
  is_top_pick?: boolean
  signal?: LeapsSignal
  history?: WheelTimingHistoryItem
  check?: WheelOpenPositionItem
  seen_at?: string | null
  headline?: string
  suggest_qty?: number
}

function oppKey(code: string | null | undefined, symbol: string, side: string, strike?: number | null, expiry?: string | null) {
  if (code) return code
  return `${symbol}|${side}|${strike ?? ''}|${(expiry || '').slice(0, 10)}`
}

function rankedTags(o: WheelScanOpportunity): string[] {
  const tags: string[] = []
  if (o.trend === 'DOWN') tags.push('趋势弱')
  if (o.trend === 'WEAK') tags.push('↓EMA50')
  if (o.covers_earnings) tags.push('含财报')
  if (o.exceeds_capital) tags.push('超上限')
  if ((o.iv_rank ?? 0) >= 70) tags.push('IV高')
  return tags
}

function capitalForOpp(side: 'PUT' | 'CALL' | undefined, strike?: number | null, size = 100): number | null {
  if (strike == null || !side) return null
  return strike * size
}

/** 换仓/平仓后候选下一腿:用已有机会缓存,不重扫 */
function pickReplaceCandidates(
  opps: WheelOpportunitiesResult | null | undefined,
  mc: Pick<WheelOpenPositionItem, 'symbol' | 'side' | 'freed_capital_est' | 'portfolio_put_blocked'>,
  limit = 2,
): { opp: WheelOpportunity; putBlocked: boolean; capitalEst: number | null }[] {
  if (!opps) return []
  const putBlocked = !!(mc.portfolio_put_blocked || opps.summary?.portfolio_put_blocked || opps.portfolio?.portfolio_put_blocked)
  const pool: WheelOpportunity[] = []
  for (const list of [opps.primary_picks, opps.actionable_items, opps.items]) {
    if (!list) continue
    for (const o of list) {
      if (o.actionable || o.grade === 'dual' || o.grade === 'timing' || o.grade === 'score') pool.push(o)
    }
  }
  const seen = new Set<string>()
  const out: { opp: WheelOpportunity; putBlocked: boolean; capitalEst: number | null }[] = []
  const freed = mc.freed_capital_est
  for (const o of pool) {
    const key = o.contract_code || `${o.symbol}|${o.side}|${o.strike}|${o.expiry}`
    if (seen.has(key)) continue
    seen.add(key)
    if (o.symbol === mc.symbol && String(o.side) === String(mc.side)) continue
    const capitalEst = capitalForOpp(o.side as 'PUT' | 'CALL', o.strike ?? null)
    if (freed != null && freed > 0 && capitalEst != null && capitalEst > freed * 1.1) continue
    const blocked = putBlocked && o.side === 'PUT'
    out.push({ opp: o, putBlocked: blocked, capitalEst })
    if (out.length >= limit) break
  }
  return out
}

/** 优先度 ≈ 年化×流动性 / 资金占用 × 触线加成 × IV加成 × 风控折扣 */
function computeEfficiency(row: Pick<OppRow, 'annualized' | 'ranked' | 'categories' | 'ema_type' | 'iv_rank' | 'risk_block' | 'risk_soft' | 'strike' | 'side' | 'bid'>): number {
  const ann = row.annualized ?? 0
  const liq = row.ranked?.score_factors?.liquidity ?? (row.categories.includes('EMA_TOUCH') ? 0.8 : 1)
  const cap = capitalForOpp(row.side, row.strike) || 1
  let mult = 1
  if (row.categories.includes('EMA_TOUCH')) mult *= row.ema_type === 'EMA200' ? 1.3 : 1.15
  if ((row.iv_rank ?? 0) >= 70) mult *= 1.15
  else if ((row.iv_rank ?? 0) >= 50) mult *= 1.08
  if (row.risk_block) mult *= 0.25
  else if (row.risk_soft.length) mult *= 0.75
  // 归一:年化% / 万美元占用
  return (ann * liq / (cap / 10000)) * mult
}

function enrichOpenRow(
  row: OppRow,
  targetsBySym: Record<string, WheelTarget>,
  stressHigh: boolean,
  portfolioAvailable: number,
): OppRow {
  const t = targetsBySym[row.symbol]
  const hard: string[] = []
  const soft: string[] = [...row.risk_soft]

  row.covers_earnings = !!(row.ranked?.covers_earnings || row.ranked?.earnings_warn || row.tags.includes('含财报'))
  row.dte_bucket = dteBucket(row.dte)
  // 年化/日租只用真实 bid；触线价不参与（深 ITM last 会严重失真）
  const px = resolveOppSellPrice({ bid: row.bid, trigger_price: row.trigger_price })
  const prem = px.sell
  if ((row.annualized == null || row.annualized <= 0) && prem != null) {
    const est = estimateAnnualized(prem, row.strike, row.dte)
    if (est != null) row.annualized = est
  }
  row.daily_rent = dailyRentPer10k(prem, row.strike, row.dte)
  row.ann_per_delta = annPerDelta(row.annualized, row.delta)
  row.open_interest = row.ranked?.open_interest ?? row.open_interest ?? null
  row.capital = capitalForOpp(row.side, row.strike)

  // 建议张数(额度)
  const used = t ? targetCapital(t.active_cycles || []) : 0
  const headroom = t && (t.max_capital ?? 0) > 0 ? Math.max(0, t.max_capital - used) : null
  row.suggest_qty = row.strike != null
    ? suggestQty({
      strike: row.strike, side: row.side || 'PUT',
      symbolHeadroom: headroom, portfolioAvailable,
    })
    : 1

  // 可交易性门槛
  const gate = evaluateTradeability({
    bid: row.bid,
    spread_pct: row.spread_pct,
    open_interest: row.open_interest,
    min_oi: t?.min_open_interest ?? 100,
    max_spread_pct: 10,
    suggest_qty: row.side === 'PUT' ? row.suggest_qty : 1,
    side: row.side,
    strike: row.strike,
    exceeds_capital: !!(row.ranked?.exceeds_capital || row.tags.includes('超上限')),
  })
  row.tradeable = gate.ok
  row.kill_reasons = gate.reasons

  if (row.ranked?.exceeds_capital || row.tags.includes('超上限')) hard.push('超资金上限')
  if (row.side === 'PUT' && t && row.strike != null && t.floor_price > 0 && row.strike > t.floor_price) {
    soft.push('超过愿接价')
  }
  if (row.side === 'PUT' && row.signal && (row.signal as any).below_floor) soft.push('已入愿接区·指派风险升')
  if (row.side === 'PUT' && t && row.strike != null && t.floor_price > 0 && row.strike <= t.floor_price) {
    const cushion = t.floor_price - row.strike
    if (cushion >= 0) soft.push(`愿接余量$${cushion.toFixed(cushion < 1 ? 2 : 0)}`)
  }
  if (row.side === 'CALL' && t) {
    const holding = (t.active_cycles || []).find(c => c.status === 'HOLDING' || c.status === 'CC_OPEN')
    const cb = holding?.cost_basis
    if (cb != null && row.strike != null && row.strike < cb * 1.02) soft.push('Call接近成本底线(非floor)')
  }
  if (row.covers_earnings) soft.push('含财报')
  if (row.ranked?.trend === 'DOWN' || row.tags.includes('趋势弱')) soft.push('趋势弱')
  if (row.side === 'PUT' && stressHigh) hard.push('组合行权压力高')
  if (row.dte_bucket === 'far') soft.push('远月假高年化风险')
  if (!gate.ok) hard.push(...gate.reasons.map(r => `门槛:${r}`))

  // 同标的拥挤:已有 CSP 时新 Put 软降权
  if (row.side === 'PUT' && t) {
    const cspN = (t.active_cycles || []).filter(c => c.status === 'CSP_OPEN').length
    if (cspN >= 1) soft.push(`同标的已有${cspN}轮CSP`)
  }

  row.risk_hard = [...new Set([...row.risk_hard, ...hard])]
  row.risk_soft = [...new Set(soft)]
  row.risk_block = row.risk_hard.some(h => !h.startsWith('门槛:')) // 纯门槛不阻断展示,只降档
  // 不可交易或硬风控 → 不可优先
  const blocked = row.risk_block || !row.tradeable

  row.efficiency = computeEfficiency(row)
  // 排序分:同桶内日租为主,兼效率
  if (row.daily_rent != null) {
    row.efficiency = row.daily_rent * 10 + (row.ann_per_delta ?? 0) * 0.1
  }

  const hasRanked = row.categories.includes('RANKED')
  const hasTouch = row.categories.includes('EMA_TOUCH')
  const ivOk = (row.iv_rank ?? 0) >= 50
  const strongTouch = row.ema_type === 'EMA200'

  row.trade_tier = resolveTradeTier({
    kind: 'OPEN',
    hasRanked,
    hasTouch,
    tradeable: row.tradeable && !row.risk_block,
    risk_block: blocked && row.risk_block,
    ema_type: row.ema_type,
    iv_rank: row.iv_rank,
    covers_earnings: row.covers_earnings,
    demote_earnings: true,
  })
  // 不可交易强制观察
  if (!row.tradeable || row.risk_block) row.trade_tier = 'WATCH'

  if (row.trade_tier === 'PRIORITY') row.strength = strongTouch && ivOk ? 'STRONG' : 'READY'
  else if (row.trade_tier === 'QUEUE') row.strength = 'READY'
  else row.strength = 'WATCH'

  row.actionable = row.trade_tier === 'PRIORITY'
  if (row.trade_tier === 'PRIORITY' && !row.categories.includes('ACTIONABLE')) {
    row.categories = ['ACTIONABLE', ...row.categories]
  }

  return row
}

function sortOppRows(rows: OppRow[]): OppRow[] {
  return [...rows].sort((a, b) => {
    // 管理类置顶:决策树 priority 越小越急
    if (a.kind !== b.kind) return a.kind === 'MANAGE' ? -1 : 1
    if (a.kind === 'MANAGE') {
      const pa = a.action_priority ?? 9
      const pb = b.action_priority ?? 9
      if (pa !== pb) return pa - pb
      return b.efficiency - a.efficiency
    }
    // 开仓:档位优先 > 可排单 > 观察
    const tierOrder = { PRIORITY: 0, QUEUE: 1, WATCH: 2, MANAGE: 0 }
    const ta = tierOrder[a.trade_tier] ?? 3
    const tb = tierOrder[b.trade_tier] ?? 3
    if (ta !== tb) return ta - tb
    // 同档:DTE 桶(核心优先)
    const ba = DTE_BUCKET_META[a.dte_bucket]?.order ?? 9
    const bb = DTE_BUCKET_META[b.dte_bucket]?.order ?? 9
    if (ba !== bb) return ba - bb
    // 同桶:日租 desc → 年化/Δ desc → 评分
    const ra = a.daily_rent ?? -1
    const rb = b.daily_rent ?? -1
    if (rb !== ra) return rb - ra
    const da = a.ann_per_delta ?? -1
    const db = b.ann_per_delta ?? -1
    if (db !== da) return db - da
    return (b.score ?? 0) - (a.score ?? 0)
  })
}

function emptyOpenMetrics() {
  return {
    trade_tier: 'WATCH' as TradeTier,
    tradeable: false,
    kill_reasons: [] as string[],
    dte_bucket: 'unknown' as DteBucket,
    daily_rent: null as number | null,
    ann_per_delta: null as number | null,
    covers_earnings: false,
  }
}

/** 后端 /opportunities → 前端 OppRow（开仓档） */
function serverOppToRow(o: WheelOpportunity): OppRow {
  const side = (o.side === 'CALL' ? 'CALL' : 'PUT') as 'PUT' | 'CALL'
  const categories: OppCategory[] = []
  if (o.source === 'dual' || o.grade === 'dual') {
    categories.push('RANKED', 'EMA_TOUCH', 'ACTIONABLE')
  } else if (o.source === 'score' || o.grade === 'score') {
    categories.push('RANKED')
  } else if (o.source === 'timing' || o.grade === 'timing') {
    categories.push('EMA_TOUCH')
  } else {
    categories.push('RANKED')
  }

  const tags: string[] = [...(o.flags || [])]
  if (o.covers_earnings) tags.push('含财报')
  if (o.exceeds_capital) tags.push('超上限')
  if (o.trend === 'DOWN') tags.push('趋势弱')
  if (o.timing?.ema_type === 'EMA200') tags.push('EMA200')

  // 卖出参考：只用 bid / premium_used；触线价(trigger)是 K 线 last/high，深 ITM 会像 89 这种不能当卖价
  const px = resolveOppSellPrice({
    bid: o.bid,
    premium_used: o.premium_used,
    trigger_price: o.timing?.trigger_price,
  })
  const prem = px.sell
  let annualized = o.annualized ?? null
  // 仅有真实权利金时才粗算年化；触线价不算
  if ((annualized == null || annualized <= 0) && prem != null) {
    annualized = estimateAnnualized(prem, o.strike, o.dte)
  }
  // 深 ITM 启发式：权利金 > 0.25×strike 对 Wheel 卖 Put 通常不合理
  const deepItmPrem = prem != null && o.strike != null && o.strike > 0 && prem / o.strike > 0.25

  // 后端 actionable 优先；dual/强触线 → 优先；可做 timing/score → 可排单
  let trade_tier: TradeTier = 'WATCH'
  if (o.grade === 'blocked') trade_tier = 'WATCH'
  else if (o.actionable && (o.grade === 'dual' || o.source === 'dual' || o.timing?.strength === 'STRONG')) {
    trade_tier = 'PRIORITY'
  } else if (o.actionable) {
    trade_tier = 'QUEUE'
  } else if (o.grade === 'watch' || o.timing || o.score != null) {
    trade_tier = 'WATCH'
  }

  const kill: string[] = []
  if (o.grade === 'blocked') {
    kill.push(...(o.flags || ['硬风控阻断']))
  }
  if (px.kind === 'none' || px.kind === 'trigger') kill.push('无买价')
  if (deepItmPrem) kill.push('权利金疑似深ITM')
  // 软标签不进 kill(已入愿接区等),避免整条被「隐藏不可交易」吃掉
  if (!o.actionable && o.grade !== 'blocked' && kill.length === 0) {
    // 可观察但未达可做:不记入 kill,仍可在「观察」展示
  } else if (!o.actionable && o.grade === 'blocked') {
    /* already in kill */
  }

  // 可交易:可做档必须有真实买价;观察档只要有买价也可备忘/展示(不算 killed)
  const hasLiveBid = px.kind === 'bid' || px.kind === 'premium'
  let tradeable = false
  if (o.grade === 'blocked' || deepItmPrem) {
    tradeable = false
    trade_tier = 'WATCH'
  } else if (o.actionable && hasLiveBid) {
    tradeable = true
  } else if (o.timing && hasLiveBid) {
    // 触线+买价:至少可观察/复制备忘
    tradeable = true
    if (!o.actionable) trade_tier = 'WATCH'
  } else if (!hasLiveBid) {
    tradeable = false
    trade_tier = 'WATCH'
  }
  if (!tradeable && kill.length === 0 && !hasLiveBid) {
    kill.push('无买价')
  }

  const row: OppRow = {
    id: o.id || oppKey(o.contract_code, o.symbol, side, o.strike, o.expiry),
    kind: 'OPEN',
    categories,
    strength: trade_tier === 'PRIORITY'
      ? (o.timing?.strength === 'STRONG' ? 'STRONG' : 'READY')
      : trade_tier === 'QUEUE' ? 'READY' : 'WATCH',
    trade_tier,
    tradeable,
    kill_reasons: [...new Set(kill)],
    dte_bucket: dteBucket(o.dte),
    daily_rent: dailyRentPer10k(prem, o.strike, o.dte),
    ann_per_delta: annPerDelta(annualized, o.delta),
    covers_earnings: !!o.covers_earnings,
    open_interest: null,
    actionable: trade_tier === 'PRIORITY',
    symbol: o.symbol,
    side,
    contract_code: o.contract_code || o.contract_short || undefined,
    strike: o.strike,
    expiry: o.expiry,
    dte: o.dte,
    score: o.score,
    annualized,
    delta: o.delta,
    // 只填真实 bid/premium；触线价走 trigger_price，避免备忘写成「参考价 89」
    bid: px.kind === 'bid' || px.kind === 'premium' ? px.sell : null,
    spread_pct: o.spread_pct,
    tags,
    risk_hard: o.exceeds_capital ? ['超资金上限'] : [],
    risk_soft: [
      ...(o.flags || []).filter(f => f !== '超资金上限'),
      ...(deepItmPrem ? ['权利金疑似深ITM'] : []),
      ...(px.kind === 'trigger' || px.kind === 'none' ? ['无实时买价'] : []),
    ],
    risk_block: o.grade === 'blocked' || !!o.exceeds_capital,
    efficiency: 0,
    capital: o.context?.committed,
    cycle_id: o.cycle_id,
    ranked: undefined,
    ema_type: o.timing?.ema_type,
    ema_value: o.timing?.ema_value,
    trigger_price: o.timing?.trigger_price,
    iv_rank: o.iv_rank,
    times_triggered: o.timing?.times_triggered,
    seen_at: o.event_at || o.timing?.last_seen || null,
    headline: o.grade === 'dual' ? '双满足' : o.actionable ? '可做' : undefined,
    is_top_pick: !!o.is_top_pick,
  }
  if (row.daily_rent != null) {
    row.efficiency = row.daily_rent * 10 + (row.ann_per_delta ?? 0) * 0.1
  } else {
    row.efficiency = (row.score ?? 0) + (row.annualized ?? 0)
  }
  return row
}

function buildOppRows(
  pool: WheelScanResult | null,
  signals: LeapsSignal[],
  historyItems: WheelTimingHistoryItem[] | undefined,
  targets: WheelTarget[],
  openChecks: Record<string, WheelOpenPositionItem>,
  stats: WheelStats | null,
  profitTarget: number,
  portfolioAvailable = 100000,
): OppRow[] {
  const map = new Map<string, OppRow>()
  const targetsBySym = Object.fromEntries(targets.map(t => [t.symbol, t]))
  const stress = stats?.capital?.assignment_stress ?? 0
  const committed = stats?.capital?.total_committed ?? 0
  const stressHigh = stress > 0 && committed > 0 && stress >= committed * 1.5

  for (const o of pool?.opportunities || []) {
    const key = oppKey(o.contract_code, o.symbol, o.side, o.strike, o.expiry)
    map.set(key, {
      id: key,
      kind: 'OPEN',
      categories: ['RANKED'],
      strength: 'READY',
      actionable: false,
      ...emptyOpenMetrics(),
      symbol: o.symbol,
      side: o.side,
      contract_code: o.contract_code,
      strike: o.strike,
      expiry: o.expiry,
      dte: o.dte,
      score: o.score,
      annualized: o.annualized,
      delta: o.delta,
      bid: o.bid,
      spread_pct: o.spread_pct,
      open_interest: o.open_interest,
      tags: rankedTags(o),
      risk_hard: [],
      risk_soft: [],
      risk_block: false,
      efficiency: 0,
      cycle_id: o.cycle_id,
      ranked: o,
      iv_rank: o.iv_rank,
      underlying_price: o.spot_price,
      seen_at: pool?.scanned_at || null,
    })
  }

  const mergeTouch = (
    symbol: string,
    side: 'PUT' | 'CALL',
    code: string | undefined,
    fields: Partial<OppRow>,
  ) => {
    const key = oppKey(code, symbol, side, fields.strike, fields.expiry)
    const existing = map.get(key)
    if (existing) {
      if (!existing.categories.includes('EMA_TOUCH')) existing.categories.push('EMA_TOUCH')
      Object.assign(existing, Object.fromEntries(
        Object.entries(fields).filter(([, v]) => v !== undefined && v !== null),
      ))
      if (fields.ema_type === 'EMA200' && !existing.tags.includes('EMA200')) existing.tags.push('EMA200')
      if (fields.history) existing.history = fields.history
      if (fields.signal) existing.signal = fields.signal
    } else {
      map.set(key, {
        id: key,
        kind: 'OPEN',
        categories: ['EMA_TOUCH'],
        strength: 'WATCH',
        actionable: false,
        ...emptyOpenMetrics(),
        symbol,
        side,
        contract_code: code,
        tags: fields.ema_type === 'EMA200' ? ['EMA200'] : [],
        risk_hard: [],
        risk_soft: [],
        risk_block: false,
        efficiency: 0,
        ...fields,
      })
    }
  }

  // 先历史(字段全:bid/Δ/年化/DTE),再信号(补触线标记),避免信号空字段盖住历史
  for (const h of historyItems || []) {
    mergeTouch(h.symbol, h.side, h.contract_code, {
      history: h,
      ema_type: h.ema_type,
      ema_value: h.ema_value,
      trigger_price: h.trigger_price,
      iv_rank: h.iv_rank,
      underlying_price: h.underlying_price,
      times_triggered: h.times_triggered,
      strike: h.strike,
      expiry: h.expiry,
      dte: h.dte,
      delta: h.delta,
      bid: h.bid,
      annualized: h.annualized,
      seen_at: h.last_seen,
    })
  }

  for (const sig of signals) {
    const side: 'PUT' | 'CALL' = sig.signal_level === 'WHEEL_CALL' ? 'CALL' : 'PUT'
    const ext = sig as LeapsSignal & {
      strike?: number; dte?: number; delta?: number; bid?: number
      annualized?: number; expiry?: string
    }
    mergeTouch(sig.symbol, side, sig.contract_code, {
      signal: sig,
      ema_type: sig.ema_type,
      ema_value: sig.ema_value,
      trigger_price: sig.trigger_price,
      iv_rank: sig.iv_rank,
      underlying_price: sig.underlying_price,
      seen_at: sig.created_at,
      // 仅在有值时写入(mergeTouch 已过滤 null);避免冲掉 history 的 bid/年化
      strike: ext.strike,
      dte: ext.dte,
      delta: ext.delta,
      bid: ext.bid,
      annualized: ext.annualized,
      expiry: ext.expiry,
    })
  }

  const openRows = [...map.values()].map(r => enrichOpenRow(r, targetsBySym, stressHigh, portfolioAvailable))

  // 管理类:平仓/Roll/换仓/裸奔/空转
  const manage: OppRow[] = []
  const manageBase = {
    trade_tier: 'MANAGE' as TradeTier,
    tradeable: true,
    kill_reasons: [] as string[],
    dte_bucket: 'unknown' as DteBucket,
    daily_rent: null as number | null,
    ann_per_delta: null as number | null,
    covers_earnings: false,
  }
  // 一 cycle 仅一张管理卡:完全由后端 action_code 驱动
  for (const item of Object.values(openChecks)) {
    const code = (item.action_code || '').toUpperCase()
    if (!code || code === 'NONE') {
      // 无主建议时:若有浅 ITM 文案仍可展示弱提示,否则跳过
      if (!item.action_hint) continue
    }
    let cat: OppCategory = 'CLOSE'
    let tag = '关注'
    let actionable = true
    let efficiency = 500
    if (code === 'CLOSE') {
      cat = 'CLOSE'; tag = '该平仓'; efficiency = 1000 + (item.profit_pct || 0)
    } else if (code === 'ROLL' || code === 'ROLL_ADJUST' || code === 'PREPARE_ASSIGN') {
      cat = 'ROLL'
      tag = code === 'PREPARE_ASSIGN'
        ? (item.side === 'CALL' ? '准备交货' : '准备接货')
        : '该Roll'
      efficiency = 900 + (item.dte != null ? Math.max(0, 30 - item.dte) : 0)
    } else if (code === 'REPLACE') {
      cat = 'LOW_YIELD'
      tag = item.capital_tight ? '该换仓·资金紧' : '该换仓'
      // 资金紧时升排:与后端 action_priority 下调一致
      efficiency = (item.capital_tight ? 920 : 800) + (item.profit_pct || 0)
    } else if (code === 'HOLD_THETA') {
      cat = 'CLOSE'; tag = '吃θ'; actionable = false; efficiency = 400
    } else if (item.action_hint) {
      // 兜底:根据 hint 归类
      if ((item.action_hint || '').includes('Roll') || item.roll_21dte) {
        cat = 'ROLL'; tag = '该Roll'; efficiency = 850
      } else if (item.low_yield) {
        cat = 'LOW_YIELD'; tag = '该换仓'; efficiency = 800
      } else if (item.profit_hit) {
        cat = 'CLOSE'; tag = '该平仓'; efficiency = 1000
      } else {
        actionable = false; tag = '观察'
      }
    }
    const prio = item.action_priority ?? 9
    manage.push({
      id: `pos-${item.cycle_id}`,
      kind: 'MANAGE',
      categories: [cat],
      strength: 'MANAGE',
      ...manageBase,
      dte_bucket: dteBucket(item.dte),
      actionable,
      symbol: item.symbol,
      side: item.side as 'PUT' | 'CALL',
      contract_code: item.contract_code,
      strike: item.strike,
      expiry: item.expiry,
      dte: item.dte,
      remaining_annualized: item.remaining_annualized,
      annualized: item.remaining_annualized,
      profit_pct: item.profit_pct,
      action_hint: item.action_hint || `浮盈≥${profitTarget}%`,
      action_code: code || 'NONE',
      action_priority: prio,
      prefer_card: item.prefer_card,
      decision_why: item.reasons,
      tags: [tag],
      risk_hard: item.deep_itm ? ['深度ITM'] : [],
      risk_soft: [
        ...(item.itm && !item.deep_itm ? ['ITM'] : []),
        ...(item.dividend_warn ? [`除息${item.dividend_warn.days_to_ex}d`] : []),
        ...(item.capital_tight ? ['资金紧'] : []),
      ],
      risk_block: false,
      efficiency: efficiency + Math.max(0, 10 - prio) * 10,
      cycle_id: item.cycle_id,
      check: item,
      headline: item.action_hint
        || (item.profit_pct != null
          ? `浮盈 ${item.profit_pct}% · 买回 $${fmt(item.buyback_ask || item.current_price)}`
          : undefined),
      seen_at: null,
    })
  }
  for (const t of targets.filter(x => x.enabled)) {
    for (const c of t.active_cycles || []) {
      if (c.status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3) {
        manage.push({
          id: `uncov-${c.id}`,
          kind: 'MANAGE',
          categories: ['UNCOVERED'],
          strength: 'MANAGE',
          ...manageBase,
          actionable: true,
          symbol: t.symbol,
          side: 'CALL',
          cycle_id: c.id,
          tags: ['裸奔'],
          risk_hard: [],
          risk_soft: ['theta流失'],
          risk_block: false,
          efficiency: 700 + (c.uncovered_days || 0),
          headline: `持股 ${c.shares} 股已裸奔 ${c.uncovered_days} 天 · CB $${fmt(c.cost_basis)}`,
          action_hint: '去找 Call',
          seen_at: null,
        })
      }
    }
    if ((t.idle_days ?? 0) >= 5) {
      const working = (t.active_cycles || []).some(c => ['CSP_OPEN', 'CC_OPEN', 'HOLDING'].includes(c.status))
      if (!working) {
        manage.push({
          id: `idle-${t.symbol}`,
          kind: 'MANAGE',
          categories: ['IDLE'],
          strength: 'MANAGE',
          ...manageBase,
          actionable: true,
          symbol: t.symbol,
          side: 'PUT',
          tags: ['空转'],
          risk_hard: [],
          risk_soft: [],
          risk_block: false,
          efficiency: 600 + (t.idle_days || 0),
          headline: `空转 ${t.idle_days} 天 · 资金闲置`,
          action_hint: '找 Put 开轮',
          seen_at: null,
        })
      }
    }
  }

  return sortOppRows([...manage, ...openRows])
}

function symbolAllowsSide(t: WheelTarget | undefined, side: 'PUT' | 'CALL' | undefined): boolean {
  if (!t || !side) return true
  const cycles = t.active_cycles || []
  if (side === 'CALL') return cycles.some(c => c.status === 'HOLDING')
  // PUT: 有资金余量或未设上限
  if ((t.max_capital ?? 0) <= 0) return true
  const used = targetCapital(cycles)
  return used < t.max_capital
}

function filterOppRows(
  rows: OppRow[],
  cat: OppCategoryFilter,
  side: OppSideFilter,
  opts: {
    stateAware: boolean
    hideBlocked: boolean
    targets: WheelTarget[]
    selectedSymbol: string | null
    bucketFilter: OppBucketFilter
    hideUntradeable: boolean
  },
): OppRow[] {
  const bySym = Object.fromEntries(opts.targets.map(t => [t.symbol, t]))
  return rows.filter(r => {
    if (opts.hideBlocked && r.kind === 'OPEN' && r.risk_block && cat !== 'KILLED' && cat !== 'WATCH' && cat !== 'all') return false
    // 杀掉视图必须展示不可交易；「观察/全部」保留触线观察项,不被「隐藏不可交易」误杀
    if (opts.hideUntradeable && r.kind === 'OPEN' && !r.tradeable
      && cat !== 'KILLED' && cat !== 'WATCH' && cat !== 'all') return false
    if (opts.selectedSymbol && r.symbol !== opts.selectedSymbol) return false

    // DTE 桶:杀掉视图看全部桶；其它默认核心+延伸
    if (r.kind === 'OPEN' && cat !== 'KILLED') {
      if (opts.bucketFilter === 'core' && r.dte_bucket !== 'core') return false
      if (opts.bucketFilter === 'core_extend' && r.dte_bucket !== 'core' && r.dte_bucket !== 'extend') return false
      if (opts.bucketFilter === 'far' && r.dte_bucket !== 'far') return false
    }

    if (cat === 'KILLED') return r.kind === 'OPEN' && !r.tradeable
    if (cat === 'PRIORITY') {
      if (r.kind === 'MANAGE') return r.actionable
      return r.trade_tier === 'PRIORITY'
    }
    if (cat === 'QUEUE') return r.kind === 'OPEN' && r.trade_tier === 'QUEUE'
    if (cat === 'WATCH') return r.kind === 'OPEN' && r.trade_tier === 'WATCH'
    if (cat === 'ACTIONABLE') {
      if (r.kind === 'MANAGE') return r.actionable
      return r.trade_tier === 'PRIORITY'
    }
    if (cat === 'MANAGE') return r.kind === 'MANAGE'
    if (cat !== 'all') {
      if (!r.categories.includes(cat as OppCategory)) return false
    }
    if (side !== 'all' && r.side && r.side !== side) return false
    if (opts.stateAware && r.kind === 'OPEN' && r.side) {
      if (!symbolAllowsSide(bySym[r.symbol], r.side)) return false
    }
    return true
  })
}

function tierLabel(t: TradeTier): { text: string; color: SemColor } {
  if (t === 'PRIORITY') return { text: '优先', color: 'green' }
  if (t === 'QUEUE') return { text: '可排单', color: 'blue' }
  if (t === 'MANAGE') return { text: '该管', color: 'orange' }
  return { text: '观察', color: 'purple' }
}

/** 机会来源类型:触线50 / 触线200 / 高分 / 双满足 */
export type OppSignalKind = 'EMA50' | 'EMA200' | 'SCORE' | 'DUAL50' | 'DUAL200' | 'MANAGE' | 'OTHER'

function resolveOppSignalKind(row: Pick<OppRow, 'kind' | 'categories' | 'ema_type'>): OppSignalKind {
  if (row.kind === 'MANAGE') return 'MANAGE'
  const ranked = row.categories.includes('RANKED')
  const touch = row.categories.includes('EMA_TOUCH')
  const e200 = row.ema_type === 'EMA200'
  if (ranked && touch) return e200 ? 'DUAL200' : 'DUAL50'
  if (touch) return e200 ? 'EMA200' : 'EMA50'
  if (ranked) return 'SCORE'
  return 'OTHER'
}

function signalKindLabel(k: OppSignalKind): { text: string; color: SemColor; title: string } {
  switch (k) {
    case 'EMA50': return { text: '触线50', color: 'blue', title: '合约价触及 EMA50' }
    case 'EMA200': return { text: '触线200', color: 'purple', title: '合约价触及 EMA200(更强)' }
    case 'SCORE': return { text: '高分', color: 'green', title: '全池规则打分入选(未触线)' }
    case 'DUAL50': return { text: '双·50', color: 'green', title: '高分 ∩ 触线 EMA50' }
    case 'DUAL200': return { text: '双·200', color: 'green', title: '高分 ∩ 触线 EMA200' }
    case 'MANAGE': return { text: '持仓', color: 'orange', title: '在场管理项' }
    default: return { text: '其它', color: 'purple', title: '未分类来源' }
  }
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
    <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13, alignItems: 'center' }}>
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
            padding: '1px 7px', borderRadius: 9, fontSize: 12,
            background: i === idx ? STAGE_COLORS[s] + '33' : 'transparent',
            color: i === idx ? STAGE_COLORS[s] : 'var(--text-secondary)',
            border: `1px solid ${i === idx ? STAGE_COLORS[s] : 'var(--border)'}`,
            fontWeight: i === idx ? 700 : 400,
            whiteSpace: 'nowrap',
          }}>{STAGE_LABELS[s]}</span>
          {i < stages.length - 1 && <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>→</span>}
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
    expiry: (initial.expiry || '').slice(0, 10),
    qty: initial.qty || '1',
    price: initial.price || '',
    fee: initial.fee || '0',
    contract_size: initial.contract_size || '100',
    note: initial.note || '',
    traded_at: nowLocal(),
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
      const code = normalizeContractCode(form.contract_code, form.symbol) || form.contract_code || undefined
      await recordWheelTrade({
        symbol: form.symbol,
        trade_type: form.trade_type,
        contract_code: code,
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

  const codeWarn = needContract ? contractCodeWarning(form.contract_code) : null

  const inputStyle = {
    width: '100%', padding: '6px 8px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 14,
  } as const

  return (
    <div style={{
      position: 'fixed', inset: 0, background: '#0009', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div className="modal-card" style={{ width: 440, maxWidth: '100%', maxHeight: '85vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 16px', fontSize: 17 }}>登记交易 — {form.symbol}</h3>
        {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            类型
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
              {allowed.map(t => (
                <button key={t} type="button" onClick={() => setForm(f => ({ ...f, trade_type: t }))}
                  style={{
                    padding: '5px 12px', borderRadius: 14, fontSize: 13, cursor: 'pointer',
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
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              合约代码(选填,留空将按 Strike+到期日自动补全)
              <input value={form.contract_code} style={inputStyle} placeholder="留空自动生成,如 US.AAPL260821P00200000"
                onChange={e => setForm(f => ({ ...f, contract_code: e.target.value }))}
                onBlur={() => setForm(f => ({
                  ...f,
                  contract_code: normalizeContractCode(f.contract_code, f.symbol) || f.contract_code,
                }))} />
              {codeWarn && <div style={{ color: '#fb923c', fontSize: 13, marginTop: 4 }}>{codeWarn}</div>}
            </label>
          )}
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', background: 'var(--bg-secondary)', padding: '8px 10px', borderRadius: 6 }}>
            最少填<strong>成交价</strong>+数量;合约代码建议填全以便体检与转化率。富途成交后在此登记即可驱动状态机。
          </div>
          {(needContract || ['ASSIGNED', 'CALLED_AWAY'].includes(form.trade_type)) && (
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              行权价 Strike {['ASSIGNED', 'CALLED_AWAY'].includes(form.trade_type) ? '(留空则用在场合约的)' : ''}
              <input type="number" value={form.strike} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, strike: e.target.value }))} />
            </label>
          )}
          {needContract && (
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              到期日
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
                <input type="date" value={form.expiry} style={{ ...inputStyle, width: 150, cursor: 'pointer' }}
                  onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                  onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
                {expiryChips.map(([label, val]) => (
                  <button key={label} type="button" onClick={() => setForm(f => ({ ...f, expiry: val }))}
                    title={val}
                    style={{
                      padding: '3px 8px', borderRadius: 10, fontSize: 13, cursor: 'pointer',
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
              <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {form.trade_type === 'BUY_SHARES' ? '股数' : '张数'}
                <SelectNum
                  value={form.qty}
                  options={form.trade_type === 'BUY_SHARES' ? QTY_SHARE_OPTS : QTY_CONTRACT_OPTS}
                  style={inputStyle}
                  onChange={v => setForm(f => ({ ...f, qty: v }))}
                />
              </label>
            )}
            {needPrice && (
              <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {form.trade_type === 'SELL_SHARES' ? '每股卖价' : form.trade_type === 'BUY_SHARES' ? '每股成本' : '权利金/张'}
                <input type="number" step="any" value={form.price} style={inputStyle}
                  placeholder="成交价"
                  onChange={e => setForm(f => ({ ...f, price: e.target.value }))} />
              </label>
            )}
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              手续费
              <SelectNum value={form.fee} options={FEE_OPTS} style={inputStyle}
                onChange={v => setForm(f => ({ ...f, fee: v }))} />
            </label>
          </div>
          {needContract && (
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              合约乘数
              <SelectNum value={form.contract_size} options={CONTRACT_SIZE_OPTS} style={inputStyle}
                onChange={v => setForm(f => ({ ...f, contract_size: v }))} />
            </label>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              成交时间
              <input type="datetime-local" value={form.traded_at} style={{ ...inputStyle, cursor: 'pointer' }}
                onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                onChange={e => setForm(f => ({ ...f, traded_at: e.target.value }))} />
            </label>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              备注
              <input value={form.note} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
            </label>
          </div>
          {cashFlow != null && priceN > 0 && (
            <div style={{
              padding: '8px 12px', borderRadius: 6, fontSize: 14,
              background: cashFlow >= 0 ? '#4ade8011' : '#f8717111',
              border: `1px solid ${cashFlow >= 0 ? '#4ade8044' : '#f8717144'}`,
            }}>
              本笔现金流:<b style={{ color: cashFlow >= 0 ? '#4ade80' : '#f87171' }}>
                {cashFlow >= 0 ? '+' : ''}{cashFlow.toLocaleString('en-US', { maximumFractionDigits: 2 })}
              </b>
              <span style={{ color: 'var(--text-secondary)', marginLeft: 8, fontSize: 13 }}>
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
  const { toast } = useToast()
  const [tab, setTab] = useState<'home' | 'board' | 'opps' | 'timing' | 'ledger' | 'targets' | 'risk'>('home')
  const [cockpitOpen, setCockpitOpen] = useState(false)
  const [filterOpen, setFilterOpen] = useState(false)
  /** 后端统一机会流；有数据时 OPEN 行优先用它 */
  const [serverOpps, setServerOpps] = useState<WheelOpportunitiesResult | null>(null)
  const [serverOppsLoading, setServerOppsLoading] = useState(false)
  /** 全池扫描细进度：标的/到期日/合约 n/m */
  const [scanProgress, setScanProgress] = useState<WheelScanProgress | null>(null)
  const [targets, setTargets] = useState<WheelTarget[]>([])
  const [stats, setStats] = useState<WheelStats | null>(null)
  const [cycles, setCycles] = useState<WheelCycle[]>([])
  const [trades, setTrades] = useState<WheelTrade[]>([])
  const [candidates, setCandidates] = useState<LeapsCandidate[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pendingQueue, setPendingQueue] = useState<PendingRegItem[]>(() => loadPendingQueue())
  const [pendingLeaving, setPendingLeaving] = useState<string | null>(null)
  const [riskTier, setRiskTierState] = useState<RiskTier>(() => getRiskTier())
  const [budget, setBudgetState] = useState(() => getPortfolioBudget())
  const [budgetSource, setBudgetSource] = useState(() => getPortfolioBudgetSource())
  const [showOnboard, setShowOnboard] = useState(() => !isOnboardDone())
  const [manageCompare, setManageCompare] = useState<WheelOpenPositionItem | null>(null)
  const [executeLoading, setExecuteLoading] = useState(false)
  const [todayBoard, setTodayBoard] = useState<WheelTodayBoard | null>(null)
  const [expandedExplain, setExpandedExplain] = useState<string | null>(null)
  const [rowCursor, setRowCursor] = useState(0)

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
  // EMA 触线信号(机会扫描的一类)
  const [timingSignals, setTimingSignals] = useState<LeapsSignal[]>([])
  const [timingScanning, setTimingScanning] = useState(false)
  const [scanStatus, setScanStatus] = useState<WheelScanStatus | null>(null)
  // 触线历史(分页)
  const [timingHistory, setTimingHistory] = useState<WheelTimingHistoryPage | null>(null)
  const [historyPage, setHistoryPage] = useState(1)
  // 在场合约体检(cycle_id → item)
  const [openChecks, setOpenChecks] = useState<Record<string, WheelOpenPositionItem>>({})
  const [portfolioContext, setPortfolioContext] = useState<WheelPortfolioContext | null>(null)
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
    floor_price: string; delta_min: string; delta_max: string
    dte_min: string; dte_max: string; min_annualized: string
  } | null>(null)
  const [savingParams, setSavingParams] = useState(false)
  // 机会扫描(高分候选 + EMA 触线)
  const [poolScan, setPoolScan] = useState<WheelScanResult | null>(null)
  const [poolScanLoading, setPoolScanLoading] = useState(false)
  const [poolPushing, setPoolPushing] = useState(false)
  const [oppCatFilter, setOppCatFilter] = useState<OppCategoryFilter>('PRIORITY')
  const [oppSideFilter, setOppSideFilter] = useState<OppSideFilter>('all')
  const [oppBucketFilter, setOppBucketFilter] = useState<OppBucketFilter>('core_extend')
  const [oppStateAware, setOppStateAware] = useState(true)
  const [oppHideBlocked, setOppHideBlocked] = useState(true)
  const [oppHideUntradeable, setOppHideUntradeable] = useState(true)
  const [oppOnlySelected, setOppOnlySelected] = useState(false)
  const [killedDiag, setKilledDiag] = useState(false)
  /** 机会列表分页(客户端,筛完后再切页) */
  const [oppPage, setOppPage] = useState(1)
  const [oppPageSize, setOppPageSize] = useState(15)

  // 添加标的表单
  const [addSymbol, setAddSymbol] = useState('')
  const [addFloor, setAddFloor] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => subscribeSettings(next => setSettings(next)), [])
  useEffect(() => {
    const sync = () => setPendingQueue(loadPendingQueue())
    window.addEventListener('tradeforge:pending-reg', sync)
    return () => window.removeEventListener('tradeforge:pending-reg', sync)
  }, [])

  function flash(msg: string, kind: 'info' | 'success' | 'error' = 'info') {
    toast(msg, kind)
  }

  const loadAll = useCallback(async () => {
    setError(null)
    try {
      const st = getAppSettings()
      const [t, s, c, tr, cand, tim, hist, today] = await Promise.all([
        getWheelTargets().catch(() => []),
        getWheelStats().catch(() => null),
        getWheelCycles().catch(() => []),
        getWheelTrades().catch(() => []),
        getWheelCandidates().catch(() => []),
        getWheelTimingSignals(20).catch(() => []),
        getWheelTimingHistory(1, 40).catch(() => null),
        getWheelToday(st.marketHost, st.marketPort, false).catch(() => null),
      ])
      setTargets(t)
      if (today) setTodayBoard(today)
      // 后端未带 suggested_floor 时(旧进程/失败)前端补拉,避免整列「参考 --」
      const needFloor = (t as WheelTarget[]).filter(
        x => x.suggested_floor == null || !Number.isFinite(Number(x.suggested_floor)),
      )
      if (needFloor.length > 0) {
        Promise.all(
          needFloor.map(async row => {
            try {
              const f = await getWheelFloorSuggest(row.symbol)
              const sf = f.suggested_floor != null ? Number(f.suggested_floor) : NaN
              if (!Number.isFinite(sf) || sf <= 0) return null
              return {
                symbol: row.symbol,
                suggested_floor: sf,
                suggested_floor_delta: row.floor_price != null
                  ? Math.round((sf - Number(row.floor_price)) * 100) / 100
                  : (f.delta_vs_current ?? null),
                suggested_floor_spot: f.spot ?? null,
                suggested_floor_note: f.rationale || null,
                spot: row.spot ?? (f.spot != null ? Number(f.spot) : null),
              }
            } catch {
              return null
            }
          }),
        ).then(rows => {
          const by = Object.fromEntries(
            rows.filter(Boolean).map(r => [r!.symbol, r!]),
          )
          if (!Object.keys(by).length) return
          setTargets(prev => prev.map(x => {
            const u = by[x.symbol]
            return u ? { ...x, ...u } : x
          }))
        })
      }
      setStats(s)
      setCycles(c)
      setTrades(tr)
      setCandidates(cand)
      setTimingSignals(tim)
      if (hist) setTimingHistory(hist)
      // 在场合约体检(需 OpenD,失败静默)
      refreshChecks()
      // Telegram + 组合净值(唯一预算源)同步
      getBackendConfig().then(cfg => {
        setTgOk(!!cfg.telegram?.bot_token)
        const n = syncPortfolioBudgetFromConfig(cfg.wheel_portfolio?.total_equity)
        setBudgetState(n)
        setBudgetSource(getPortfolioBudgetSource())
      }).catch(() => setTgOk(null))
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  // 设置页保存净值后,首页预算即时刷新
  useEffect(() => subscribePortfolioBudget((n, src) => {
    setBudgetState(n)
    setBudgetSource(src as 'config' | 'legacy' | 'default')
  }), [])

  const refreshChecks = useCallback(() => {
    const st = getAppSettings()
    return checkWheelOpenPositions(st.marketHost, st.marketPort).then(r => {
      const map: Record<string, WheelOpenPositionItem> = {}
      r.items.forEach(i => { map[i.cycle_id] = i })
      setOpenChecks(map)
      setPortfolioContext(r.portfolio_context || null)
      setProfitTarget(r.profit_target_pct)
      setChecksAt(new Date())
      setOpendOk(true)
    }).catch(() => { setOpenChecks({}); setPortfolioContext(null); setOpendOk(false) })
  }, [])

  // 每 5 分钟自动刷新体检数据;每 30 秒重算新鲜度显示
  useEffect(() => {
    const t1 = setInterval(() => { refreshChecks() }, 5 * 60 * 1000)
    const t2 = setInterval(() => setNowTick(Date.now()), 30 * 1000)
    return () => { clearInterval(t1); clearInterval(t2) }
  }, [refreshChecks])

  /** 触线扫描:后台异步 + 轮询细进度(标的/到期日/合约 n/m) */
  async function runTimingScan(): Promise<void> {
    setTimingScanning(true)
    setScanStatus({
      running: true, started_at: null, finished_at: null,
      signals_found: 0, report: [], error: null,
      phase: 'timing', message: '触线扫描启动…',
      symbol: null, side: null, expiry: null,
      contract_i: 0, contract_n: 0, target_i: 0, target_n: 0,
    })
    try {
      await triggerWheelTimingScan()
      const deadline = Date.now() + 8 * 60 * 1000
      await new Promise<void>((resolve) => {
        const poll = async () => {
          const st = await getWheelScanStatus().catch(() => null)
          if (st) {
            // 合并更新:同标的才继承到期日/合约进度,换标的时清空避免串号
            setScanStatus(prev => {
              const sameSym = !!(st.symbol && prev?.symbol && st.symbol === prev.symbol && st.side === prev.side)
              return {
                ...(prev || {
                  running: true, started_at: null, finished_at: null,
                  signals_found: 0, report: [], error: null,
                }),
                ...st,
                message: st.message || prev?.message || '触线扫描中…',
                symbol: st.symbol ?? prev?.symbol ?? null,
                side: st.side ?? prev?.side ?? null,
                expiry: st.expiry != null && st.expiry !== ''
                  ? st.expiry
                  : (sameSym ? (prev?.expiry ?? null) : null),
                contract_i: st.contract_i != null ? st.contract_i : (sameSym ? (prev?.contract_i ?? 0) : 0),
                contract_n: st.contract_n != null && st.contract_n > 0
                  ? st.contract_n
                  : (sameSym ? (prev?.contract_n ?? 0) : 0),
                target_i: st.target_i ?? prev?.target_i ?? 0,
                target_n: st.target_n ?? prev?.target_n ?? 0,
              }
            })
          }
          if (st && !st.running && st.finished_at) {
            setTimingSignals(await getWheelTimingSignals(20).catch(() => []))
            setTimingScanning(false)
            resolve()
            return
          }
          // 后端已不 running 但未写 finished_at 的兜底
          if (st && !st.running && st.phase && st.phase !== 'timing') {
            setTimingScanning(false)
            resolve()
            return
          }
          if (Date.now() > deadline) {
            setTimingScanning(false)
            resolve()
            return
          }
          setTimeout(poll, 350)
        }
        // 立刻拉一次,再进入高频轮询
        void poll()
      })
    } catch (e: any) {
      setError('触线扫描失败:' + e.message)
      setTimingScanning(false)
    }
  }

  /** 机会扫描:优先后端 /opportunities 合流；并行触线；可选 TG 推送 */
  async function handleOpportunityScan(opts: { force?: boolean; push?: boolean } = {}) {
    const { force = false, push = false } = opts
    setError(null)
    setPoolScanLoading(true)
    setServerOppsLoading(true)
    setScanProgress({ running: true, phase: 'pool', message: '准备扫描…', target_i: 0, target_n: targets.length })
    if (push) setPoolPushing(true)

    const st = getAppSettings()

    // 轮询细进度（标的 / 到期日 / 合约 n/m）
    let progressTimer: ReturnType<typeof setInterval> | null = setInterval(() => {
      getWheelScanProgress()
        .then(p => setScanProgress(p))
        .catch(() => { /* ignore */ })
    }, 400)

    const unifiedPromise = (async () => {
      try {
        const r = await getWheelOpportunities(st.marketHost, st.marketPort, {
          refresh: force,
          run_pool: true,
          filter: 'all',
          hide_blocked: false,
        })
        setServerOpps(r)
        // 同步一份 pool 缓存供兼容/诊断
        if (r.pool) {
          setPoolScan(prev => ({
            scanned_at: r.pool?.scanned_at || prev?.scanned_at || r.built_at,
            targets_scanned: r.pool?.targets_scanned ?? prev?.targets_scanned ?? 0,
            total_found: r.pool?.total_found ?? r.summary?.total ?? prev?.total_found ?? 0,
            opportunities: prev?.opportunities || [],
            skipped: prev?.skipped || [],
            errors: prev?.errors || [],
            telegram_sent: prev?.telegram_sent,
          }))
        }
        // 无双满足时:可做触线多在「可排单」;都不可做则切「观察」— 避免默认「优先」空白
        const act = r.summary?.actionable ?? 0
        const watchN = r.summary?.watch ?? 0
        const dualN = r.summary?.dual ?? 0
        if (dualN === 0 && act > 0) {
          setOppCatFilter('QUEUE')
          flash(r.headline || `可做 ${act}(触线/高分) · 已切到可排单`, 'success')
        } else if (act === 0 && watchN > 0) {
          setOppCatFilter('WATCH')
          flash(r.headline || `暂无优先 · ${watchN} 条在观察`, 'info')
        } else {
          flash(r.headline || `机会已更新 · 可做 ${act}`, 'success')
        }
      } catch (e: any) {
        // 合流失败则回退本地全池
        try {
          setPoolScan(await getWheelPoolScan(st.marketHost, st.marketPort, force))
          flash('统一机会失败，已回退全池扫描', 'error')
        } catch (e2: any) {
          setError('机会扫描失败:' + (e2.message || e.message))
          flash('机会扫描失败', 'error')
        }
      } finally {
        setServerOppsLoading(false)
        setPoolScanLoading(false)
        if (progressTimer) {
          clearInterval(progressTimer)
          progressTimer = null
        }
        getWheelScanProgress().then(p => setScanProgress(p)).catch(() => setScanProgress(null))
      }
    })()

    const pushPromise = push
      ? (async () => {
        try {
          const r = await pushWheelPoolScan(st.marketHost, st.marketPort)
          if (!r.telegram_sent) {
            setError('已扫描,但 Telegram 未配置或发送失败(前往「设置」页检查)')
          } else {
            flash('已推送 TG Top', 'success')
          }
        } catch (e: any) {
          setError('TG 推送失败:' + e.message)
        } finally {
          setPoolPushing(false)
        }
      })()
      : Promise.resolve()

    const timingPromise = runTimingScan()
    await Promise.all([unifiedPromise, pushPromise, timingPromise])
  }

  async function handleRoll(cycleId: string, preferCard?: string | null) {
    setRollLoading(true)
    setError(null)
    try {
      const st = getAppSettings()
      const data = await getWheelRollOptions(cycleId, st.marketHost, st.marketPort)
      // 决策树 prefer_card 优先于 API highlighted
      if (preferCard) {
        setRollData({
          ...data,
          highlighted_card: preferCard,
          decision: { ...(data.decision || {}), prefer_card: preferCard },
        })
      } else {
        setRollData(data)
      }
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

  // 切换 tab / 筛选时重置键盘行光标
  useEffect(() => {
    setRowCursor(0)
  }, [tab, oppCatFilter, oppSideFilter, oppBucketFilter])

  /** 用 OpenD 补某行实时 bid，并写回 serverOpps */
  async function refreshOppBid(row: OppRow): Promise<number | null> {
    if (!row.contract_code) return null
    try {
      const q = await getWheelContractQuote(
        row.symbol,
        row.contract_code,
        settings.marketHost,
        settings.marketPort,
        row.side === 'CALL' ? 'CALL' : 'PUT',
      )
      const bid = q.bid != null && q.bid > 0 ? q.bid : null
      if (bid != null) {
        setServerOpps(prev => {
          if (!prev?.items?.length) return prev
          const items = prev.items.map(it => {
            const sameCode = (it.contract_code || it.contract_short || '').toUpperCase().replace(/^US\./, '')
              === row.contract_code!.toUpperCase().replace(/^US\./, '')
            if (!sameCode && !(it.symbol === row.symbol && it.strike === row.strike && String(it.expiry || '').slice(0, 10) === String(row.expiry || '').slice(0, 10))) {
              return it
            }
            return { ...it, bid, premium_used: it.premium_used ?? bid, has_live_bid: true }
          })
          return { ...prev, items }
        })
      }
      return bid
    } catch {
      return null
    }
  }

  async function handleSuggest(symbol: string, side: 'put' | 'call', cycleId?: string, forRow?: OppRow) {
    setSelectedSymbol(symbol)
    setSuggestLoading(true)
    setSuggest(null)
    setSuggestCycleId(cycleId)
    setError(null)
    flash(`正在拉 ${symbol} ${side === 'put' ? 'Put' : 'Call'} 期权链…`)
    try {
      // 先补该合约实时 bid（若有合约码）
      if (forRow?.contract_code) {
        const bid = await refreshOppBid(forRow)
        if (bid != null) flash(`${symbol} 买价已更新 ${bid}`, 'success')
        else flash(`${symbol} 未取到实时买价，展示期权链建议`, 'info')
      }
      const r = await getWheelSuggest(symbol, side, settings.marketHost, settings.marketPort, cycleId)
      setSuggest(r)
      // 若建议链含同行合约，再写一次 bid
      if (forRow?.contract_code && r.suggestions?.length) {
        const norm = forRow.contract_code.toUpperCase().replace(/^US\./, '')
        const hit = r.suggestions.find(s =>
          (s.contract_code || '').toUpperCase().replace(/^US\./, '') === norm
          || (forRow.strike != null && Math.abs((s.strike || 0) - forRow.strike) < 1e-6
            && String(s.expiry || '').slice(0, 10) === String(forRow.expiry || '').slice(0, 10)))
        if (hit?.bid != null && hit.bid > 0) {
          setServerOpps(prev => {
            if (!prev?.items?.length) return prev
            return {
              ...prev,
              items: prev.items.map(it => {
                const same = (it.contract_code || '').toUpperCase().replace(/^US\./, '') === norm
                return same ? { ...it, bid: hit.bid, premium_used: hit.premium_used ?? hit.bid } : it
              }),
            }
          })
        }
      }
      flash(`${symbol} 详情已加载`, 'success')
    } catch (e: any) {
      const msg = `获取${side === 'put' ? 'Put' : 'Call'}建议失败:` + e.message
      setError(msg)
      flash(msg, 'error')
    } finally {
      setSuggestLoading(false)
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
    if (isNaN(floor) || floor <= 0) { setError('请填写有效的愿接最高价(floor)'); return }
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

  const putBlocked = !!(
    serverOpps?.summary?.portfolio_put_blocked
    || serverOpps?.portfolio?.portfolio_put_blocked
    || stressBlocksNewPuts(
      stats?.capital?.assignment_stress ?? 0,
      stats?.capital?.total_committed ?? 0,
      riskTier,
    )
  )
  const ops = useMemo(() => {
    const idleCount = targets.filter(t => t.enabled && (t.idle_days ?? 0) >= 5).length
    const uncoveredCount = targets.reduce((n, t) =>
      n + (t.active_cycles || []).filter(c => c.status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3).length, 0)
    return computeOpsMetrics({
      trades, cycles, capital: stats?.capital ?? null,
      conversion: stats?.conversion ?? null, idleCount, uncoveredCount,
    })
  }, [trades, cycles, stats, targets, budget])

  const allOppRows = useMemo(() => {
    const local = buildOppRows(
      poolScan, timingSignals, timingHistory?.items, targets, openChecks, stats, profitTarget,
      ops.available,
    )
    const manage = local.filter(r => r.kind === 'MANAGE')
    // 有后端合流结果时，开仓行以服务端为准（档位/可做更一致）
    if (serverOpps?.items?.length) {
      const open = sortOppRows(serverOpps.items.map(serverOppToRow))
      return sortOppRows([...manage, ...open])
    }
    return local
  }, [poolScan, timingSignals, timingHistory, targets, openChecks, stats, profitTarget, ops.available, serverOpps])
  const filteredOppRows = useMemo(() => {
    let rows = filterOppRows(allOppRows, oppCatFilter, oppSideFilter, {
      stateAware: oppStateAware,
      hideBlocked: oppHideBlocked,
      targets,
      selectedSymbol: oppOnlySelected ? selectedSymbol : null,
      bucketFilter: oppBucketFilter,
      hideUntradeable: oppHideUntradeable,
    })
    if (putBlocked) rows = rows.filter(r => !(r.kind === 'OPEN' && r.side === 'PUT'))
    return rows
  }, [allOppRows, oppCatFilter, oppSideFilter, oppStateAware, oppHideBlocked, targets, oppOnlySelected, selectedSymbol, putBlocked, oppBucketFilter, oppHideUntradeable])

  // 筛选/扫描变化时回到第 1 页
  useEffect(() => {
    setOppPage(1)
    setRowCursor(0)
  }, [oppCatFilter, oppSideFilter, oppBucketFilter, oppStateAware, oppHideBlocked, oppHideUntradeable, oppOnlySelected, selectedSymbol, putBlocked, serverOpps?.built_at])

  const oppPageCount = Math.max(1, Math.ceil(filteredOppRows.length / oppPageSize))
  const oppPageSafe = Math.min(oppPage, oppPageCount)
  const pagedOppRows = useMemo(() => {
    const start = (oppPageSafe - 1) * oppPageSize
    return filteredOppRows.slice(start, start + oppPageSize)
  }, [filteredOppRows, oppPageSafe, oppPageSize])

  const oppCounts = useMemo(() => {
    const open = allOppRows.filter(r => r.kind === 'OPEN')
    const killed = open.filter(r => !r.tradeable)
    return {
      all: allOppRows.length,
      priority: open.filter(r => r.trade_tier === 'PRIORITY').length,
      queue: open.filter(r => r.trade_tier === 'QUEUE').length,
      watch: open.filter(r => r.trade_tier === 'WATCH').length,
      actionable: open.filter(r => r.trade_tier === 'PRIORITY').length
        + allOppRows.filter(r => r.kind === 'MANAGE' && r.actionable).length,
      ranked: open.filter(r => r.categories.includes('RANKED')).length,
      touch: open.filter(r => r.categories.includes('EMA_TOUCH')).length,
      manage: allOppRows.filter(r => r.kind === 'MANAGE').length,
      close: allOppRows.filter(r => r.categories.includes('CLOSE')).length,
      roll: allOppRows.filter(r => r.categories.includes('ROLL')).length,
      put: allOppRows.filter(r => r.side === 'PUT').length,
      call: allOppRows.filter(r => r.side === 'CALL').length,
      killed: killed.length,
      far: open.filter(r => r.dte_bucket === 'far').length,
      killBreakdown: killed.reduce((acc, r) => {
        for (const k of r.kill_reasons) acc[k] = (acc[k] || 0) + 1
        return acc
      }, {} as Record<string, number>),
    }
  }, [allOppRows])
  const oppScanning = poolScanLoading || serverOppsLoading || timingScanning || poolPushing

  async function copyOrderMemo(p: {
    symbol: string; side: 'PUT' | 'CALL'; action: 'SELL' | 'BUY'
    contract_code?: string; strike?: number | null; expiry?: string | null
    qty?: number; price?: number | null; note?: string
    price_kind?: 'bid' | 'premium' | 'trigger' | 'none'
  }) {
    const ok = await copyText(buildFutuOrderMemo(p))
    flash(ok ? '已复制富途下单备忘' : '复制失败,请手动抄写', ok ? 'success' : 'error')
  }

  function oppMemoFields(row: OppRow): {
    price: number | null
    price_kind: 'bid' | 'premium' | 'trigger' | 'none'
  } {
    const px = resolveOppSellPrice({
      bid: row.bid,
      trigger_price: row.trigger_price,
    })
    // 备忘：有 bid 写卖价；仅有触线价则标明非买价（price 传 trigger 供对照）
    if (px.kind === 'bid' || px.kind === 'premium') {
      return { price: px.sell, price_kind: px.kind }
    }
    if (px.trigger != null) return { price: px.trigger, price_kind: 'trigger' }
    return { price: null, price_kind: 'none' }
  }

  function enqueuePending(item: Omit<PendingRegItem, 'id' | 'created_at'>) {
    addPendingReg(item)
    setPendingQueue(loadPendingQueue())
    flash(`已加入待登记: ${item.symbol}`, 'success')
  }

  function openOppRegister(row: OppRow) {
    if (row.kind === 'MANAGE') {
      const code = (row.action_code || '').toUpperCase()
      // 吃 θ / 观察:打开分叉决策台(高亮放任到期),不直接登记平仓
      if ((code === 'HOLD_THETA' || code === 'NONE') && row.check) {
        setManageCompare(row.check)
        return
      }
      if (code === 'HOLD_THETA' || code === 'NONE') {
        flash(row.action_hint || '建议继续持有观察', 'info')
        return
      }
      if (code === 'ROLL' || code === 'ROLL_ADJUST' || code === 'PREPARE_ASSIGN'
        || row.categories.includes('ROLL')) {
        if (row.check) setManageCompare(row.check)
        if (row.cycle_id) handleRoll(row.cycle_id, row.prefer_card || row.check?.prefer_card)
        return
      }
      // 平仓/换仓/止盈:一律先决策弹窗(与轮子列表一致)
      if (
        row.check
        && (
          row.categories.includes('CLOSE')
          || row.categories.includes('LOW_YIELD')
          || code === 'REPLACE'
          || code === 'CLOSE'
        )
      ) {
        setManageCompare(row.check)
        return
      }
      if (row.categories.includes('UNCOVERED')) {
        handleSuggest(row.symbol, 'call', row.cycle_id || undefined)
        return
      }
      if (row.categories.includes('IDLE')) {
        handleSuggest(row.symbol, 'put')
        return
      }
      // 无体检缓存时才退回直接登记
      if ((row.categories.includes('CLOSE') || row.categories.includes('LOW_YIELD') || code === 'REPLACE' || code === 'CLOSE') && row.side) {
        const closeType = row.side === 'PUT' ? 'BUY_PUT_CLOSE' : 'BUY_CALL_CLOSE'
        setTradeModal({
          initial: {
            symbol: row.symbol,
            trade_type: closeType,
            contract_code: normalizeContractCode(row.contract_code, row.symbol) || row.contract_code || '',
            strike: row.strike != null ? String(row.strike) : '',
            expiry: row.expiry ? String(row.expiry).slice(0, 10) : '',
            price: row.check?.buyback_ask != null ? String(row.check.buyback_ask)
              : row.check?.current_price != null ? String(row.check.current_price) : '',
            qty: '1',
            note: row.action_hint || (row.categories.includes('CLOSE') ? '利润达标平仓' : '剩余年化低换仓'),
          },
          status: row.side === 'PUT' ? 'CSP_OPEN' : 'CC_OPEN',
          cycleId: row.cycle_id || undefined,
        })
        return
      }
    }
    if (row.side === 'PUT' && putBlocked) {
      flash('组合行权压力过高,已暂停新开 Put(可在标的设置调风险档位)')
      return
    }
    const t = targets.find(x => x.symbol === row.symbol)
    const used = targetCapital(t?.active_cycles || [])
    const headroom = t && (t.max_capital ?? 0) > 0 ? Math.max(0, t.max_capital - used) : null
    const qty = row.strike != null
      ? suggestQty({
        strike: row.strike, side: row.side || 'PUT',
        symbolHeadroom: headroom,
        portfolioAvailable: ops.available,
      })
      : 1
    const { price, price_kind } = oppMemoFields(row)
    const noteParts = [
      row.categories.includes('ACTIONABLE') ? '可下单' : '',
      row.strength === 'STRONG' ? '强信号' : row.strength === 'READY' ? '可做' : '观察',
      row.ema_type ? `触${row.ema_type}` : '',
      row.score != null ? `评分${fmt(row.score, 1)}` : '',
      row.annualized != null && price_kind !== 'trigger' ? `年化${fmt(row.annualized, 1)}%` : '',
      price_kind === 'trigger' ? `仅触线价${row.trigger_price}·无买价` : '',
      price_kind === 'none' ? '无买价' : '',
      row.seen_at ? `发现${fmtRelativeTime(row.seen_at)}` : '',
    ].filter(Boolean)
    const code = normalizeContractCode(row.contract_code, row.symbol) || row.contract_code || ''
    // 登记默认价：仅填真实 bid；无买价留空让用户填成交价
    const regPrice = price_kind === 'bid' || price_kind === 'premium' ? price : null
    enqueuePending({
      symbol: row.symbol,
      side: row.side || 'PUT',
      trade_type: row.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
      contract_code: code,
      strike: row.strike,
      expiry: row.expiry ? String(row.expiry).slice(0, 10) : undefined,
      qty: Math.max(1, qty),
      price: regPrice,
      note: noteParts.join(' · '),
      cycle_id: row.cycle_id || undefined,
      source: 'opp',
    })
    setTradeModal({
      initial: {
        symbol: row.symbol,
        trade_type: row.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
        contract_code: code,
        strike: row.strike != null ? String(row.strike) : '',
        expiry: row.expiry ? String(row.expiry).slice(0, 10) : '',
        price: regPrice != null ? String(regPrice) : '',
        qty: String(Math.max(1, qty)),
        note: noteParts.join(' · '),
      },
      status: row.side === 'PUT' ? 'IDLE' : 'HOLDING',
      cycleId: row.cycle_id || undefined,
    })
  }

  // 键盘快捷键: J/K 行 · Enter 登记 · C 备忘 · ? 为何 · / 筛选 · 1-3 Tab
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return
      if (e.key === '1') { setTab('home'); return }
      if (e.key === '2') { setTab('opps'); return }
      if (e.key === '3') { setTab('board'); return }
      if (tab !== 'opps' && tab !== 'home') return
      const rows = tab === 'opps'
        ? pagedOppRows
        : allOppRows.filter(r => r.kind === 'OPEN' && r.trade_tier === 'PRIORITY' && !(putBlocked && r.side === 'PUT'))
      if (e.key === 'j' || e.key === 'J') {
        e.preventDefault()
        setRowCursor(c => {
          if (tab === 'opps' && c >= rows.length - 1 && oppPageSafe < oppPageCount) {
            setOppPage(p => p + 1)
            return 0
          }
          return Math.min(c + 1, Math.max(0, rows.length - 1))
        })
      } else if (e.key === 'k' || e.key === 'K') {
        e.preventDefault()
        setRowCursor(c => {
          if (tab === 'opps' && c <= 0 && oppPageSafe > 1) {
            setOppPage(p => p - 1)
            return oppPageSize - 1
          }
          return Math.max(0, c - 1)
        })
      } else if (e.key === 'Enter' && rows[rowCursor]) {
        e.preventDefault()
        openOppRegister(rows[rowCursor])
      } else if ((e.key === 'c' || e.key === 'C') && rows[rowCursor]?.kind === 'OPEN') {
        e.preventDefault()
        const r = rows[rowCursor]
        const mf = resolveOppSellPrice({ bid: r.bid, trigger_price: r.trigger_price })
        copyOrderMemo({
          symbol: r.symbol, side: r.side || 'PUT', action: 'SELL',
          contract_code: r.contract_code, strike: r.strike, expiry: r.expiry,
          price: mf.kind === 'bid' || mf.kind === 'premium' ? mf.sell : mf.trigger,
          price_kind: mf.kind === 'bid' || mf.kind === 'premium' ? mf.kind : (mf.trigger != null ? 'trigger' : 'none'),
        })
      } else if (e.key === '?' && rows[rowCursor]?.kind === 'OPEN') {
        e.preventDefault()
        setExpandedExplain(rows[rowCursor].id)
      } else if (e.key === '/' && tab === 'opps') {
        e.preventDefault()
        setFilterOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab, pagedOppRows, allOppRows, rowCursor, putBlocked, oppPageSafe, oppPageCount, oppPageSize])

  const inputStyle = {
    padding: '5px 8px', background: 'var(--bg-secondary)', border: '1px solid var(--border)',
    borderRadius: 4, color: 'var(--text)', fontSize: 14,
  } as const

  const manageCount = allOppRows.filter(r => r.kind === 'MANAGE').length
  const priorityCount = oppCounts.priority
  // 主路径：今日/机会/持仓；台账/标的日常配置；触线档案+风控为次级
  const pageTabs: { k: typeof tab; label: string }[] = [
    { k: 'home', label: '今日' },
    { k: 'opps', label: priorityCount ? `机会(${priorityCount})` : '机会' },
    { k: 'board', label: '持仓' },
    { k: 'ledger', label: '台账' },
    { k: 'targets', label: `标的(${targets.length})` },
    { k: 'timing', label: '触线档案' },
    { k: 'risk', label: '风控' },
  ]

  function showKilledOpps() {
    setOppHideUntradeable(false)
    setOppHideBlocked(false)
    setOppBucketFilter('all_buckets')
    setOppCatFilter('KILLED')
    setKilledDiag(true)
    flash(`已显示 ${oppCounts.killed} 条被杀掉机会`, 'info')
  }

  return (
    <div className="page" style={{ maxWidth: 1280 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0 }}>Wheel</h2>
        <button type="button" className="btn btn-sm btn-secondary" onClick={loadAll} title="刷新台账与标的">刷新</button>
        <span style={{ display: 'flex', gap: 12, alignItems: 'center', marginLeft: 'auto', fontSize: 13, color: 'var(--text-secondary)' }}>
          <StatusDot ok={opendOk} label="富途" />
          <StatusDot ok={tgOk} label="TG" />
          {(() => {
            void nowTick
            if (!checksAt) return <span>行情未加载</span>
            const mins = Math.floor((Date.now() - checksAt.getTime()) / 60000)
            return <span style={{ color: mins >= 10 ? 'var(--warning)' : undefined }}>
              截至 {checksAt.toTimeString().slice(0, 5)}{mins > 0 ? ` (${mins}m)` : ''}
            </span>
          })()}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => refreshChecks()} title="刷新 OpenD 体检">↻</button>
        </span>
      </div>

      {opendOk === false && (
        <div className="banner warn">
          <span style={{ flex: 1 }}>富途 OpenD 未连接 — 实时数据不可用，台账/登记仍可用</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => refreshChecks()}>重试连接</button>
        </div>
      )}
      {undoTrade && (
        <div className="banner info">
          <span style={{ flex: 1 }}>已删除「{TRADE_LABELS[undoTrade.trade_type]}」({undoTrade.symbol})</span>
          <button type="button" className="btn btn-sm btn-primary" onClick={handleUndoDelete}>撤销</button>
        </div>
      )}
      {error && (
        <div className="banner error">
          <span style={{ flex: 1 }}>{error}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={loadAll}>重试</button>
          <span style={{ fontSize: 13, opacity: 0.85 }}>{scanFailureHint(error).tips.join(' · ')}</span>
        </div>
      )}

      {showOnboard && (
        <div className="panel" style={{ borderColor: 'rgba(56,189,248,0.35)' }}>
          <div className="panel-title">👋 3 步上手</div>
          <ol style={{ margin: '0 0 12px', paddingLeft: 18, fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <li>标的里添加代码并设愿接最高价(Put 行权价上限,不是止损)</li>
            <li>机会扫描 → 优先 → 备忘 → 富途成交</li>
            <li>今日「待登记」填成交价，驱动状态机</li>
          </ol>
          <div className="empty-actions" style={{ justifyContent: 'flex-start' }}>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => { setTab('targets'); setShowOnboard(false); setOnboardDone() }}>去加标的</button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setTab('opps'); setShowOnboard(false); setOnboardDone() }}>去扫描</button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setShowOnboard(false); setOnboardDone() }}>知道了</button>
          </div>
        </div>
      )}

      {/* 组合摘要 — 今日三问优先:该管 / 优先 / 可用 / 压力 */}
      <div className="metric-row">
        <span className={`metric-chip ${manageCount > 0 ? 'warn' : 'ok'}`}><span>该管</span><b>{manageCount}</b></span>
        <span className="metric-chip"><span>优先开</span><b>{priorityCount}</b></span>
        <span className={`metric-chip ${ops.available > 0 ? 'ok' : 'warn'}`}><span>可用</span><b>${fmtMoney(ops.available)}</b></span>
        <span className={`metric-chip ${putBlocked ? 'danger' : ''}`}><span>压力</span><b>{putBlocked ? '高·停Put' : '正常'}</b></span>
        {pendingQueue.length > 0 && (
          <span className="metric-chip warn"><span>待登记</span><b>{pendingQueue.length}</b></span>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCockpitOpen(v => !v)}>
          {cockpitOpen ? '收起指标' : '更多指标'}
        </button>
      </div>
      {cockpitOpen && (
        <div className="metrics-grid" style={{ marginBottom: 16, border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
          {[
            {
              label: budgetSource === 'config' ? '组合净值' : '预算(未设净值)',
              value: `$${fmtMoney(ops.budget)}`,
            },
            { label: '占用', value: `$${fmtMoney(ops.committed)}` },
            { label: '本月权利金', value: `$${fmt(stats?.premium_month)}` },
            { label: '活跃轮子', value: String(stats?.active_cycles ?? '—') },
            { label: '行权压力', value: `$${fmtMoney(ops.assignment_stress)}` },
            { label: '触线转化', value: ops.conversion_rate != null ? `${ops.conversion_rate}%` : '—' },
            { label: '临期', value: String(stats?.expiring_soon.length ?? 0) },
            { label: '利润达标', value: String(profitHits.length) },
          ].map(m => (
            <div key={m.label} className="metric-card">
              <div className="value" style={{ fontSize: 19 }}>{m.value}</div>
              <div className="label">{m.label}</div>
            </div>
          ))}
        </div>
      )}

      {(profitHits.length > 0 || (stats?.expiring_soon.length ?? 0) > 0) && tab === 'home' && (
        <div className="banner warn">
          {profitHits.length > 0 && <span>💰 达标 {profitHits.length} 笔</span>}
          {(stats?.expiring_soon.length ?? 0) > 0 && <span>⚠ 临期 {stats!.expiring_soon.length} 笔</span>}
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => { setTab('opps'); setOppCatFilter('MANAGE') }}>去处理</button>
        </div>
      )}

      {/* 主 Tab：全部平铺，不折叠 */}
      <div className="page-tabs">
        {pageTabs.map(t => (
          <button key={t.k} type="button" className={`page-tab ${tab === t.k ? 'active' : ''}`}
            onClick={() => setTab(t.k)}>
            {t.label}
          </button>
        ))}
        <span className="desktop-only" style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--text-tertiary)', alignSelf: 'center' }}>
          快捷键 1/2/3 · J/K · Enter · C · ? · /
        </span>
      </div>

      {/* ── 今日(首屏决策) ── */}
      {tab === 'home' && (
        <div className="tab-panel">
          <div className="context-strip">
            <div>
              <div className="headline">
                {manageCount > 0 || pendingQueue.length > 0 || priorityCount > 0
                  ? `${manageCount} 项待处理 · ${priorityCount} 笔优先 · ${pendingQueue.length} 待登记`
                  : '今日清闲 — 可扫描找新机会'}
              </div>
              <div className="sub">
                档位 {STRATEGY_TEMPLATES[riskTier].label}
                {putBlocked ? ' · 行权压力高已停新 Put' : ''}
                {opendOk === false ? ' · OpenD 离线' : ''}
                {todayBoard?.stale ? ` · 行情缓存${todayBoard.stale_age_minutes != null ? ` ${todayBoard.stale_age_minutes}m` : ''}` : ''}
                {todayBoard?.capital?.buying_power != null ? ` · BP $${fmt(todayBoard.capital.buying_power, 0)}` : ''}
              </div>
              {todayBoard?.headline && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                  {todayBoard.headline}
                </div>
              )}
            </div>
            <div className="context-actions">
              <button type="button" className="btn btn-primary"
                disabled={oppScanning}
                onClick={() => { setTab('opps'); handleOpportunityScan() }}>
                {oppScanning ? '扫描中…' : '扫机会'}
              </button>
              <button type="button" className="btn btn-secondary"
                onClick={() => { setTab('opps'); setOppCatFilter('MANAGE') }}>
                处理待办
              </button>
            </div>
          </div>

          {oppScanning && (
            <div className="scan-progress panel">
              <div className="scan-progress-row">
                <span className="label">高分</span>
                <div className="bar">
                  <div
                    className={`fill ${poolScanLoading || serverOppsLoading ? (scanProgress?.contract_n ? '' : 'pulse') : ''}`}
                    style={{
                      width: (() => {
                        if (!(poolScanLoading || serverOppsLoading)) return poolScan || serverOpps ? '100%' : '0%'
                        const ti = scanProgress?.target_i || 0
                        const tn = scanProgress?.target_n || 0
                        const ci = scanProgress?.contract_i || 0
                        const cn = scanProgress?.contract_n || 0
                        if (tn > 0) {
                          const base = ((ti - 1) / tn) * 100
                          const slice = cn > 0 ? (ci / cn) * (100 / tn) : 0
                          return `${Math.min(99, Math.max(4, base + slice))}%`
                        }
                        return '40%'
                      })(),
                    }}
                  />
                </div>
                <span>
                  {poolScanLoading || serverOppsLoading
                    ? (scanProgress?.target_n ? `${scanProgress.target_i || 0}/${scanProgress.target_n}` : '扫描中')
                    : '完成'}
                </span>
              </div>
              {(poolScanLoading || serverOppsLoading) && (
                <div className="scan-progress-detail">
                  <ScanProgressDetail
                    symbol={scanProgress?.symbol}
                    side={scanProgress?.side}
                    expiry={scanProgress?.expiry}
                    contract_i={scanProgress?.contract_i}
                    contract_n={scanProgress?.contract_n}
                    target_i={scanProgress?.target_i}
                    target_n={scanProgress?.target_n}
                    fallback={scanProgress?.message || '准备扫描…'}
                  />
                </div>
              )}
              <div className="scan-progress-row">
                <span className="label">触线</span>
                <div className="bar">
                  <div
                    className={`fill ${timingScanning ? (scanStatus?.contract_n ? '' : 'pulse') : ''}`}
                    style={{
                      width: (() => {
                        if (!timingScanning) return scanStatus?.finished_at ? '100%' : '0%'
                        const ti = scanStatus?.target_i || 0
                        const tn = scanStatus?.target_n || 0
                        const ci = scanStatus?.contract_i || 0
                        const cn = scanStatus?.contract_n || 0
                        if (tn > 0) {
                          const base = ((Math.max(ti, 1) - 1) / tn) * 100
                          const slice = cn > 0 ? (ci / cn) * (100 / tn) : 0
                          return `${Math.min(99, Math.max(4, base + slice))}%`
                        }
                        return '40%'
                      })(),
                    }}
                  />
                </div>
                <span>
                  {timingScanning
                    ? (scanStatus?.target_n ? `${scanStatus.target_i || 0}/${scanStatus.target_n}` : '扫描中')
                    : scanStatus?.finished_at ? `触发${scanStatus.signals_found}` : '—'}
                </span>
              </div>
              {timingScanning && (
                <div className="scan-progress-detail">
                  <ScanProgressDetail
                    symbol={scanStatus?.symbol}
                    side={scanStatus?.side}
                    expiry={scanStatus?.expiry}
                    contract_i={scanStatus?.contract_i}
                    contract_n={scanStatus?.contract_n}
                    target_i={scanStatus?.target_i}
                    target_n={scanStatus?.target_n}
                    fallback={scanStatus?.message || '触线扫描启动…'}
                  />
                </div>
              )}
            </div>
          )}

          {/* 今日待办流: P0 该管 → P1 开仓 → P2 待登记 */}
          <div className="panel" style={{ borderColor: 'rgba(0,200,5,0.28)' }}>
            <div className="panel-title" style={{ marginBottom: 4 }}>今日待办</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>
              先处理持仓，再开新仓，最后清登记队列
            </div>
            {putBlocked && (
              <div className="banner error" style={{ marginBottom: 10 }}>
                组合压力高:已暂停新开 Put
                {serverOpps?.portfolio?.utilization_pct != null
                  && ` · 利用率 ${fmt(serverOpps.portfolio.utilization_pct, 0)}%`}
              </div>
            )}
            {todayBoard?.stale && (
              <div className="banner warn" style={{ marginBottom: 10 }}>
                行情缓存(OpenD 弱网) · 决策可看但价格可能旧
                {todayBoard.stale_age_minutes != null ? ` · ${todayBoard.stale_age_minutes} 分钟前` : ''}
              </div>
            )}
            {(todayBoard?.concentration?.warnings?.length || 0) > 0 && (
              <div className="banner warn" style={{ marginBottom: 10, fontSize: 13 }}>
                集中度: {(todayBoard!.concentration!.warnings || []).slice(0, 2).join('；')}
              </div>
            )}
            {(todayBoard?.events?.length || 0) > 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
                近事件: {(todayBoard!.events || []).slice(0, 4).map(e =>
                  `${e.symbol} ${e.label}${e.days === 0 ? '今' : `${e.days}d`}`).join(' · ')}
              </div>
            )}
            {(todayBoard?.post_assign?.length || 0) > 0 && (
              <div className="banner info" style={{ marginBottom: 10 }}>
                <b>指派后待挂 CC</b>
                {(todayBoard!.post_assign || []).slice(0, 3).map((p: any) => (
                  <div key={String(p.cycle_id)} style={{ marginTop: 4 }}>
                    {p.symbol} {p.shares}股
                    {p.cost_basis != null ? ` · CB≈$${p.cost_basis}` : ''}
                    {p.uncovered_days != null ? ` · 裸奔${p.uncovered_days}天` : ''}
                    {' · '}{p.next_step_hint || '找 Call'}
                  </div>
                ))}
              </div>
            )}
            <div className="home-todo">
              {/* P0 */}
              <div className="home-todo-step p0">
                <div className="home-todo-num">1</div>
                <div>
                  <div className="home-todo-label">P0 · 必须处理持仓</div>
                  {(() => {
                    const list = allOppRows.filter(r => r.kind === 'MANAGE')
                      .sort((a, b) => (a.action_priority ?? 9) - (b.action_priority ?? 9))
                      .slice(0, 3)
                    if (!list.length) return <div className="home-todo-empty">暂无紧急项 — 持仓健康</div>
                    return list.map(m => (
                      <div key={m.id} className="opp-row" style={{ margin: '0 0 6px' }}>
                        <div className="opp-row-main">
                          <div className="opp-row-title">
                            <Badge color={m.categories.includes('CLOSE') ? 'green' : 'orange'}>
                              {m.tags[0] || '该管'}
                            </Badge>
                            {m.symbol} {m.side}
                            {m.strike != null && <span style={{ opacity: 0.7 }}>${m.strike}</span>}
                          </div>
                          <div className="opp-row-meta">
                            <span>{m.action_hint || m.headline}</span>
                            {m.dte != null && <span>DTE {m.dte}</span>}
                            {m.profit_pct != null && <span>浮盈 {m.profit_pct}%</span>}
                          </div>
                        </div>
                        <div className="opp-row-actions">
                          {m.actionable !== false && (
                            <button type="button" className="btn btn-primary btn-sm" onClick={() => openOppRegister(m)}>
                              {(m.action_code === 'ROLL' || m.action_code === 'ROLL_ADJUST' || m.action_code === 'PREPARE_ASSIGN')
                                ? '看 Roll' : '处理'}
                            </button>
                          )}
                        </div>
                      </div>
                    ))
                  })()}
                  {manageCount > 3 && (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setTab('opps'); setOppCatFilter('MANAGE') }}>
                      全部 {manageCount} 项 →
                    </button>
                  )}
                </div>
              </div>
              {/* P1 */}
              <div className="home-todo-step p1">
                <div className="home-todo-num">2</div>
                <div>
                  <div className="home-todo-label">P1 · 优先开仓</div>
                  {(() => {
                    if (putBlocked) {
                      return <div className="home-todo-empty">压力解除前不推新 Put · 可看 CC 或触线</div>
                    }
                    const openPick = (serverOpps?.primary_picks || [])
                      .filter(p => p.actionable && !(putBlocked && p.side === 'PUT'))
                      .map(serverOppToRow)[0]
                      || allOppRows.find(r => r.kind === 'OPEN' && r.is_top_pick && r.tradeable)
                      || allOppRows.find(r => r.kind === 'OPEN' && (r.trade_tier === 'PRIORITY' || r.trade_tier === 'QUEUE') && r.tradeable)
                    if (!openPick) {
                      return (
                        <div className="home-todo-empty">
                          暂无主推{' '}
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => handleOpportunityScan()}>扫机会</button>
                        </div>
                      )
                    }
                    const sk = signalKindLabel(resolveOppSignalKind(openPick))
                    return (
                      <div className="opp-row" style={{ margin: 0 }}>
                        <div className="opp-row-main">
                          <div className="opp-row-title">
                            <Badge color="green">主推</Badge>
                            <Badge color={sk.color}>{sk.text}</Badge>
                            {openPick.symbol} {openPick.side}
                            {openPick.strike != null && <span style={{ opacity: 0.75 }}>${openPick.strike}</span>}
                          </div>
                          <div className="opp-row-meta">
                            {openPick.annualized != null && <span>年化 {fmt(openPick.annualized, 1)}%</span>}
                            {openPick.bid != null && <span>bid {fmt(openPick.bid, 2)}</span>}
                            {openPick.dte != null && <span>DTE {openPick.dte}</span>}
                          </div>
                        </div>
                        <div className="opp-row-actions">
                          <button type="button" className="btn btn-primary btn-sm" onClick={() => openOppRegister(openPick)}>登记</button>
                        </div>
                      </div>
                    )
                  })()}
                  {serverOpps?.headline && (
                    <div style={{ marginTop: 6, fontSize: 13, color: 'var(--text-tertiary)' }}>{serverOpps.headline}</div>
                  )}
                </div>
              </div>
              {/* P2 */}
              <div className="home-todo-step p2">
                <div className="home-todo-num">3</div>
                <div>
                  <div className="home-todo-label">P2 · 待登记队列</div>
                  {pendingQueue.length === 0 ? (
                    <div className="home-todo-empty">队列为空</div>
                  ) : (
                    <div className="opp-row" style={{ margin: 0 }}>
                      <div className="opp-row-main">
                        <div className="opp-row-title">
                          <Badge color="blue">待登记</Badge>
                          {pendingQueue.length} 笔备忘
                        </div>
                        <div className="opp-row-meta">
                          <span>{pendingQueue.slice(0, 3).map(p => p.symbol).join(' · ')}{pendingQueue.length > 3 ? '…' : ''}</span>
                        </div>
                      </div>
                      <div className="opp-row-actions">
                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setTab('opps')}>去清</button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {!!scanStatus?.report?.length && !timingScanning && (
            <div className="panel">
              <div className="panel-title" style={{ marginBottom: 8 }}>触线扫描诊断</div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>
                触发 {scanStatus.signals_found ?? 0} 条
                {scanStatus.finished_at ? ` · ${String(scanStatus.finished_at).slice(11, 19)}` : ''}
              </div>
              <div style={{ maxHeight: 220, overflow: 'auto', fontSize: 13 }}>
                {(scanStatus.report || []).map((row, i) => {
                  const skipped = row.expiries_skipped || []
                  const scanned = row.expiries_scanned || []
                  const issues: string[] = []
                  if (row.bars_insufficient) issues.push(`K不足 ${row.bars_insufficient}`)
                  if (row.no_history) issues.push(`无历史 ${row.no_history}`)
                  if (row.not_touching) issues.push(`未触线 ${row.not_touching}`)
                  if (row.in_cooldown) issues.push(`冷却 ${row.in_cooldown}`)
                  if (row.ema_partial_hits) issues.push(`近似EMA ${row.ema_partial_hits}`)
                  if (skipped.length) issues.push(`未扫到期 ${skipped.slice(0, 3).join(',')}${skipped.length > 3 ? '…' : ''}`)
                  return (
                    <div key={`${row.symbol}-${row.side}-${i}`} style={{
                      display: 'flex', flexWrap: 'wrap', gap: '4px 12px',
                      padding: '6px 0', borderBottom: '1px solid var(--border, #333)',
                    }}>
                      <strong>{row.symbol}</strong>
                      <span>{row.side}</span>
                      {row.spot != null && <span>现价 {row.spot}</span>}
                      {row.contracts != null && <span>合约 {row.contracts}</span>}
                      {row.signals != null && <span style={{ color: (row.signals || 0) > 0 ? 'var(--green, #3d8)' : undefined }}>信号 {row.signals}</span>}
                      {scanned.length > 0 && <span title={scanned.join(', ')}>已扫 {scanned.length} 到期</span>}
                      {row.strike_lo != null && row.strike_hi != null && (
                        <span>K [{row.strike_lo}–{row.strike_hi}]</span>
                      )}
                      {issues.map((t, j) => <span key={j} style={{ color: 'var(--orange, #e90)' }}>{t}</span>)}
                      {row.note && <span style={{ opacity: 0.85, width: '100%' }}>{row.note}</span>}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div className="panel">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <div className="panel-title" style={{ margin: 0 }}>优先可下单</div>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setTab('opps'); setOppCatFilter('PRIORITY') }}>更多</button>
            </div>
            {allOppRows.filter(r => r.kind === 'OPEN' && r.trade_tier === 'PRIORITY' && !(putBlocked && r.side === 'PUT')).slice(0, 3).length === 0 ? (
              <EmptyState
                title="暂无优先档"
                description="需要可交易高分 + 触线确认。可先扫描，或查看可排单/观察。"
              >
                <button type="button" className="btn btn-primary btn-sm" disabled={oppScanning}
                  onClick={() => { setTab('opps'); handleOpportunityScan() }}>扫描</button>
                <button type="button" className="btn btn-secondary btn-sm"
                  onClick={() => { setTab('opps'); setOppCatFilter('QUEUE') }}>看可排单</button>
              </EmptyState>
            ) : (
              allOppRows.filter(r => r.kind === 'OPEN' && r.trade_tier === 'PRIORITY' && !(putBlocked && r.side === 'PUT')).slice(0, 3).map((r, i) => {
                const skHome = signalKindLabel(resolveOppSignalKind(r))
                return (
                <div key={r.id} className="opp-row" style={i === rowCursor && tab === 'home' ? { background: 'var(--green-dim)' } : undefined}>
                  <div className="opp-row-main">
                    <div className="opp-row-title">
                      <Badge color={skHome.color} title={skHome.title}>{skHome.text}</Badge>
                      {r.symbol}
                      <span style={{ fontWeight: 500, color: r.side === 'PUT' ? 'var(--green)' : 'var(--purple)' }}>
                        {r.side === 'PUT' ? '卖Put' : '卖Call'}
                      </span>
                    </div>
                    <div className="opp-row-meta">
                      <span>年化 <b style={{ color: 'var(--green)' }}>{r.annualized != null ? `${fmt(r.annualized, 1)}%` : '—'}</b></span>
                      <span>日租 {r.daily_rent != null ? fmt(r.daily_rent, 2) : '—'}</span>
                      <span>{DTE_BUCKET_META[r.dte_bucket]?.label}</span>
                    </div>
                  </div>
                  <div className="opp-row-actions">
                    <button type="button" className="btn btn-secondary btn-sm"
                      onClick={() => {
                        const mf = oppMemoFields(r)
                        copyOrderMemo({
                          symbol: r.symbol, side: r.side || 'PUT', action: 'SELL',
                          contract_code: r.contract_code, strike: r.strike, expiry: r.expiry,
                          price: mf.price, price_kind: mf.price_kind,
                        })
                      }}>备忘</button>
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => openOppRegister(r)}>登记</button>
                  </div>
                </div>
                )
              })
            )}
          </div>

          <div className="panel">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <div className="panel-title" style={{ margin: 0 }}>待登记</div>
              {pendingQueue.length > 0 && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => { savePendingQueue([]); setPendingQueue([]) }}>清空</button>
              )}
            </div>
            {pendingQueue.length === 0 ? (
              <div style={{ fontSize: 14, color: 'var(--text-secondary)' }}>队列空。机会点「登记」会自动入队。</div>
            ) : (
              pendingQueue.map(p => (
                <div key={p.id} className={`opp-row ${pendingLeaving === p.id ? 'pending-done' : ''}`}>
                  <div className="opp-row-main">
                    <div className="opp-row-title">
                      {p.symbol}
                      <span style={{ fontWeight: 500 }}>{p.trade_type.includes('BUY') ? '平' : '卖'}{p.side}</span>
                    </div>
                    <div className="opp-row-meta mono">
                      <span>{p.contract_code || '—'}</span>
                      {p.strike != null && <span>${fmt(p.strike)}</span>}
                      {p.price != null && <span>参考 {p.price}</span>}
                      <span>×{p.qty ?? 1}</span>
                    </div>
                  </div>
                  <div className="opp-row-actions">
                    <button type="button" className="btn btn-secondary btn-sm"
                      onClick={() => copyOrderMemo({
                        symbol: p.symbol, side: p.side,
                        action: p.trade_type.startsWith('BUY') ? 'BUY' : 'SELL',
                        contract_code: p.contract_code, strike: p.strike, expiry: p.expiry,
                        qty: p.qty, price: p.price, note: p.note,
                      })}>备忘</button>
                    <button type="button" className="btn btn-primary btn-sm"
                      onClick={() => setTradeModal({
                        initial: {
                          symbol: p.symbol, trade_type: p.trade_type,
                          contract_code: p.contract_code || '',
                          strike: p.strike != null ? String(p.strike) : '',
                          expiry: p.expiry || '',
                          price: p.price != null ? String(p.price) : '',
                          qty: String(p.qty ?? 1),
                          note: p.note || '',
                        },
                        status: p.trade_type === 'SELL_PUT' ? 'IDLE'
                          : p.trade_type === 'SELL_CALL' ? 'HOLDING'
                            : p.trade_type === 'BUY_PUT_CLOSE' ? 'CSP_OPEN' : 'CC_OPEN',
                        cycleId: p.cycle_id,
                      })}>登记</button>
                    <button type="button" className="btn btn-ghost btn-sm" title="完成/移除"
                      onClick={() => {
                        setPendingLeaving(p.id)
                        setTimeout(() => {
                          removePendingReg(p.id)
                          setPendingQueue(loadPendingQueue())
                          setPendingLeaving(null)
                        }, 320)
                      }}>完成</button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* ── 看板 ── */}
      {tab === 'board' && (
        <div>
          {/* 今日行动 */}
          {(() => {
            const checks = Object.values(openChecks)
            const closeSyms = new Set(checks.filter(i => {
              const c = (i.action_code || '').toUpperCase()
              return c === 'CLOSE' || c === 'REPLACE' || i.profit_hit || (i.action_hint || '').includes('换仓')
            }).map(i => i.symbol))
            const rollSyms = new Set(checks.filter(i => {
              const c = (i.action_code || '').toUpperCase()
              return c === 'ROLL' || c === 'ROLL_ADJUST' || c === 'PREPARE_ASSIGN'
                || (i.action_hint || '').includes('Roll')
            }).map(i => i.symbol))
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
                <span style={{ fontSize: 14, fontWeight: 700 }}>⚡ 今日行动</span>
                {items.filter(([, , n]) => n > 0).map(([key, label, n, color]) => (
                  <button key={key} onClick={() => setActionFilter(f => f === key ? null : key)}
                    style={{
                      padding: '3px 12px', borderRadius: 12, fontSize: 13, cursor: 'pointer', fontWeight: 600,
                      background: actionFilter === key ? C[color] : C[color] + '18',
                      color: actionFilter === key ? '#111' : C[color],
                      border: `1px solid ${C[color]}66`,
                    }}>
                    {label} {n}
                  </button>
                ))}
                {actionFilter && (
                  <button className="btn" style={{ fontSize: 13, padding: '2px 10px' }} onClick={() => setActionFilter(null)}>清除过滤</button>
                )}
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>点击类别过滤左侧标的</span>
              </div>
            )
          })()}

          {targets.filter(t => t.enabled).length === 0 && (
            <div style={{ color: 'var(--text-secondary)', fontSize: 14, padding: '20px 0' }}>
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
              enabled = enabled.filter(t => t.symbol.toUpperCase() === q)
            }
            if (actionFilter) {
              const checks = Object.values(openChecks)
              const match = (t: WheelTarget): boolean => {
                if (actionFilter === 'close') {
                  return checks.some(i => {
                    if (i.symbol !== t.symbol) return false
                    const c = (i.action_code || '').toUpperCase()
                    return c === 'CLOSE' || c === 'REPLACE' || i.profit_hit
                      || (i.action_hint || '').includes('换仓')
                  })
                }
                if (actionFilter === 'roll') {
                  return checks.some(i => {
                    if (i.symbol !== t.symbol) return false
                    const c = (i.action_code || '').toUpperCase()
                    return c === 'ROLL' || c === 'ROLL_ADJUST' || c === 'PREPARE_ASSIGN'
                      || (i.action_hint || '').includes('Roll')
                  })
                }
                if (actionFilter === 'uncovered') return (t.active_cycles || []).some(c => c.status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3)
                if (actionFilter === 'idle') return (t.idle_days ?? 0) >= 5
                return true
              }
              enabled = enabled.filter(match)
            }
            if (enabled.length === 0) return (
              <div style={{ color: 'var(--text-secondary)', fontSize: 14, padding: '16px 0' }}>
                没有匹配的标的
                {(actionFilter || symbolQuery) && (
                  <button className="btn" style={{ fontSize: 13, padding: '2px 10px', marginLeft: 8 }}
                    onClick={() => { setActionFilter(null); setSymbolQuery('') }}>清除筛选</button>
                )}
              </div>
            )
            const sel = enabled.find(t => t.symbol === selectedSymbol) || enabled[0]
            const selCycles = [...(sel.active_cycles || [])].sort(
              (a, b) => (CYCLE_STATUS_ORDER[a.status] ?? 9) - (CYCLE_STATUS_ORDER[b.status] ?? 9))
            // 默认展示排序第一的轮子明细;选中无效时回退第一位
            const detailCycleId = (
              selectedCycleId && selCycles.some(c => c.id === selectedCycleId)
            ) ? selectedCycleId : (selCycles[0]?.id ?? null)
            return (
              <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                {/* ── 左:标的列表(窄屏自动占满整行堆叠) ── */}
                <div className="card" style={{ flex: '1 0 250px', maxWidth: 320, minWidth: 220, padding: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px 8px' }}>
                    <span style={{ fontSize: 13, color: 'var(--text-secondary)', flexShrink: 0 }}>标的({enabled.length})</span>
                    <select value={symbolQuery} onChange={e => {
                      setSymbolQuery(e.target.value)
                      if (e.target.value) setSelectedSymbol(e.target.value)
                    }}
                      style={{
                        width: '100%', minWidth: 0, padding: '2px 8px', fontSize: 13,
                        background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                        borderRadius: 4, color: 'var(--text)',
                      }}>
                      <option value="">全部</option>
                      {targets.filter(t => t.enabled).map(t => (
                        <option key={t.symbol} value={t.symbol}>{t.symbol}</option>
                      ))}
                    </select>
                  </div>
                  {enabled.map(t => {
                    const cs = t.active_cycles || []
                    const isSel = t.symbol === sel.symbol
                    const hasWarn = cs.some(c => (c.open_dte ?? 99) <= 7 || openChecks[c.id]?.itm)
                    const hasProfit = cs.some(c => openChecks[c.id]?.profit_hit)
                    const isIdle = t.idle_days != null && t.idle_days >= 5
                    return (
                      <div key={t.symbol} onClick={() => {
                        setSelectedSymbol(t.symbol)
                        setEditParams(null)
                        // 切换标的时默认选中该标的排序第一的轮子
                        const sorted = [...(t.active_cycles || [])].sort(
                          (a, b) => (CYCLE_STATUS_ORDER[a.status] ?? 9) - (CYCLE_STATUS_ORDER[b.status] ?? 9))
                        setSelectedCycleId(sorted[0]?.id ?? null)
                      }} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '9px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 2,
                        background: isSel ? 'var(--accent)22' : 'transparent',
                        borderLeft: `3px solid ${isSel ? 'var(--accent)' : 'transparent'}`,
                      }}>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 700, fontSize: 15, display: 'flex', alignItems: 'center', gap: 5 }}>
                            {t.symbol}
                            {hasWarn && <span title="临期或ITM" style={{ fontSize: 13 }}>⚠</span>}
                            {hasProfit && <span title="浮盈达标,可平仓" style={{ fontSize: 13 }}>💰</span>}
                            {isIdle && <span title={`空转 ${t.idle_days} 天`} style={{ fontSize: 13 }}>⏸</span>}
                          </div>
                          <div style={{ marginTop: 3 }}>
                            <TargetPriceStrip
                              size="sm"
                              spot={t.spot}
                              floor={t.floor_price}
                              suggested={t.suggested_floor}
                              suggestedDelta={t.suggested_floor_delta}
                            />
                          </div>
                          <div style={{ fontSize: 12, marginTop: 2, color: 'var(--text-secondary)' }}>
                            <span style={{ color: targetCapital(cs) > 0 ? '#38bdf8' : undefined }}>
                              占用 ${fmtMoney(targetCapital(cs))}
                              {(t.max_capital ?? 0) > 0 && `/${fmtMoney(t.max_capital)}`}
                            </span>
                            <span style={{ marginLeft: 8, color: (premiumBySymbol[t.symbol] || 0) > 0 ? '#4ade80' : undefined }}>
                              权利金 ${fmtMoney(premiumBySymbol[t.symbol] || 0)}
                            </span>
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
                          {cs.length === 0 ? (
                            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>未开轮</span>
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
                        <span style={{ fontWeight: 700, fontSize: 18 }}>{sel.symbol}</span>
                        {sel.volatility_brief && (sel.volatility_brief.atm_iv != null || sel.volatility_brief.hv20 != null) && (
                          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}
                            title={`预期IV:最近一次ATM隐含波动率快照(${sel.volatility_brief.iv_date || '--'});实际IV:20日历史波动率;IVR:IV在自身252日历史中的百分位${sel.volatility_brief.iv_rank_source === 'hv_proxy' ? '(IV历史不足,HV近似)' : ''}`}>
                            IV <b style={{ color: 'var(--text)' }}>{sel.volatility_brief.atm_iv != null ? sel.volatility_brief.atm_iv.toFixed(1) : '--'}</b>
                            {' / HV '}<b style={{ color: 'var(--text)' }}>{sel.volatility_brief.hv20 != null ? sel.volatility_brief.hv20.toFixed(1) : '--'}</b>
                            {sel.volatility_brief.iv_rank != null && <>
                              {' · IVR '}
                              <b style={{ color: sel.volatility_brief.iv_rank >= 70 ? '#f87171' : sel.volatility_brief.iv_rank >= 50 ? '#fb923c' : 'var(--text)' }}>
                                {sel.volatility_brief.iv_rank.toFixed(0)}
                              </b>
                              {sel.volatility_brief.iv_rank_source === 'hv_proxy' && <span style={{ fontSize: 12 }}>≈</span>}
                            </>}
                          </span>
                        )}
                        {!editParams && (
                          <>
                            <TargetPriceStrip
                              size="lg"
                              spot={sel.spot}
                              floor={sel.floor_price}
                              suggested={sel.suggested_floor}
                              suggestedDelta={sel.suggested_floor_delta}
                            />
                            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                              Δ <b style={{ color: 'var(--text)' }}>{sel.delta_min}~{sel.delta_max}</b>
                              {' · '}DTE <b style={{ color: 'var(--text)' }}>{sel.dte_min}~{sel.dte_max}</b>
                              {' · '}年化≥<b style={{ color: 'var(--text)' }}>{sel.min_annualized}%</b>
                            </span>
                            <button title="修改找货参数" onClick={() => setEditParams({
                              floor_price: String(sel.floor_price), delta_min: String(sel.delta_min),
                              delta_max: String(sel.delta_max), dte_min: String(sel.dte_min),
                              dte_max: String(sel.dte_max), min_annualized: String(sel.min_annualized),
                            })} style={{
                              border: '1px solid var(--border)', background: 'transparent', cursor: 'pointer',
                              borderRadius: 6, padding: '1px 8px', fontSize: 13, color: 'var(--accent)',
                            }}>✎ 编辑</button>
                          </>
                        )}
                        {sel.idle_days != null && sel.idle_days >= 5 && !editParams && (
                          <span style={{ fontSize: 13, color: '#fb923c' }}>⏸ 空转 {sel.idle_days} 天</span>
                        )}
                      </div>
                      {!editParams && (
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button className="btn btn-primary" style={{ fontSize: 13, padding: '4px 14px' }}
                            disabled={suggestLoading} onClick={() => handleSuggest(sel.symbol, 'put')}>找 Put</button>
                          <button className="btn" style={{ fontSize: 13, padding: '4px 14px', color: 'var(--accent)' }}
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
                        <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}
                          title="真被指派时最多愿付的股价;此后卖Put的strike不得超过此价">
                          愿接价$
                          <input type="number" step="any" value={editParams.floor_price} style={{
                            display: 'block', width: 90, padding: '4px 6px', marginTop: 2,
                            background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                            borderRadius: 4, color: 'var(--text)', fontSize: 13,
                          }} onChange={e => setEditParams(f => f ? { ...f, floor_price: e.target.value } : f)} />
                        </label>
                        {([
                          ['Δ min', 'delta_min', DELTA_OPTS],
                          ['Δ max', 'delta_max', DELTA_OPTS],
                          ['DTE min', 'dte_min', DTE_OPTS],
                          ['DTE max', 'dte_max', DTE_OPTS],
                          ['年化≥%', 'min_annualized', ANN_OPTS],
                        ] as [string, keyof NonNullable<typeof editParams>, number[]][]).map(([lab, key, opts]) => (
                          <label key={key} style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                            {lab}
                            <SelectNum
                              value={editParams[key]}
                              options={opts}
                              style={{
                                display: 'block', width: 72, padding: '4px 6px', marginTop: 2,
                                background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                                borderRadius: 4, color: 'var(--text)', fontSize: 13,
                              }}
                              onChange={v => setEditParams(f => f ? { ...f, [key]: v } : f)}
                            />
                          </label>
                        ))}
                        <button className="btn btn-primary" style={{ fontSize: 13, padding: '4px 14px' }}
                          disabled={savingParams} onClick={async () => {
                            setSavingParams(true)
                            setError(null)
                            try {
                              await updateWheelTarget(sel.symbol, {
                                floor_price: parseFloat(editParams.floor_price),
                                delta_min: parseFloat(editParams.delta_min),
                                delta_max: parseFloat(editParams.delta_max),
                                dte_min: parseInt(editParams.dte_min),
                                dte_max: parseInt(editParams.dte_max),
                                min_annualized: parseFloat(editParams.min_annualized),
                                floor_change_source: 'manual',
                              })
                              setEditParams(null)
                              await loadAll()
                            } catch (e: any) {
                              setError('参数保存失败:' + e.message)
                            } finally {
                              setSavingParams(false)
                            }
                          }}>{savingParams ? '保存中...' : '保存'}</button>
                        <button className="btn" style={{ fontSize: 13, padding: '4px 14px' }}
                          onClick={() => setEditParams(null)}>取消</button>
                      </div>
                    )}
                  </div>

                  {/* 轮子列表(窄列) + 交易明细(右) */}
                  <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <div style={{ flex: '1 1 420px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {selCycles.length === 0 && (
                    <div className="card" style={{ padding: '20px 16px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
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
                        onClick={() => setSelectedCycleId(c.id)}
                        style={{
                          padding: '8px 12px', display: 'flex', gap: 12, alignItems: 'center',
                          borderLeft: `3px solid ${STAGE_COLORS[status]}`,
                          cursor: 'pointer',
                          outline: detailCycleId === c.id ? '1px solid var(--accent)' : 'none',
                          background: detailCycleId === c.id ? 'var(--accent)11' : undefined,
                        }}>
                        {/* 左:信息区 */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          {/* 行1:流程 + 徽章 + 汇总 chips */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {selCycles.length > 1 && (
                              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)' }}>#{idx + 1}</span>
                            )}
                            <StageIndicator status={status} />
                            {check?.itm && <Badge color="red">ITM</Badge>}
                            {dteVal != null && dteVal <= 7 && <Badge color="orange">临期</Badge>}
                            {check?.profit_hit && <Badge color="green">达标</Badge>}
                            {check?.capital_tight && (
                              <Badge color="orange" title={check.capital_util_pct != null ? `利用率 ${check.capital_util_pct}%` : '组合资金占用偏紧'}>
                                资金紧
                              </Badge>
                            )}
                            {check?.strike_above_floor && (
                              <Badge color="red" title="行权价高于愿接最高价,不宜等接货">{'超过愿接价'}</Badge>
                            )}
                            {check?.profit_pct != null && check.profit_pct < 0 && !check.profit_hit && (
                              <Badge color="orange" title="买回价高于开仓权利金">浮亏</Badge>
                            )}
                            {check?.would_open_today === 'no' && (
                              <Badge color="red" title={(check.would_open_reasons || []).join(';') || '以今天纪律不会新开此腿'}>
                                规则已否决
                              </Badge>
                            )}
                            {check?.would_open_today === 'yes' && check.profit_pct != null && check.profit_pct < 0 && (
                              <Badge color="green" title={(check.would_open_reasons || []).join(';') || '纪律仍接纳'}>
                                规则仍会开
                              </Badge>
                            )}
                            {check?.would_open_today === 'caution' && (
                              <Badge color="orange" title={(check.would_open_reasons || []).join(';')}>纪律谨慎</Badge>
                            )}
                            {(check?.action_code === 'PREPARE_ASSIGN' || (check?.itm && check?.assign_checklist)) && (
                              <Badge color="red" title="见管理弹窗接货/交货清单">接货清单</Badge>
                            )}
                            {check?.action_hint && !check.profit_hit && (
                              <Badge color={check.deep_itm ? 'red' : check.low_yield && !check.roll_21dte ? 'blue' : 'orange'}
                                title={(check.reasons || []).join(';')}>
                                👉 {check.action_hint}
                                {check.decision_confidence != null ? ` · ${check.decision_confidence}%` : ''}
                              </Badge>
                            )}
                            {status === 'HOLDING' && (c.uncovered_days ?? 0) >= 3 && (
                              <Badge color="orange" title="持股但未挂 Call,theta 收入在流失">🪑 裸奔 {c.uncovered_days} 天</Badge>
                            )}
                            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
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
                                  padding: '0 7px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                                  background: (c.open_option_type === 'PUT' ? '#38bdf8' : '#a78bfa') + '22',
                                  color: c.open_option_type === 'PUT' ? '#38bdf8' : '#a78bfa',
                                  border: `1px solid ${c.open_option_type === 'PUT' ? '#38bdf8' : '#a78bfa'}55`,
                                }}>{c.open_option_type}</span>
                                <b style={{ fontSize: 14 }}>${fmt(c.open_strike)}</b>
                                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
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
                                  <span style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>止盈 {profitPct}/{profitTarget}%</span>
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
                            <button className="btn btn-primary" style={{ fontSize: 13, padding: '3px 0', width: '100%' }}
                              disabled={suggestLoading} onClick={() => handleSuggest(sel.symbol, 'put', c.id)}>找 Put</button>
                          )}
                          {status === 'HOLDING' && (
                            <button className="btn btn-primary" style={{ fontSize: 13, padding: '3px 0', width: '100%' }}
                              disabled={suggestLoading} onClick={() => handleSuggest(sel.symbol, 'call', c.id)}>找 Call</button>
                          )}
                          {hasOpen && check?.profit_hit && (
                            <button className="btn" style={{ fontSize: 13, padding: '3px 0', width: '100%', color: '#4ade80', fontWeight: 700 }}
                              title="先看决策建议,再决定是否登记"
                              onClick={() => {
                                // 与机会列表一致:先决策弹窗,再在弹窗内登记买回
                                if (check) {
                                  setManageCompare(check)
                                  return
                                }
                                setTradeModal({
                                  initial: {
                                    symbol: sel.symbol,
                                    trade_type: status === 'CSP_OPEN' ? 'BUY_PUT_CLOSE' : 'BUY_CALL_CLOSE',
                                    price: '',
                                    qty: String(c.open_qty || 1),
                                    contract_size: String(c.open_contract_size || 100),
                                  },
                                  status, cycleId: c.id,
                                })
                              }}>
                              💰 平仓
                            </button>
                          )}
                          {hasOpen && check && !check.profit_hit && (check.action_priority ?? 9) <= 3 && (
                            <button className="btn" style={{ fontSize: 13, padding: '3px 0', width: '100%', color: '#fb923c', fontWeight: 600 }}
                              title="有管理建议,先打开决策弹窗"
                              onClick={() => setManageCompare(check)}>
                              管理
                            </button>
                          )}
                          {hasOpen && (
                            <button className="btn" style={{ fontSize: 13, padding: '3px 0', width: '100%' }}
                              disabled={rollLoading} onClick={() => handleRoll(c.id)}>看 Roll</button>
                          )}
                          <button className="btn" style={{ fontSize: 13, padding: '3px 0', width: '100%' }}
                            onClick={() => setTradeModal({ initial: { symbol: sel.symbol }, status, cycleId: c.id })}>
                            登记交易
                          </button>
                        </div>
                      </div>
                    )
                  })}
                  </div>

                  {/* 交易明细面板:默认展示排序第一的轮子,点击其它轮子切换 */}
                  <div style={{ flex: detailCycleId ? '0 0 400px' : '0 0 200px', minWidth: 0 }}>
                    {(() => {
                      const dc = selCycles.find(c => c.id === detailCycleId)
                      if (selCycles.length === 0) return null
                      if (!dc) return null
                      const cycleTrades = trades
                        .filter(t => t.cycle_id === dc.id)
                        .sort((a, b) => (a.traded_at < b.traded_at ? -1 : 1))
                      const detailIdx = selCycles.findIndex(c => c.id === dc.id)
                      return (
                        <div className="card" style={{ padding: '10px 14px' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <span style={{ fontSize: 14, fontWeight: 700 }}>
                              交易明细
                              <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--text-secondary)', marginLeft: 8 }}>
                                {selCycles.length > 1 ? `#${detailIdx + 1} · ` : ''}
                                {STAGE_LABELS[dc.status]} · 始于 {fmtDate(dc.started_at)} · {cycleTrades.length} 笔
                              </span>
                            </span>
                            {selCycles.length > 1 && (
                              <span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
                                点击左侧轮子切换
                              </span>
                            )}
                          </div>
                          {cycleTrades.length === 0 && (
                            <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '8px 0' }}>暂无交易记录</div>
                          )}
                          {cycleTrades.map(t => {
                            const cf = tradeCashFlow(t)
                            return (
                              <div key={t.id} style={{
                                display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8,
                                padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 13,
                              }}>
                                <div style={{ minWidth: 0 }}>
                                  <div>
                                    <b>{TRADE_LABELS[t.trade_type]}</b>
                                    {t.is_roll && (
                                      <span style={{ padding: '0 6px', borderRadius: 7, fontSize: 11, fontWeight: 700, background: '#a78bfa22', color: '#a78bfa', border: '1px solid #a78bfa55', marginLeft: 6 }}
                                        title="同日买回+再卖出,识别为一次 Roll">Roll</span>
                                    )}
                                    <span style={{ color: 'var(--text-secondary)', fontSize: 13, marginLeft: 8 }}>{fmtDate(t.traded_at)}</span>
                                  </div>
                                  <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {t.strike != null && <>${fmt(t.strike)}</>}
                                    {t.expiry && <>{' · '}{String(t.expiry).slice(5, 10)}到期</>}
                                    {t.qty > 0 && t.price > 0 && <>{' · '}{t.qty}{['BUY_SHARES', 'SELL_SHARES'].includes(t.trade_type) ? '股' : '张'} × ${fmt(t.price)}</>}
                                    {t.fee > 0 && <>{' · '}费 {t.fee}</>}
                                    {t.note && <>{' · '}{t.note}</>}
                                  </div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                                  {cf != null && (
                                    <span style={{ fontSize: 13, fontWeight: 700, color: cf >= 0 ? '#4ade80' : '#f87171', whiteSpace: 'nowrap' }}>
                                      {cf >= 0 ? '+' : ''}{fmtMoney(Math.abs(cf)) === '0' ? cf.toFixed(2) : (cf < 0 ? '-' : '') + fmtMoney(Math.abs(cf))}
                                    </span>
                                  )}
                                  <button className="btn" style={{ fontSize: 13, padding: '1px 7px' }}
                                    onClick={() => setEditTrade(t)}>✎</button>
                                  <button className="btn" style={{ fontSize: 13, padding: '1px 7px', color: '#f87171' }}
                                    onClick={() => handleDeleteTrade(t)}>🗑</button>
                                </div>
                              </div>
                            )
                          })}
                          <div style={{ display: 'flex', gap: 14, fontSize: 13, color: 'var(--text-secondary)', paddingTop: 8 }}>
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

          {/* 找 Put/Call 详情走页面级 Drawer */}
        </div>
      )}

      {/* ── 机会扫描 ── */}
      {tab === 'opps' && (
        <div className="tab-panel">
          <div className="panel">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <span className="panel-title" style={{ margin: 0, flex: 1 }}>
                机会
                {serverOpps?.headline && (
                  <span style={{ fontWeight: 400, fontSize: 13, color: 'var(--text-secondary)', marginLeft: 10 }}>
                    {serverOpps.headline}
                    {serverOpps.summary != null && (
                      <> · 可做 {serverOpps.summary.actionable} · 双满足 {serverOpps.summary.dual}</>
                    )}
                  </span>
                )}
              </span>
              <button type="button" className="btn btn-primary btn-sm" disabled={oppScanning}
                onClick={() => handleOpportunityScan()}>{oppScanning ? '扫描中…' : '扫描'}</button>
              <button type="button" className="btn btn-secondary btn-sm" disabled={oppScanning}
                title="清空期权链缓存并重扫" onClick={() => handleOpportunityScan({ force: true })}>强制</button>
              <button type="button" className="btn btn-secondary btn-sm" disabled={oppScanning}
                title="推送 TG Top3" onClick={() => handleOpportunityScan({ push: true })}>
                {poolPushing ? '推送中' : '推TG'}
              </button>
            </div>

            {oppScanning && (
              <div className="scan-progress">
                <div className="scan-progress-row">
                  <span className="label">高分</span>
                  <div className="bar">
                    <div
                      className={`fill ${poolScanLoading || serverOppsLoading ? (scanProgress?.contract_n ? '' : 'pulse') : ''}`}
                      style={{
                        width: (() => {
                          if (!(poolScanLoading || serverOppsLoading)) return '100%'
                          const ti = scanProgress?.target_i || 0
                          const tn = scanProgress?.target_n || 0
                          const ci = scanProgress?.contract_i || 0
                          const cn = scanProgress?.contract_n || 0
                          if (tn > 0) {
                            const base = ((ti - 1) / tn) * 100
                            const slice = cn > 0 ? (ci / cn) * (100 / tn) : 0
                            return `${Math.min(99, Math.max(4, base + slice))}%`
                          }
                          return '40%'
                        })(),
                      }}
                    />
                  </div>
                  <span>
                    {poolScanLoading || serverOppsLoading
                      ? (scanProgress?.target_n
                        ? `${scanProgress.target_i || 0}/${scanProgress.target_n}`
                        : '…')
                      : (poolScan ? `${poolScan.opportunities.length}` : '—')}
                  </span>
                </div>
                {(poolScanLoading || serverOppsLoading) && (
                  <div className="scan-progress-detail">
                    <ScanProgressDetail
                      symbol={scanProgress?.symbol}
                      side={scanProgress?.side}
                      expiry={scanProgress?.expiry}
                      contract_i={scanProgress?.contract_i}
                      contract_n={scanProgress?.contract_n}
                      target_i={scanProgress?.target_i}
                      target_n={scanProgress?.target_n}
                      fallback={scanProgress?.message || '准备扫描…'}
                    />
                  </div>
                )}
                <div className="scan-progress-row">
                  <span className="label">触线</span>
                  <div className="bar">
                    <div
                      className={`fill ${timingScanning ? (scanStatus?.contract_n ? '' : 'pulse') : ''}`}
                      style={{
                        width: (() => {
                          if (!timingScanning) return '100%'
                          const ti = scanStatus?.target_i || 0
                          const tn = scanStatus?.target_n || 0
                          const ci = scanStatus?.contract_i || 0
                          const cn = scanStatus?.contract_n || 0
                          if (tn > 0) {
                            const base = ((Math.max(ti, 1) - 1) / tn) * 100
                            const slice = cn > 0 ? (ci / cn) * (100 / tn) : 0
                            return `${Math.min(99, Math.max(4, base + slice))}%`
                          }
                          return '40%'
                        })(),
                      }}
                    />
                  </div>
                  <span>
                    {timingScanning
                      ? (scanStatus?.target_n ? `${scanStatus.target_i || 0}/${scanStatus.target_n}` : '…')
                      : (scanStatus?.signals_found ?? '—')}
                  </span>
                </div>
                {timingScanning && (
                  <div className="scan-progress-detail">
                    <ScanProgressDetail
                      symbol={scanStatus?.symbol}
                      side={scanStatus?.side}
                      expiry={scanStatus?.expiry}
                      contract_i={scanStatus?.contract_i}
                      contract_n={scanStatus?.contract_n}
                      target_i={scanStatus?.target_i}
                      target_n={scanStatus?.target_n}
                      fallback={scanStatus?.message || '触线扫描启动…'}
                    />
                  </div>
                )}
              </div>
            )}

            {!!scanStatus?.report?.length && !timingScanning && (
              <div className="banner info" style={{ marginBottom: 10, flexDirection: 'column', alignItems: 'stretch', gap: 6 }}>
                <div style={{ fontWeight: 600 }}>触线诊断 · 触发 {scanStatus.signals_found ?? 0}</div>
                <div style={{ fontSize: 13, maxHeight: 140, overflow: 'auto' }}>
                  {(scanStatus.report || []).filter(r => (r.signals || 0) === 0 || (r.expiries_skipped || []).length > 0 || r.note).slice(0, 12).map((row, i) => (
                    <div key={i} style={{ marginBottom: 4 }}>
                      <strong>{row.symbol}</strong> {row.side}
                      {(row.expiries_skipped || []).length > 0 && (
                        <span> · 未扫 {(row.expiries_skipped || []).slice(0, 4).join(',')}</span>
                      )}
                      {(row.bars_insufficient || 0) > 0 && <span> · K不足{row.bars_insufficient}</span>}
                      {row.note && <span> · {row.note}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* U4 主筛选: 档位 + 方向；高级进抽屉 */}
            <div className="opp-filter-primary">
              <div className="chip-row">
                {([
                  ['PRIORITY', `优先 ${oppCounts.priority}`],
                  ['QUEUE', `可排 ${oppCounts.queue}`],
                  ['MANAGE', `该管 ${oppCounts.manage}`],
                  ['WATCH', `观察 ${oppCounts.watch}`],
                ] as const).map(([key, label]) => (
                  <button key={key} type="button" className={`chip ${oppCatFilter === key ? 'active' : ''}`}
                    onClick={() => { setOppCatFilter(key); setKilledDiag(false) }}>{label}</button>
                ))}
                {oppCounts.killed > 0 && (
                  <button type="button" className={`chip orange ${oppCatFilter === 'KILLED' ? 'active' : ''}`}
                    onClick={() => {
                      if (oppCatFilter === 'KILLED') {
                        setOppCatFilter('PRIORITY')
                        setKilledDiag(false)
                      } else {
                        showKilledOpps()
                      }
                    }}>
                    杀掉 {oppCounts.killed}
                  </button>
                )}
              </div>
              <div className="chip-row">
                {([['all', '全部'], ['PUT', 'Put'], ['CALL', 'Call']] as const).map(([k, l]) => (
                  <button key={k} type="button" className={`chip ${oppSideFilter === k ? 'active' : ''}`}
                    onClick={() => setOppSideFilter(k)}>{l}</button>
                ))}
              </div>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setFilterOpen(true)}>
                更多筛选
                {(oppBucketFilter !== 'core_extend' || !oppHideBlocked || !oppHideUntradeable || oppOnlySelected || !oppStateAware) ? ' ·' : ''}
              </button>
            </div>

            {(killedDiag || oppCatFilter === 'KILLED') && oppCounts.killed > 0 && (
              <div className="banner warn">
                <span style={{ flex: 1 }}>
                  不可交易 {oppCounts.killed} 条
                  {Object.keys(oppCounts.killBreakdown).length > 0
                    ? `：${Object.entries(oppCounts.killBreakdown).map(([k, n]) => `${k} ${n}`).join(' · ')}`
                    : ''}
                  {oppCatFilter === 'KILLED' ? '（当前列表）' : ''}
                </span>
                {oppCatFilter !== 'KILLED' ? (
                  <button type="button" className="btn btn-sm btn-secondary" onClick={showKilledOpps}>显示</button>
                ) : (
                  <button type="button" className="btn btn-sm btn-secondary" onClick={() => {
                    setOppCatFilter('PRIORITY'); setKilledDiag(false)
                  }}>返回优先</button>
                )}
              </div>
            )}
            {putBlocked && <div className="banner error">行权压力过高：已隐藏新开 Put</div>}

            {filteredOppRows.length === 0 && !oppScanning ? (
              <EmptyState
                title={
                  !serverOpps && !poolScan
                    ? '还没扫描'
                    : putBlocked && oppSideFilter !== 'CALL'
                      ? '组合已停新 Put'
                      : oppCatFilter === 'PRIORITY' ? '暂无优先机会'
                        : oppCatFilter === 'MANAGE' ? '暂无管理项'
                          : oppCatFilter === 'KILLED' ? '暂无被杀机会'
                            : '筛选后没有结果'
                }
                description={
                  !serverOpps && !poolScan
                    ? '点「扫描」拉高分+触线机会；成交后回这里登记。'
                    : putBlocked && oppSideFilter !== 'CALL'
                      ? '行权压力/利用率过高。可改看 Call、处理持仓，或在设置调低压力档。'
                      : oppCatFilter === 'PRIORITY'
                        ? '优先 = 可交易高分 ∩ 触线。可改看「可排」、放宽筛选，或再扫一遍。'
                        : oppCatFilter === 'KILLED'
                          ? '当前没有不可交易机会。'
                          : '当前筛选条件过严，试试放宽或看全部。'
                }
              >
                {(!serverOpps && !poolScan) || oppCatFilter === 'PRIORITY' ? (
                  <button type="button" className="btn btn-primary btn-sm" disabled={oppScanning} onClick={() => handleOpportunityScan()}>扫描</button>
                ) : null}
                {putBlocked && (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOppSideFilter('CALL')}>只看 Call</button>
                )}
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setFilterOpen(true)}>更多筛选</button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => {
                  setOppCatFilter('all'); setOppBucketFilter('all_buckets'); setOppHideUntradeable(false); setOppSideFilter('all')
                }}>看全部</button>
              </EmptyState>
            ) : (
              <>
              {pagedOppRows.map((row, idx) => {
                const tl = tierLabel(row.trade_tier)
                const sk = signalKindLabel(resolveOppSignalKind(row))
                const remAnn = row.remaining_annualized ?? row.check?.remaining_annualized ?? null
                const expAnn = row.kind === 'OPEN' ? (row.annualized ?? null) : null
                const cta = row.kind === 'MANAGE'
                  ? (row.categories.includes('ROLL') ? 'Roll' : row.categories.includes('CLOSE') || row.categories.includes('LOW_YIELD') ? '平仓' : row.categories.includes('UNCOVERED') ? '找Call' : '找Put')
                  : row.tradeable ? '登记' : '门槛'
                const open = expandedExplain === row.id
                return (
                  <div key={row.id}>
                    <div className="opp-row" style={idx === rowCursor ? { background: 'var(--green-dim)' } : undefined}>
                      <div className="opp-row-layout">
                        {/* 左: 身份 */}
                        <div className="opp-row-left">
                          <Badge color={tl.color}>{tl.text}</Badge>
                          {row.kind === 'OPEN' && (
                            <Badge color={sk.color} title={sk.title}>{sk.text}</Badge>
                          )}
                          {row.is_top_pick && row.kind === 'OPEN' && (
                            <Badge color="orange" title="同标的同方向当前最优">主推</Badge>
                          )}
                          <b>{row.symbol}</b>
                          <span style={{ fontWeight: 600, color: row.side === 'PUT' ? 'var(--green)' : 'var(--purple, #a78bfa)' }}>
                            {row.side === 'PUT' ? 'Put' : row.side === 'CALL' ? 'Call' : ''}
                          </span>
                          {row.strike != null && <span style={{ opacity: 0.85 }}>${fmt(row.strike, row.strike % 1 ? 2 : 0)}</span>}
                          <RiskMarks hard={row.risk_hard} soft={row.risk_soft} />
                          {row.covers_earnings && <Badge color="orange">财报</Badge>}
                          {!row.tradeable && row.kind === 'OPEN' && <Badge color="red">门槛</Badge>}
                        </div>
                        {/* 中: 关键数字 */}
                        <div className="opp-row-mid">
                          {row.kind === 'OPEN' ? (
                            <>
                              <span className="ann">年化 <b>{expAnn != null ? `${fmt(expAnn, 1)}%` : '—'}</b></span>
                              <span>Δ <b>{row.delta != null ? Number(row.delta).toFixed(2) : '—'}</b></span>
                              <span>
                                买价{' '}
                                <b style={row.bid == null ? { color: 'var(--warning, #fb923c)' } : undefined}>
                                  {row.bid != null ? fmt(row.bid, 2) : '无'}
                                </b>
                              </span>
                              <span>{DTE_BUCKET_META[row.dte_bucket]?.label || (row.dte != null ? `DTE${row.dte}` : '—')}</span>
                              {row.seen_at && <span title={String(row.seen_at)}>{fmtRelativeTime(row.seen_at)}</span>}
                            </>
                          ) : (
                            <>
                              <span>{row.action_hint || row.headline || '管理'}</span>
                              {remAnn != null && <span>剩余年化 <b>{fmt(remAnn, 1)}%</b></span>}
                              {row.dte != null && <span>DTE {row.dte}</span>}
                              {row.profit_pct != null && <span>浮盈 <b>{row.profit_pct}%</b></span>}
                            </>
                          )}
                        </div>
                        {/* 右: 主 CTA */}
                        <div className="opp-row-right">
                          {row.kind === 'OPEN' ? (
                            <>
                              <button type="button" className="btn btn-ghost btn-sm"
                                onClick={() => setExpandedExplain(open ? null : row.id)}>
                                {open ? '收起' : '为何'}
                              </button>
                              <button type="button" className="btn btn-secondary btn-sm"
                                disabled={suggestLoading}
                                title="拉期权链并补实时买价"
                                onClick={() => handleSuggest(
                                  row.symbol,
                                  row.side === 'PUT' ? 'put' : 'call',
                                  row.cycle_id ?? undefined,
                                  row,
                                )}>
                                {row.bid == null ? '补价' : '详情'}
                              </button>
                              <button type="button" className="btn btn-primary btn-sm"
                                disabled={row.risk_block || !row.tradeable}
                                title={row.risk_block ? '风控阻断' : !row.tradeable ? (row.kill_reasons.join(',') || '未过门槛') : '登记'}
                                onClick={() => openOppRegister(row)}>登记</button>
                            </>
                          ) : (
                            <button type="button" className="btn btn-primary btn-sm"
                              onClick={() => openOppRegister(row)}>{cta}</button>
                          )}
                        </div>
                      </div>
                    </div>
                    {open && row.kind === 'OPEN' && (
                      <div className="row-expand" id="wheel-explain-panel">
                        <b>{row.symbol} · {tl.text}{!row.tradeable ? ' · 已杀掉' : ''}</b>
                        <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                          {explainOpenOpp(row).map((x, i) => <li key={i}>{x}</li>)}
                          {row.kill_reasons.map((k, i) => (
                            <li key={`k${i}`} style={{ color: 'var(--warning)' }}>门槛: {k}</li>
                          ))}
                          {(row.risk_soft || []).map((s, i) => (
                            <li key={`s${i}`} style={{ color: 'var(--text-secondary)' }}>提示: {s}</li>
                          ))}
                        </ul>
                        <div style={{ marginTop: 6, fontSize: 13 }}>
                          合约 {row.contract_code || '—'}
                          {' · '}买价={row.bid ?? '无'}
                          {row.trigger_price != null && <> · 触价={fmt(row.trigger_price, 4)}</>}
                          {' · '}Δ={row.delta ?? '无'} · DTE={row.dte ?? '无'}
                        </div>
                        <div style={{ marginTop: 8 }}>
                          <button type="button" className="btn btn-secondary btn-sm"
                            onClick={() => {
                              const mf = oppMemoFields(row)
                              copyOrderMemo({
                                symbol: row.symbol, side: row.side || 'PUT', action: 'SELL',
                                contract_code: row.contract_code, strike: row.strike, expiry: row.expiry,
                                price: mf.price, price_kind: mf.price_kind,
                                note: row.seen_at ? `发现 ${fmtRelativeTime(row.seen_at)}` : undefined,
                              })
                            }}>复制备忘</button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
              {filteredOppRows.length > 0 && (
                <div style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  flexWrap: 'wrap', gap: 8, marginTop: 12, paddingTop: 10,
                  borderTop: '1px solid var(--border)', fontSize: 13, color: 'var(--text-secondary)',
                }}>
                  <span>
                    共 {filteredOppRows.length} 条
                    {filteredOppRows.length > oppPageSize
                      ? ` · 第 ${oppPageSafe}/${oppPageCount} 页`
                      : ''}
                    {filteredOppRows.length > oppPageSize && (
                      <> · 本页 {(oppPageSafe - 1) * oppPageSize + 1}–{Math.min(oppPageSafe * oppPageSize, filteredOppRows.length)}</>
                    )}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      每页
                      <select
                        value={oppPageSize}
                        onChange={e => { setOppPageSize(Number(e.target.value)); setOppPage(1); setRowCursor(0) }}
                        style={{
                          padding: '2px 6px', background: 'var(--bg-secondary)',
                          border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 13,
                        }}
                      >
                        {[10, 15, 20, 30, 50].map(n => (
                          <option key={n} value={n}>{n}</option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      disabled={oppPageSafe <= 1}
                      onClick={() => { setOppPage(p => Math.max(1, p - 1)); setRowCursor(0) }}
                    >
                      上一页
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      disabled={oppPageSafe >= oppPageCount}
                      onClick={() => { setOppPage(p => Math.min(oppPageCount, p + 1)); setRowCursor(0) }}
                    >
                      下一页
                    </button>
                  </div>
                </div>
              )}
              </>
            )}
          </div>

          {/* 筛选 Drawer */}
          <Drawer open={filterOpen} onClose={() => setFilterOpen(false)} title="筛选机会" mode="auto">
            <div className="filter-grid">
              <div className="filter-group">
                <label className="group-label">档位</label>
                <div className="chip-row">
                  {([
                    ['PRIORITY', '优先'], ['QUEUE', '可排单'], ['WATCH', '观察'], ['MANAGE', '该管'],
                    ['KILLED', '杀掉'], ['all', '全部'],
                  ] as const).map(([k, l]) => (
                    <button key={k} type="button" className={`chip ${oppCatFilter === k ? 'active' : ''}`}
                      onClick={() => setOppCatFilter(k)}>{l}</button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <label className="group-label">方向</label>
                <div className="chip-row">
                  {([['all', '全部'], ['PUT', 'Put'], ['CALL', 'Call']] as const).map(([k, l]) => (
                    <button key={k} type="button" className={`chip ${oppSideFilter === k ? 'active' : ''}`}
                      onClick={() => setOppSideFilter(k)}>{l}</button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <label className="group-label">DTE 桶</label>
                <div className="chip-row">
                  {([
                    ['core_extend', '核心+延伸'], ['core', '仅核心'], ['all_buckets', '含短端'], ['far', '远月'],
                  ] as const).map(([k, l]) => (
                    <button key={k} type="button" className={`chip orange ${oppBucketFilter === k ? 'active' : ''}`}
                      onClick={() => setOppBucketFilter(k)}>{l}</button>
                  ))}
                </div>
              </div>
              <div className="filter-group">
                <label className="group-label">开关</label>
                <div style={{ display: 'grid', gap: 8, fontSize: 14 }}>
                  <label><input type="checkbox" checked={oppStateAware} onChange={e => setOppStateAware(e.target.checked)} /> 状态过滤</label>
                  <label><input type="checkbox" checked={oppHideBlocked} onChange={e => setOppHideBlocked(e.target.checked)} /> 隐藏风控阻断</label>
                  <label><input type="checkbox" checked={oppHideUntradeable} onChange={e => setOppHideUntradeable(e.target.checked)} /> 隐藏不可交易</label>
                  <label><input type="checkbox" checked={oppOnlySelected} onChange={e => setOppOnlySelected(e.target.checked)} /> 仅选中标的</label>
                </div>
              </div>
              <button type="button" className="btn btn-primary" onClick={() => setFilterOpen(false)}>完成</button>
            </div>
          </Drawer>

        </div>
      )}

      {/* ── 风控 / 组合优化（周级能力，非盘中主路径） ── */}
      {tab === 'risk' && (
        <div className="tab-panel">
          <div className="banner info" style={{ marginBottom: 12 }}>
            <span style={{ flex: 1 }}>
              组合资金、压力测试、相关、准入、对账与回测 — 适合周复盘/调仓，不参与盘中开仓主路径。
            </span>
            <button type="button" className="btn btn-sm btn-secondary" onClick={() => setTab('home')}>回今日</button>
          </div>
          <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
            <WheelOptimizePanel />
          </div>
        </div>
      )}

      {/* ── 触线档案(EMA 触线持久化) ── */}
      {tab === 'timing' && (
        <div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
            EMA 触线落库档案：按合约去重合并，最近发现倒序。盘中开仓请用「机会」Tab。
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['最近发现', '方向', '标的', '合约', 'Strike', '到期(DTE)', '触发价', 'Δ', '年化%', '触及均线', 'IV分位', '标的价', '次数', '首次发现', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(!timingHistory || timingHistory.items.length === 0) && (
                <tr><td colSpan={15} style={{ padding: '20px 10px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无触线历史,去「机会扫描」Tab 点扫描</td></tr>
              )}
              {timingHistory?.items.map(item => (
                <tr key={item.contract_code} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{fmtDate(item.last_seen)}</td>
                  <td style={{ padding: '7px 10px' }}>
                    <span style={{
                      padding: '1px 8px', borderRadius: 4, fontWeight: 700, fontSize: 13,
                      background: item.side === 'PUT' ? '#38bdf822' : '#fbbf2422',
                      color: item.side === 'PUT' ? '#38bdf8' : '#fbbf24',
                    }}>{item.side === 'PUT' ? '卖Put' : '卖Call'}</span>
                  </td>
                  <td style={{ padding: '7px 10px', fontWeight: 600 }}>{item.symbol}</td>
                  <td style={{ padding: '7px 10px', fontFamily: 'monospace', fontSize: 13 }}>{item.contract_code}</td>
                  <td style={{ padding: '7px 10px' }}>{item.strike != null ? `$${fmt(item.strike)}` : '--'}</td>
                  <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>{item.expiry || '--'}{item.dte != null ? `(${item.dte}天)` : ''}</td>
                  <td style={{ padding: '7px 10px' }}>
                    ${fmt(item.trigger_price)}
                    {!!item.below_floor && (
                      <span title="现价已进入愿接区,指派概率升(不是禁止信号)"
                        style={{ color: '#fb923c', fontSize: 12, marginLeft: 4 }}>愿接区·风险升</span>
                    )}
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
                    <button className="btn btn-primary" style={{ fontSize: 13, padding: '2px 10px' }}
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
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14, fontSize: 14 }}>
              <button className="btn" style={{ fontSize: 13, padding: '3px 12px' }}
                disabled={historyPage <= 1} onClick={() => setHistoryPage(p => p - 1)}>上一页</button>
              <span style={{ color: 'var(--text-secondary)' }}>
                第 {timingHistory.page} / {Math.ceil(timingHistory.total / timingHistory.page_size)} 页 · 共 {timingHistory.total} 条
              </span>
              <button className="btn" style={{ fontSize: 13, padding: '3px 12px' }}
                disabled={historyPage >= Math.ceil(timingHistory.total / timingHistory.page_size)}
                onClick={() => setHistoryPage(p => p + 1)}>下一页</button>
            </div>
          )}
        </div>
      )}

      {/* ── 台账 ── */}
      {tab === 'ledger' && (
        <div>
          {/* 绩效复盘 */}
          <div className="card" style={{ padding: '12px 16px', marginBottom: 16, fontSize: 14 }}>
            <b>经营面板</b>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, marginTop: 10 }}>
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>资金利用率</div>
                <div style={{ fontWeight: 700 }}>{(ops.utilization * 100).toFixed(0)}%</div>
              </div>
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>平均轮转天数</div>
                <div style={{ fontWeight: 700 }}>{ops.turn_days != null ? fmt(ops.turn_days, 0) : '--'}</div>
              </div>
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>本月平仓腿占比</div>
                <div style={{ fontWeight: 700 }}>{ops.early_close_share != null ? `${(ops.early_close_share * 100).toFixed(0)}%` : '--'}</div>
              </div>
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>触线转化(30d)</div>
                <div style={{ fontWeight: 700, color: C.green }}>
                  {stats?.conversion
                    ? `${stats.conversion.converted_30d}/${stats.conversion.signal_count_30d} (${stats.conversion.rate_pct}%)`
                    : '--'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>空转 / 裸奔</div>
                <div style={{ fontWeight: 700 }}>{ops.idle_count} / {ops.uncovered_count}</div>
              </div>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8 }}>
              转化低=信号噪音或登记缺合约代码。周转天数偏高=平仓/Roll 偏慢。利用率过低=资金空转。
            </div>
          </div>
          {stats?.monthly_premium && stats.monthly_premium.length > 0 && (
            <div style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div className="card" style={{ padding: '12px 16px', flex: '1 1 320px', minWidth: 0 }}>
                <h3 style={{ fontSize: 14, margin: '0 0 10px' }}>月度净权利金</h3>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 90 }}>
                  {(() => {
                    const mp = stats.monthly_premium!
                    const maxAbs = Math.max(...mp.map(m => Math.abs(m.premium)), 1)
                    return mp.map(m => (
                      <div key={m.ym} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 0 }}
                        title={`${m.ym}:$${fmt(m.premium)}`}>
                        <span style={{ fontSize: 11, color: m.premium >= 0 ? C.green : C.red }}>{fmtMoney(Math.abs(m.premium))}</span>
                        <div style={{
                          width: '70%', borderRadius: '3px 3px 0 0',
                          height: Math.max(Math.abs(m.premium) / maxAbs * 60, 2),
                          background: m.premium >= 0 ? C.green : C.red, opacity: 0.85,
                        }} />
                        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{m.ym.slice(5)}</span>
                      </div>
                    ))
                  })()}
                </div>
              </div>
              {stats.symbol_ranking && stats.symbol_ranking.length > 0 && (
                <div className="card" style={{ padding: '12px 16px', flex: '1 1 380px', minWidth: 0 }}>
                  <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>标的收益排行<span style={{ fontSize: 13, fontWeight: 400, color: 'var(--text-secondary)', marginLeft: 8 }}>谁值得继续轮,谁该踢出池子</span></h3>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
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
          <h3 style={{ fontSize: 15, marginBottom: 10 }}>周期({cycles.length})</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14, marginBottom: 28 }}>
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

          <h3 style={{ fontSize: 15, marginBottom: 10 }}>交易明细({trades.length})</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
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
                      <span style={{ padding: '0 6px', borderRadius: 7, fontSize: 11, fontWeight: 700, background: '#a78bfa22', color: '#a78bfa', border: '1px solid #a78bfa55', marginLeft: 6 }}
                        title="同日买回+再卖出,识别为一次 Roll">Roll</span>
                    )}
                  </td>
                  <td style={{ padding: '7px 10px', fontFamily: 'monospace', fontSize: 13 }}>
                    {t.contract_code || (t.strike ? `$${fmt(t.strike)}` : '--')}
                  </td>
                  <td style={{ padding: '7px 10px' }}>{t.qty}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(t.price)}</td>
                  <td style={{ padding: '7px 10px' }}>${fmt(t.fee)}</td>
                  <td style={{ padding: '7px 10px', color: 'var(--text-secondary)' }}>{t.note || ''}</td>
                  <td style={{ padding: '7px 10px' }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn" style={{ fontSize: 13, padding: '2px 8px' }} onClick={() => setEditTrade(t)}>编辑</button>
                      <button className="btn" style={{ fontSize: 13, padding: '2px 8px', color: '#f87171' }} onClick={() => handleDeleteTrade(t)}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8 }}>
            修改或删除任意一笔后,所属周期会按剩余交易按时间顺序重新计算;若剩余序列不合法(如删掉卖出腿但保留行权腿)会拒绝并保持原样
          </div>
        </div>
      )}

      {/* ── 标的设置 ── */}
      {tab === 'targets' && (
        <div>
          <div className="card" style={{ padding: '14px 18px', marginBottom: 16 }}>
            <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>策略模板</h3>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              {(Object.keys(STRATEGY_TEMPLATES) as RiskTier[]).map(tier => (
                <button key={tier} type="button" className="btn"
                  style={{
                    fontSize: 13, fontWeight: riskTier === tier ? 700 : 400,
                    borderColor: riskTier === tier ? 'var(--accent)' : undefined,
                    background: riskTier === tier ? 'var(--accent)' : undefined,
                    color: riskTier === tier ? '#fff' : undefined,
                  }}
                  onClick={() => { setRiskTier(tier); setRiskTierState(tier); flash(`已切换风险档:${STRATEGY_TEMPLATES[tier].label}`) }}
                  title={STRATEGY_TEMPLATES[tier].desc}>
                  {STRATEGY_TEMPLATES[tier].label}
                </button>
              ))}
              <span style={{ fontSize: 13, color: 'var(--text-secondary)', alignSelf: 'center' }}>
                {STRATEGY_TEMPLATES[riskTier].desc}
              </span>
            </div>
            <div style={{
              display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10,
              fontSize: 13, color: 'var(--text-secondary)',
              padding: '8px 10px', borderRadius: 8, background: 'var(--bg-secondary)', border: '1px solid var(--border)',
            }}>
              <span>
                组合净值 / 预算{' '}
                <b style={{ color: 'var(--text)' }}>${fmtMoney(budget)}</b>
                <span style={{ marginLeft: 6, opacity: 0.85 }}>
                  {budgetSource === 'config' ? '· 来自设置页(唯一入口)'
                    : budgetSource === 'legacy' ? '· 本地旧缓存,请到设置页填写组合净值后统一'
                      : '· 默认值,请到设置页填写组合净值'}
                </span>
              </span>
              <button
                type="button"
                className="btn btn-sm"
                style={{ fontSize: 13 }}
                onClick={() => {
                  // 跳转设置:用 hash / 自定义事件;无路由时提示
                  window.dispatchEvent(new CustomEvent('tradeforge:navigate', { detail: { page: 'settings', section: 'wheel' } }))
                  flash('请在「设置 → Wheel → 组合风控」修改组合净值(唯一入口)', 'info')
                }}
              >
                去设置修改
              </button>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
              <button className="btn" style={{ fontSize: 13 }}
                onClick={async () => {
                  const tpl = STRATEGY_TEMPLATES[riskTier]
                  let n = 0
                  for (const t of targets) {
                    try {
                      await updateWheelTarget(t.symbol, {
                        delta_min: tpl.delta_min, delta_max: tpl.delta_max,
                        dte_min: tpl.dte_min, dte_max: tpl.dte_max,
                        min_annualized: tpl.min_annualized,
                      })
                      n++
                    } catch { /* skip */ }
                  }
                  await loadAll()
                  flash(`已将 ${n} 个标的参数套用「${tpl.label}」模板`)
                }}>
                套用到全部标的
              </button>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              开仓规则(delta/DTE/年化) · 管理规则(止盈/Roll) · 通知见设置页。模板只改标的筛选参数,不改历史台账。
            </div>
          </div>

          <div className="card" style={{ padding: '14px 18px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>添加标的</span>
            <select
              value={addSymbol}
              onChange={e => setAddSymbol(e.target.value)}
              style={{ ...inputStyle, width: 300 }}
            >
              <option value="">选择标的…</option>
              {(() => {
                const existing = new Set(targets.map(t => t.symbol.toUpperCase()))
                const normMkt = (m?: string) => {
                  const raw = (m || '').trim()
                  const u = raw.toUpperCase()
                  if (u === 'US' || raw === '美股') return 'US'
                  if (u === 'HK' || raw === '港股') return 'HK'
                  return u || 'OTHER'
                }
                const list = candidates.map(c => ({
                  ...c,
                  market: normMkt(c.market),
                  in_wheel: c.in_wheel ?? existing.has(c.symbol.toUpperCase()),
                }))
                const us = list.filter(c => c.market === 'US')
                const hk = list.filter(c => c.market === 'HK')
                const other = list.filter(c => c.market !== 'US' && c.market !== 'HK')
                const usAvail = us.filter(c => !c.in_wheel).length
                const hkAvail = hk.filter(c => !c.in_wheel).length
                const labelOf = (c: typeof list[0]) => {
                  const bits = [c.symbol]
                  if (c.name && c.name !== c.symbol) bits.push(c.name)
                  if (c.in_wheel) bits.push('已添加')
                  else if (c.enabled === false) bits.push('池中未启用')
                  return bits.join(' · ')
                }
                if (list.length === 0) {
                  return <option value="" disabled>{candidates.length === 0 ? '候选加载中或股票池为空' : '无候选'}</option>
                }
                return (
                  <>
                    <optgroup label={`美股（可加 ${usAvail}/${us.length}）`}>
                      {us.length === 0 ? (
                        <option value="" disabled>股票池暂无美股 — 请到「股票池」添加并启用</option>
                      ) : us.map(c => (
                        <option key={c.symbol} value={c.in_wheel ? '' : c.symbol} disabled={!!c.in_wheel}>
                          {labelOf(c)}
                        </option>
                      ))}
                    </optgroup>
                    <optgroup label={`港股（可加 ${hkAvail}/${hk.length}）`}>
                      {hk.length === 0 ? (
                        <option value="" disabled>股票池暂无港股</option>
                      ) : hk.map(c => (
                        <option key={c.symbol} value={c.in_wheel ? '' : c.symbol} disabled={!!c.in_wheel}>
                          {labelOf(c)}
                        </option>
                      ))}
                    </optgroup>
                    {other.length > 0 && (
                      <optgroup label="其他">
                        {other.map(c => (
                          <option key={c.symbol} value={c.in_wheel ? '' : c.symbol} disabled={!!c.in_wheel}>
                            {labelOf(c)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </>
                )
              })()}
            </select>
            <input type="number" step="any" value={addFloor} onChange={e => setAddFloor(e.target.value)}
              placeholder="愿接最高价" style={{ ...inputStyle, width: 110 }}
              title="被指派时最多愿付的股价(Put strike上限),不是止损线" />
            <button className="btn btn-primary" style={{ fontSize: 14, padding: '5px 14px' }}
              disabled={adding || !addSymbol} onClick={handleAddTarget}>
              {adding ? '添加中...' : '添加'}
            </button>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              美股/港股均来自股票池；已添加的灰显不可选；未启用的也可直接加入 Wheel
            </span>
          </div>

          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['标的', '现价 · 愿接 · 参考', 'Delta 区间', 'DTE 区间', '最低年化%', '最低OI', '状态', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 500 }}
                    title={h.startsWith('现价') ? '现价=日K收盘 · 愿接=你的合同价 · 参考=市场结构建议' : undefined}
                  >{h}</th>
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

      {/* 找 Put/Call / 机会详情 — 全页共用 Drawer */}
      <Drawer
        open={!!suggest || suggestLoading}
        onClose={() => { setSuggest(null); setSuggestLoading(false) }}
        title={suggest ? `${suggest.symbol} 卖 ${suggest.side}` : '加载详情'}
        subtitle={
          suggest
            ? `现价 $${fmt(suggest.spot_price)}${suggest.cost_basis != null ? ` · Cost $${fmt(suggest.cost_basis)}` : ''}`
            : '需富途 OpenD'
        }
        mode="auto"
      >
        {suggestLoading && !suggest && (
          <div style={{ color: 'var(--warning)', fontSize: 14 }}>正在拉取期权链并筛选…</div>
        )}
        {suggest && (
          <div>
            {suggest.volatility && (
              <div style={{ padding: '8px 12px', background: 'var(--bg-secondary)', borderRadius: 6, marginBottom: 10 }}>
                <VolatilityBar v={suggest.volatility} />
              </div>
            )}
            {(suggest.earnings_warn || suggest.delta_preference || suggest.trend_warning) && (
              <div style={{ display: 'flex', gap: 12, fontSize: 13, marginBottom: 10, flexWrap: 'wrap' }}>
                {suggest.earnings_warn && (
                  <span style={{ color: '#fb923c' }}>
                    ⚠ 财报 {suggest.earnings_date}(距今 {suggest.days_to_earnings} 天)
                  </span>
                )}
                {suggest.trend_warning && <span style={{ color: '#f87171' }}>⚠ {suggest.trend_warning}</span>}
                {suggest.delta_preference && <span style={{ color: '#38bdf8' }}>ℹ {suggest.delta_preference}</span>}
              </div>
            )}
            {suggest.suggestions.length === 0 ? (
              <EmptyState
                title="无符合条件合约"
                description={suggest.message || '可在「标的设置」放宽 delta / DTE / 最低年化'}
              >
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setTab('targets')}>
                  去标的设置
                </button>
              </EmptyState>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 640 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      {['合约', 'Strike', 'Δ', 'DTE', 'Bid', '点差%', '年化%', ...(suggest.side === 'PUT' ? ['年化·保'] : []), '分', 'OI', ''].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {suggest.suggestions.map(s => (
                      <tr key={s.contract_code} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '7px 8px', fontFamily: 'monospace', fontSize: 13, color: 'var(--text-secondary)' }}>
                          {s.contract_code}
                          {s.covers_earnings && <span style={{ color: '#fb923c', marginLeft: 4 }}>财报</span>}
                        </td>
                        <td style={{ padding: '7px 8px', fontWeight: 600 }}>${fmt(s.strike)}</td>
                        <td style={{ padding: '7px 8px' }}>{s.delta}</td>
                        <td style={{ padding: '7px 8px' }}>{s.dte}</td>
                        <td style={{ padding: '7px 8px' }}>${fmt(s.bid)}</td>
                        <td style={{ padding: '7px 8px', color: (s.spread_pct ?? 0) > 6 ? '#fb923c' : undefined }}>
                          {s.spread_pct != null ? s.spread_pct : '—'}
                        </td>
                        <td style={{ padding: '7px 8px', color: '#4ade80', fontWeight: 700 }}>{fmt(s.annualized, 1)}</td>
                        {suggest.side === 'PUT' && (
                          <td style={{ padding: '7px 8px', color: '#38bdf8' }}>
                            {s.annualized_margin != null ? fmt(s.annualized_margin, 1) : '—'}
                          </td>
                        )}
                        <td style={{ padding: '7px 8px', fontWeight: 700 }}
                          title={s.score_factors
                            ? `年化 ${s.score_factors.annualized} × 流动性 ${s.score_factors.liquidity} × 趋势 ${s.score_factors.trend} × 财报 ${s.score_factors.earnings} × IV ${s.score_factors.iv_bonus} × delta ${s.score_factors.delta_pref}`
                            : undefined}>
                          {s.score != null ? fmt(s.score, 1) : '—'}
                        </td>
                        <td style={{ padding: '7px 8px' }}>{s.open_interest}</td>
                        <td style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>
                          <button type="button" className="btn btn-ghost btn-sm" style={{ minHeight: 28, padding: '2px 8px' }}
                            onClick={() => copyOrderMemo({
                              symbol: suggest.symbol, side: suggest.side, action: 'SELL',
                              contract_code: s.contract_code, strike: s.strike, expiry: s.expiry, price: s.bid,
                            })}>备忘</button>
                          <button type="button" className="btn btn-primary btn-sm" style={{ minHeight: 28, padding: '2px 10px', marginLeft: 4 }}
                            onClick={() => {
                              enqueuePending({
                                symbol: suggest.symbol, side: suggest.side,
                                trade_type: suggest.side === 'PUT' ? 'SELL_PUT' : 'SELL_CALL',
                                contract_code: normalizeContractCode(s.contract_code, suggest.symbol) || s.contract_code,
                                strike: s.strike, expiry: s.expiry, price: s.bid, qty: 1, source: 'suggest',
                              })
                              setTradeModal({
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
                              })
                            }}>
                            登记
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 10 }}>
              筛选来自标的设置 · 先在富途成交再登记实际价
            </div>
          </div>
        )}
      </Drawer>

      {tradeModal && (
        <TradeModal initial={tradeModal.initial} cycleStatus={tradeModal.status}
          cycleId={tradeModal.cycleId} newCycle={tradeModal.newCycle}
          onClose={() => setTradeModal(null)}
          onSaved={() => {
            setSuggest(null)
            // 登记成功后从待登记队列移除同合约
            const code = normalizeContractCode(tradeModal.initial.contract_code, tradeModal.initial.symbol)
            const leaving = loadPendingQueue().find(p => {
              if (code && (normalizeContractCode(p.contract_code, p.symbol) === code)) return true
              return p.symbol === tradeModal.initial.symbol && p.trade_type === tradeModal.initial.trade_type
                && String(p.strike || '') === String(tradeModal.initial.strike || '')
            })
            if (leaving) {
              setPendingLeaving(leaving.id)
              setTimeout(() => {
                const q = loadPendingQueue().filter(p => p.id !== leaving.id)
                // also drop any other matches
                const q2 = q.filter(p => {
                  if (code && (normalizeContractCode(p.contract_code, p.symbol) === code)) return false
                  if (p.symbol === tradeModal.initial.symbol && p.trade_type === tradeModal.initial.trade_type
                    && String(p.strike || '') === String(tradeModal.initial.strike || '')) return false
                  return true
                })
                savePendingQueue(q2)
                setPendingQueue(q2)
                setPendingLeaving(null)
              }, 420)
            } else {
              const q = loadPendingQueue().filter(p => {
                if (code && (normalizeContractCode(p.contract_code, p.symbol) === code)) return false
                if (p.symbol === tradeModal.initial.symbol && p.trade_type === tradeModal.initial.trade_type
                  && String(p.strike || '') === String(tradeModal.initial.strike || '')) return false
                return true
              })
              savePendingQueue(q)
              setPendingQueue(q)
            }
            loadAll()
            flash('登记成功', 'success')
          }} />
      )}
      {editTrade && (
        <EditTradeModal trade={editTrade}
          onClose={() => setEditTrade(null)} onSaved={loadAll} />
      )}
      {rollData && (
        <RollModal data={rollData} onClose={() => setRollData(null)} onSaved={loadAll} />
      )}

      {manageCompare && (
        <ManageDecisionModal
          mc={manageCompare}
          serverOpps={serverOpps}
          portfolioPutBlocked={!!portfolioContext?.portfolio_put_blocked}
          rollLoading={rollLoading}
          executeLoading={executeLoading}
          pickReplaceCandidates={pickReplaceCandidates}
          onDismiss={() => setManageCompare(null)}
          onQuickExecute={async () => {
            const mc = manageCompare
            setExecuteLoading(true)
            try {
              const r = await executeWheelDraft({
                kind: 'manage',
                action: 'auto',
                item: mc as unknown as Record<string, unknown>,
                apply: true,
              })
              if (r.post_assign) {
                flash(`已记账 · 下一步: ${(r.post_assign as any).next_step_hint || '找 CC'}`, 'success')
              } else {
                flash('已一键记账', 'success')
              }
              setManageCompare(null)
              loadAll()
            } catch (e: any) {
              flash(e.message || '执行失败', 'error')
            } finally {
              setExecuteLoading(false)
            }
          }}
          onExpire={() => {
            const mc = manageCompare
            setTradeModal({
              initial: {
                symbol: mc.symbol, trade_type: 'EXPIRE', contract_code: mc.contract_code,
                strike: String(mc.strike), expiry: mc.expiry?.slice(0, 10) || '',
                note: mc.side === 'CALL' ? 'CC 放任到期' : 'CSP 放任到期',
              },
              status: mc.side === 'CALL' ? 'CC_OPEN' : 'CSP_OPEN', cycleId: mc.cycle_id,
            })
            setManageCompare(null)
          }}
          onBuyback={() => {
            const mc = manageCompare
            setTradeModal({
              initial: {
                symbol: mc.symbol,
                trade_type: mc.side === 'CALL' ? 'BUY_CALL_CLOSE' : 'BUY_PUT_CLOSE',
                contract_code: mc.contract_code, strike: String(mc.strike),
                expiry: mc.expiry?.slice(0, 10) || '',
                price: String(mc.buyback_ask || mc.current_price || ''), qty: '1',
                note: mc.side === 'CALL' ? 'CC 买回平仓' : 'CSP 买回平仓',
              },
              status: mc.side === 'CALL' ? 'CC_OPEN' : 'CSP_OPEN', cycleId: mc.cycle_id,
            })
            setManageCompare(null)
          }}
          onRoll={() => {
            const id = manageCompare.cycle_id
            const pref = manageCompare.prefer_card
            setManageCompare(null)
            if (id) handleRoll(id, pref)
          }}
          onGoOpps={() => {
            setManageCompare(null)
            setTab('opps')
            setOppCatFilter('PRIORITY')
          }}
        />
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
  const prefer = data.highlighted_card || data.decision?.prefer_card || null
  const defaultCode = data.default_candidate?.contract_code
    || data.candidates[0]?.contract_code
    || null
  const [selected, setSelected] = useState<string | null>(defaultCode)
  const [buyback, setBuyback] = useState(String(data.current.buyback_ask || ''))
  const initCand = data.candidates.find(c => c.contract_code === defaultCode) || data.candidates[0]
  const [newPrice, setNewPrice] = useState(String(initCand?.bid ?? ''))
  const [fee, setFee] = useState('0')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const cand = data.candidates.find(c => c.contract_code === selected)
  const size = data.current.contract_size || 100
  const netCredit = cand && buyback && newPrice
    ? ((parseFloat(newPrice) - parseFloat(buyback)) * size).toFixed(0) : null

  const preferLabel: Record<string, string> = {
    roll_out: '优先 Roll out(换到期)',
    adjust_strike: '优先调 strike(Roll 改行权价)',
    no_roll: '优先不平/不 roll(止盈平仓或持有)',
  }

  const inputStyle = {
    width: 90, padding: '4px 6px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 13,
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
        <h3 style={{ margin: '0 0 6px', fontSize: 17 }}>Roll 对比 — {data.symbol} {data.side}</h3>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
          当前 ${fmt(data.current.strike)} {data.current.expiry}(DTE {data.current.dte ?? '--'}) ·
          开仓 ${fmt(data.current.open_price)} · 买回约 ${fmt(data.current.buyback_ask)} · δ{data.current.delta}
        </div>
        {(data.decision?.headline || prefer) && (
          <div style={{
            marginBottom: 10, padding: '8px 12px', borderRadius: 8,
            background: 'var(--green-dim, #22c55e18)', border: '1px solid var(--green, #22c55e55)',
            fontSize: 14,
          }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>
              决策建议 · {prefer ? (preferLabel[prefer] || prefer) : '见详情'}
            </div>
            {data.decision?.headline && <div>{data.decision.headline}</div>}
            {data.decision?.detail && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>{data.decision.detail}</div>
            )}
          </div>
        )}
        {err && <div className="alert alert-error" style={{ marginBottom: 10 }}>{err}</div>}
        {(data.warnings?.length ?? 0) > 0 && (
          <div style={{ marginBottom: 10, padding: '6px 10px', background: '#fb923c11', border: '1px solid #fb923c55', borderRadius: 6, fontSize: 13, color: '#fb923c' }}>
            {data.warnings!.map((w, i) => <div key={i}>⚠ {w}</div>)}
          </div>
        )}

        {data.candidates.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: 14, padding: '10px 0' }}>
            没有找到相近 delta 的下期合约(可先手动平仓,再用助手找新合约)
            {prefer === 'no_roll' && ' · 当前决策更倾向止盈/持有,可不 roll'}
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['', '合约编号', 'Strike', '到期', 'DTE', 'δ', 'Bid', '净收权利金/张', '新仓年化%'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '5px 8px' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.candidates.map(c => (
                <tr key={c.contract_code} style={{
                  borderBottom: '1px solid var(--border)', cursor: 'pointer',
                  background: selected === c.contract_code ? 'var(--green-dim)' : undefined,
                }}
                  onClick={() => { setSelected(c.contract_code); setNewPrice(String(c.bid)) }}>
                  <td style={{ padding: '6px 8px' }}>
                    <input type="radio" checked={selected === c.contract_code} readOnly />
                  </td>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 13, color: 'var(--text-secondary)' }}>{c.contract_code}</td>
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

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 13, marginBottom: 14 }}>
          <label>买回价 <input type="number" style={inputStyle} value={buyback} onChange={e => setBuyback(e.target.value)} /></label>
          <label>新卖价 <input type="number" style={inputStyle} value={newPrice} onChange={e => setNewPrice(e.target.value)} /></label>
          <label>手续费/腿 <input type="number" style={inputStyle} value={fee} onChange={e => setFee(e.target.value)} /></label>
          {netCredit != null && (
            <span>本次 Roll 净{parseFloat(netCredit) >= 0 ? '收' : '付'} <b style={{ color: parseFloat(netCredit) >= 0 ? '#4ade80' : '#f87171' }}>${netCredit}</b>/张</span>
          )}
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
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
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 14,
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
        <h3 style={{ margin: '0 0 6px', fontSize: 17 }}>编辑交易 — {trade.symbol}</h3>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 14 }}>
          保存后所属周期将按时间顺序重放重算;不合法的修改会被拒绝
        </div>
        {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
        <div style={{ display: 'grid', gap: 12 }}>
          <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            类型
            <select value={form.trade_type} style={inputStyle}
              onChange={e => setForm(f => ({ ...f, trade_type: e.target.value as WheelTradeType }))}>
              {(Object.keys(TRADE_LABELS) as WheelTradeType[]).map(t => (
                <option key={t} value={t}>{TRADE_LABELS[t]}</option>
              ))}
            </select>
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              合约代码
              <input value={form.contract_code} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, contract_code: e.target.value }))} />
            </label>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Strike
              <input type="number" value={form.strike} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, strike: e.target.value }))} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              到期日
              <input type="date" value={(form.expiry || '').slice(0, 10)} style={{ ...inputStyle, cursor: 'pointer' }}
                onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
            </label>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              成交时间
              <input type="datetime-local" value={(form.traded_at || '').slice(0, 16)} style={{ ...inputStyle, cursor: 'pointer' }}
                onClick={e => { try { (e.currentTarget as HTMLInputElement).showPicker() } catch {} }}
                onChange={e => setForm(f => ({ ...f, traded_at: e.target.value }))} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8 }}>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              张数
              <SelectNum value={form.qty} options={QTY_CONTRACT_OPTS} style={inputStyle}
                onChange={v => setForm(f => ({ ...f, qty: v }))} />
            </label>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              价格
              <input type="number" step="any" value={form.price} style={inputStyle}
                onChange={e => setForm(f => ({ ...f, price: e.target.value }))} />
            </label>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              手续费
              <SelectNum value={form.fee} options={FEE_OPTS} style={inputStyle}
                onChange={v => setForm(f => ({ ...f, fee: v }))} />
            </label>
            <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              合约乘数
              <SelectNum value={form.contract_size} options={CONTRACT_SIZE_OPTS} style={inputStyle}
                onChange={v => setForm(f => ({ ...f, contract_size: v }))} />
            </label>
          </div>
          <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
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
    border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 13,
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
        floor_change_source: 'manual',
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
          <span style={{ color: 'var(--text-secondary)', fontSize: 13, marginLeft: 6 }}>{target.name}</span>
        </td>
        <td style={{ padding: '8px 10px' }}>
          <TargetPriceStrip
            spot={target.spot}
            floor={target.floor_price}
            suggested={target.suggested_floor}
            suggestedDelta={target.suggested_floor_delta}
          />
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
            <button className="btn" style={{ fontSize: 13, padding: '2px 8px' }} onClick={() => setEditing(true)}>编辑</button>
            <button className="btn" style={{ fontSize: 13, padding: '2px 8px' }} onClick={onToggle}>{target.enabled ? '停用' : '启用'}</button>
            <button className="btn" style={{ fontSize: 13, padding: '2px 8px', color: '#f87171' }} onClick={onDelete}>删除</button>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-secondary)' }}>
      <td style={{ padding: '8px 10px', fontWeight: 600 }}>{target.symbol}{err && <div style={{ color: '#f87171', fontSize: 13 }}>{err}</div>}</td>
      <td style={{ padding: '8px 10px' }}>
        <div style={{ marginBottom: 4 }}>
          <TargetPriceStrip
            size="sm"
            spot={target.spot}
            floor={target.floor_price}
            suggested={target.suggested_floor}
            suggestedDelta={target.suggested_floor_delta}
          />
        </div>
        <label style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          改愿接{' '}
          <input type="number" step="any" style={inputStyle} value={form.floor_price}
            onChange={e => setForm(f => ({ ...f, floor_price: e.target.value }))}
            title="愿接最高价:真被指派时最多愿付;Put strike≤此价;不是止损" />
        </label>
      </td>
      <td style={{ padding: '8px 10px' }}>
        <SelectNum value={form.delta_min} options={DELTA_OPTS} style={inputStyle}
          onChange={v => setForm(f => ({ ...f, delta_min: v }))} />
        {' ~ '}
        <SelectNum value={form.delta_max} options={DELTA_OPTS} style={inputStyle}
          onChange={v => setForm(f => ({ ...f, delta_max: v }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <SelectNum value={form.dte_min} options={DTE_OPTS} style={inputStyle}
          onChange={v => setForm(f => ({ ...f, dte_min: v }))} />
        {' ~ '}
        <SelectNum value={form.dte_max} options={DTE_OPTS} style={inputStyle}
          onChange={v => setForm(f => ({ ...f, dte_max: v }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <SelectNum value={form.min_annualized} options={ANN_OPTS} style={inputStyle}
          onChange={v => setForm(f => ({ ...f, min_annualized: v }))} />
      </td>
      <td style={{ padding: '8px 10px' }}>
        <SelectNum value={form.min_open_interest} options={OI_OPTS} style={inputStyle}
          onChange={v => setForm(f => ({ ...f, min_open_interest: v }))} />
      </td>
      <td style={{ padding: '8px 10px' }}></td>
      <td style={{ padding: '8px 10px' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-primary" style={{ fontSize: 13, padding: '2px 8px' }} onClick={save}>保存</button>
          <button className="btn" style={{ fontSize: 13, padding: '2px 8px' }} onClick={() => setEditing(false)}>取消</button>
        </div>
      </td>
    </tr>
  )
}
