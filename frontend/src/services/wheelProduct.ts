/** Wheel 产品层工具:待登记队列、下单备忘、策略模板、组合指标 */

export type AppMode = 'wheel' | 'research'
export type RiskTier = 'conservative' | 'balanced' | 'aggressive'

export type PendingRegItem = {
  id: string
  symbol: string
  side: 'PUT' | 'CALL'
  trade_type: 'SELL_PUT' | 'SELL_CALL' | 'BUY_PUT_CLOSE' | 'BUY_CALL_CLOSE'
  contract_code?: string
  strike?: number | null
  expiry?: string | null
  qty?: number
  price?: number | null
  note?: string
  cycle_id?: string
  source: 'opp' | 'suggest' | 'manual' | 'manage'
  created_at: string
}

const MODE_KEY = 'tradeforge.appMode'
const PENDING_KEY = 'tradeforge.wheel.pendingReg'
const ONBOARD_KEY = 'tradeforge.wheel.onboardDone'
const TIER_KEY = 'tradeforge.wheel.riskTier'
const BUDGET_KEY = 'tradeforge.wheel.portfolioBudget'

export function getAppMode(): AppMode {
  try {
    const v = localStorage.getItem(MODE_KEY)
    return v === 'research' ? 'research' : 'wheel'
  } catch {
    return 'wheel'
  }
}

export function setAppMode(mode: AppMode) {
  localStorage.setItem(MODE_KEY, mode)
  window.dispatchEvent(new CustomEvent('tradeforge:app-mode', { detail: mode }))
}

export function getRiskTier(): RiskTier {
  try {
    const v = localStorage.getItem(TIER_KEY)
    if (v === 'conservative' || v === 'aggressive') return v
  } catch { /* */ }
  return 'balanced'
}

export function setRiskTier(tier: RiskTier) {
  localStorage.setItem(TIER_KEY, tier)
}

export function getPortfolioBudget(): number {
  try {
    const n = Number(localStorage.getItem(BUDGET_KEY))
    return Number.isFinite(n) && n > 0 ? n : 100000
  } catch {
    return 100000
  }
}

export function setPortfolioBudget(v: number) {
  localStorage.setItem(BUDGET_KEY, String(Math.max(0, v)))
}

export function isOnboardDone(): boolean {
  return localStorage.getItem(ONBOARD_KEY) === '1'
}

export function setOnboardDone() {
  localStorage.setItem(ONBOARD_KEY, '1')
}

export function loadPendingQueue(): PendingRegItem[] {
  try {
    const raw = localStorage.getItem(PENDING_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : []
  } catch {
    return []
  }
}

export function savePendingQueue(items: PendingRegItem[]) {
  localStorage.setItem(PENDING_KEY, JSON.stringify(items.slice(0, 50)))
  window.dispatchEvent(new CustomEvent('tradeforge:pending-reg'))
}

export function addPendingReg(item: Omit<PendingRegItem, 'id' | 'created_at'>): PendingRegItem {
  const list = loadPendingQueue()
  const full: PendingRegItem = {
    ...item,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    created_at: new Date().toISOString(),
    contract_code: normalizeContractCode(item.contract_code, item.symbol) || item.contract_code,
  }
  // 同合约去重:更新而非重复加
  const key = full.contract_code || `${full.symbol}|${full.side}|${full.strike}|${full.expiry}`
  const next = list.filter(x => {
    const k = x.contract_code || `${x.symbol}|${x.side}|${x.strike}|${x.expiry}`
    return k !== key
  })
  next.unshift(full)
  savePendingQueue(next)
  return full
}

export function removePendingReg(id: string) {
  savePendingQueue(loadPendingQueue().filter(x => x.id !== id))
}

/** 规范化合约代码:美股无市场前缀时补 US. */
export function normalizeContractCode(code?: string | null, symbol?: string): string {
  const c = (code || '').trim().toUpperCase()
  if (!c) return ''
  if (c.includes('.')) return c
  // OCC 裸码或仅 ticker 期权码
  if (/^[A-Z]+\d{6}[CP]\d+$/.test(c) || /^[A-Z0-9]+$/.test(c)) {
    // 港股数字标的不硬加 US
    if (symbol && (/^\d/.test(symbol) || symbol.endsWith('.HK'))) return c
    return `US.${c}`
  }
  return c
}

export function contractCodeWarning(code?: string | null): string | null {
  const c = (code || '').trim()
  if (!c) return '未填合约代码:将影响体检/转化率统计,建议填写或等系统自动生成'
  if (!c.includes('.')) return '合约代码缺少市场前缀,提交时将尝试补 US.'
  return null
}

/** 富途下单备忘(复制到剪贴板) */
export function buildFutuOrderMemo(p: {
  symbol: string
  side: 'PUT' | 'CALL'
  action: 'SELL' | 'BUY'
  contract_code?: string
  strike?: number | null
  expiry?: string | null
  qty?: number
  /** 卖出参考：优先真实 bid，勿把触线 last 当卖价 */
  price?: number | null
  /** bid=实时买价；trigger=仅有触线价(非卖价)；none=无 */
  price_kind?: 'bid' | 'premium' | 'trigger' | 'none'
  note?: string
}): string {
  const code = normalizeContractCode(p.contract_code, p.symbol) || p.contract_code || '(无代码)'
  const dir = p.action === 'SELL'
    ? (p.side === 'PUT' ? '卖出 Put(开仓)' : '卖出 Call(开仓)')
    : (p.side === 'PUT' ? '买入 Put(平仓)' : '买入 Call(平仓)')
  const kind = p.price_kind || (p.price != null ? 'bid' : 'none')
  let priceLine = '参考卖价: (无实时买价，限价自定)'
  if (p.price != null && kind === 'bid') {
    priceLine = `参考卖价(bid): ${p.price}`
  } else if (p.price != null && kind === 'premium') {
    priceLine = `参考权利金: ${p.price}`
  } else if (p.price != null && kind === 'trigger') {
    priceLine = `触线价: ${p.price} (非买价，勿直接当卖出限价)`
  }
  const lines = [
    '【TradeForge 下单备忘】',
    `标的: ${p.symbol}`,
    `方向: ${dir}`,
    `合约: ${code}`,
    p.strike != null ? `Strike: ${p.strike}` : '',
    p.expiry ? `到期: ${String(p.expiry).slice(0, 10)}` : '',
    `数量: ${p.qty ?? 1} 张`,
    priceLine,
    p.note ? `备注: ${p.note}` : '',
    '—— 在富途成交后回到 TradeForge「待登记」一键登记',
  ].filter(Boolean)
  return lines.join('\n')
}

/** 开仓机会：真实可卖参考价 vs 触线价分离 */
export function resolveOppSellPrice(p: {
  bid?: number | null
  premium_used?: number | null
  trigger_price?: number | null
}): { sell: number | null; kind: 'bid' | 'premium' | 'trigger' | 'none'; trigger: number | null } {
  const bid = p.bid != null && p.bid > 0 ? p.bid : null
  const prem = p.premium_used != null && p.premium_used > 0 ? p.premium_used : null
  const trigger = p.trigger_price != null && p.trigger_price > 0 ? p.trigger_price : null
  if (bid != null) return { sell: bid, kind: 'bid', trigger }
  if (prem != null) return { sell: prem, kind: 'premium', trigger }
  // 触线价仅作旁注，不算卖出参考
  return { sell: null, kind: trigger != null ? 'trigger' : 'none', trigger }
}

/** 相对时间：机会出现/最近发现 */
export function fmtRelativeTime(iso?: string | null, now = Date.now()): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return String(iso).replace('T', ' ').slice(0, 16)
  const mins = Math.floor((now - t) / 60000)
  if (mins < 0) return String(iso).replace('T', ' ').slice(5, 16)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}分钟前`
  const h = Math.floor(mins / 60)
  if (h < 24) return `${h}小时前`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}天前`
  return String(iso).replace('T', ' ').slice(5, 16)
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      return true
    } catch {
      return false
    }
  }
}

/** 策略模板 → 标的默认参数 */
export const STRATEGY_TEMPLATES: Record<RiskTier, {
  label: string
  desc: string
  floor_pct_of_spot: number
  delta_min: number
  delta_max: number
  dte_min: number
  dte_max: number
  min_annualized: number
  profit_target_pct: number
  stress_put_block_ratio: number
}> = {
  conservative: {
    label: '保守',
    desc: '愿接约 0.85×现价(更深 OTM Put)、更严年化;行权压力 1.2x 停新 Put。floor=愿接最高价非止损',
    floor_pct_of_spot: 0.85,
    delta_min: 0.10, delta_max: 0.22,
    dte_min: 25, dte_max: 45,
    min_annualized: 18,
    profit_target_pct: 50,
    stress_put_block_ratio: 1.2,
  },
  balanced: {
    label: '均衡',
    desc: '愿接约 0.90×现价;默认 Wheel 舒适区。Put strike≤floor;Call 看成本底线',
    floor_pct_of_spot: 0.90,
    delta_min: 0.15, delta_max: 0.30,
    dte_min: 21, dte_max: 45,
    min_annualized: 15,
    profit_target_pct: 50,
    stress_put_block_ratio: 1.5,
  },
  aggressive: {
    label: '积极',
    desc: '愿接约 0.95×现价(近价 Put 权利金更高);压力 2.0x 才停 Put。近价愿接≠错误',
    floor_pct_of_spot: 0.95,
    delta_min: 0.18, delta_max: 0.35,
    dte_min: 14, dte_max: 40,
    min_annualized: 12,
    profit_target_pct: 40,
    stress_put_block_ratio: 2.0,
  },
}

export function stressBlocksNewPuts(
  assignmentStress: number,
  totalCommitted: number,
  tier: RiskTier = getRiskTier(),
): boolean {
  if (assignmentStress <= 0) return false
  const ratio = STRATEGY_TEMPLATES[tier].stress_put_block_ratio
  const base = Math.max(totalCommitted, getPortfolioBudget() * 0.3)
  return assignmentStress >= base * ratio
}

/** 按剩余资金建议张数(至少 0) */
export function suggestQty(params: {
  strike: number
  contractSize?: number
  symbolHeadroom?: number | null
  portfolioAvailable?: number
  side: 'PUT' | 'CALL'
}): number {
  const size = params.contractSize || 100
  const per = params.strike * size
  if (per <= 0) return 1
  let cap = Infinity
  if (params.side === 'PUT') {
    if (params.symbolHeadroom != null && params.symbolHeadroom >= 0) {
      cap = Math.min(cap, params.symbolHeadroom)
    }
    if (params.portfolioAvailable != null) {
      cap = Math.min(cap, params.portfolioAvailable)
    }
  }
  if (!Number.isFinite(cap) || cap === Infinity) return 1
  return Math.max(0, Math.floor(cap / per))
}

// ── M1 机会发现:可交易性 / DTE 桶 / 日租 / 双轨档位 ──────────────────────────

export type DteBucket = 'short' | 'core' | 'extend' | 'far' | 'unknown'
/** 观察=单边信号; 可排单=规则+可交易; 优先=可排单+触线确认 */
export type TradeTier = 'WATCH' | 'QUEUE' | 'PRIORITY' | 'MANAGE'

export const DTE_BUCKET_META: Record<DteBucket, { label: string; order: number }> = {
  core: { label: '核心21-35', order: 0 },
  extend: { label: '延伸36-45', order: 1 },
  short: { label: '短端14-20', order: 2 },
  far: { label: '远月>45', order: 3 },
  unknown: { label: 'DTE未知', order: 4 },
}

export function dteBucket(dte?: number | null): DteBucket {
  if (dte == null || Number.isNaN(dte)) return 'unknown'
  if (dte >= 21 && dte <= 35) return 'core'
  if (dte >= 36 && dte <= 45) return 'extend'
  if (dte >= 14 && dte <= 20) return 'short'
  if (dte > 45) return 'far'
  if (dte < 14) return 'short'
  return 'unknown'
}

/** 日租($/万保/天) ≈ premium*10000/(DTE*strike); premium 可用 bid 或触线价 */
export function dailyRentPer10k(bid?: number | null, strike?: number | null, dte?: number | null): number | null {
  if (bid == null || strike == null || dte == null || bid <= 0 || strike <= 0 || dte <= 0) return null
  return (bid * 10000) / (dte * strike)
}

/** 缺 annualized 时用 bid/strike 粗算预期年化% */
export function estimateAnnualized(bid?: number | null, strike?: number | null, dte?: number | null): number | null {
  if (bid == null || strike == null || dte == null || bid <= 0 || strike <= 0 || dte <= 0) return null
  return (bid / strike) * (365 / dte) * 100
}

/** 年化/|Δ|,Δ过小截断避免失真;无Δ时仍返回年化本身便于展示 */
export function annPerDelta(annualized?: number | null, delta?: number | null): number | null {
  if (annualized == null) return null
  if (delta == null || delta === 0) return annualized // 无Δ时不除,展示为近似
  const d = Math.max(Math.abs(delta), 0.05)
  return annualized / d
}

export type TradeabilityInput = {
  bid?: number | null
  spread_pct?: number | null
  open_interest?: number | null
  min_oi?: number
  max_spread_pct?: number
  suggest_qty?: number
  side?: 'PUT' | 'CALL'
  strike?: number | null
  exceeds_capital?: boolean
}

export function evaluateTradeability(p: TradeabilityInput): { ok: boolean; reasons: string[] } {
  const reasons: string[] = []
  const maxSpread = p.max_spread_pct ?? 10
  const minOi = p.min_oi ?? 100
  if (p.bid == null || p.bid <= 0) reasons.push('无买价')
  if (p.spread_pct != null && p.spread_pct > maxSpread) reasons.push(`点差${p.spread_pct}%过高`)
  if (p.open_interest != null && p.open_interest < minOi) reasons.push(`OI ${p.open_interest}<${minOi}`)
  if (p.suggest_qty != null && p.suggest_qty < 1 && p.side === 'PUT') reasons.push('额度不足(张数=0)')
  if (p.exceeds_capital) reasons.push('超标的资金上限')
  return { ok: reasons.length === 0, reasons }
}

export function resolveTradeTier(p: {
  kind: 'OPEN' | 'MANAGE'
  hasRanked: boolean
  hasTouch: boolean
  tradeable: boolean
  risk_block: boolean
  ema_type?: string | null
  iv_rank?: number | null
  covers_earnings?: boolean
  demote_earnings?: boolean
}): TradeTier {
  if (p.kind === 'MANAGE') return 'MANAGE'
  if (p.risk_block || !p.tradeable) return 'WATCH'
  // 优先:可交易高分 + 触线;强触线可放宽到高分可交易
  const strongTouch = p.ema_type === 'EMA200' || (p.iv_rank ?? 0) >= 50
  if (p.hasRanked && p.tradeable && p.hasTouch) {
    if (p.covers_earnings && p.demote_earnings) return 'QUEUE' // 含财报不进优先
    return 'PRIORITY'
  }
  if (p.hasRanked && p.tradeable && strongTouch && p.hasTouch) return 'PRIORITY'
  if (p.hasRanked && p.tradeable) return 'QUEUE'
  return 'WATCH'
}

export function explainOpenOpp(row: {
  categories: string[]
  strength: string
  trade_tier?: string
  tradeable?: boolean
  kill_reasons?: string[]
  dte_bucket?: string
  daily_rent?: number | null
  ann_per_delta?: number | null
  annualized?: number | null
  score?: number | null
  risk_hard: string[]
  risk_soft: string[]
  ema_type?: string | null
  tags: string[]
  actionable: boolean
  covers_earnings?: boolean
}): string[] {
  const reasons: string[] = []
  if (row.trade_tier === 'PRIORITY') reasons.push('档位=优先:规则过线且触线确认,默认可执行')
  else if (row.trade_tier === 'QUEUE') reasons.push('档位=可排单:规则与流动性过关,未触线或事件降权')
  else if (row.trade_tier === 'WATCH') reasons.push('档位=观察:仅单边信号或未过可交易门槛')
  if (row.kill_reasons?.length) reasons.push(`不可交易:${row.kill_reasons.join(',')}`)
  if (row.categories.includes('RANKED') && row.categories.includes('EMA_TOUCH')) {
    reasons.push('高分∩触线双轨对齐')
  } else if (row.categories.includes('RANKED')) {
    reasons.push('通过 delta/DTE/年化/点差硬过滤')
  } else if (row.categories.includes('EMA_TOUCH')) {
    reasons.push(row.ema_type === 'EMA200' ? '合约价触及 EMA200' : '合约价触及 EMA50')
  }
  if (row.daily_rent != null) reasons.push(`日租 ${row.daily_rent.toFixed(2)} $/万保/天(同DTE桶排序主键)`)
  if (row.ann_per_delta != null) reasons.push(`年化/Δ ${row.ann_per_delta.toFixed(1)}`)
  if (row.covers_earnings) reasons.push('存续盖住财报:默认不进优先档')
  if (row.dte_bucket === 'far') reasons.push('远月桶:易出现假高年化,默认折叠')
  if (row.annualized != null && row.annualized < 12) reasons.push(`预期年化仅 ${row.annualized.toFixed(1)}%`)
  for (const r of row.risk_hard) reasons.push(`阻断:${r}`)
  for (const r of row.risk_soft.slice(0, 2)) reasons.push(`注意:${r}`)
  return reasons.slice(0, 5)
}

export function scanFailureHint(errMsg: string): { title: string; tips: string[] } {
  const m = (errMsg || '').toLowerCase()
  if (m.includes('opend') || m.includes('连接') || m.includes('11111') || m.includes('拒绝')) {
    return {
      title: 'OpenD 未连接',
      tips: ['启动富途 OpenD', '设置页检查 Host/Port', '连上后点「强制刷新」'],
    }
  }
  if (m.includes('限频') || m.includes('freq') || m.includes('quota')) {
    return {
      title: '行情限频',
      tips: ['稍等 1–2 分钟再扫', '减少同时请求', '设置页增大标的间隔'],
    }
  }
  if (m.includes('没有') || m.includes('无') || m.includes('empty')) {
    return {
      title: '无满足条件合约',
      tips: ['标的设置放宽 delta/DTE', '降低最低年化', '检查接货底线是否过高'],
    }
  }
  return {
    title: '扫描异常',
    tips: ['点重试', '查看触线诊断', '确认网络与代理'],
  }
}

/** 组合经营指标(前端汇总) */
export function computeOpsMetrics(params: {
  trades: { trade_type: string; qty: number; price: number; fee: number; contract_size: number; traded_at: string }[]
  cycles: { status: string; realized_pnl?: number | null; started_at?: string; closed_at?: string | null; total_premium?: number }[]
  capital?: { total_committed: number; assignment_stress: number; csp_collateral: number; holding_cost: number } | null
  conversion?: { rate_pct: number; signal_count_30d: number; converted_30d: number } | null
  idleCount: number
  uncoveredCount: number
}) {
  const budget = getPortfolioBudget()
  const committed = params.capital?.total_committed ?? 0
  const util = budget > 0 ? committed / budget : 0
  const closed = params.cycles.filter(c => c.status === 'CLOSED')
  let turnDays: number | null = null
  if (closed.length) {
    const days = closed.map(c => {
      try {
        const a = new Date(c.started_at || 0).getTime()
        const b = new Date(c.closed_at || c.started_at || 0).getTime()
        return Math.max(1, (b - a) / 86400000)
      } catch { return 30 }
    })
    turnDays = days.reduce((s, d) => s + d, 0) / days.length
  }
  // 本月权利金里,买回平仓对应的「提前兑现」粗算:平仓腿绝对金额 / (卖出+平仓绝对额)
  const month = new Date().toISOString().slice(0, 7)
  let sellPrem = 0, closePrem = 0
  for (const t of params.trades) {
    if (!t.traded_at?.startsWith(month)) continue
    const cash = t.qty * t.price * t.contract_size
    if (t.trade_type === 'SELL_PUT' || t.trade_type === 'SELL_CALL') sellPrem += cash
    if (t.trade_type === 'BUY_PUT_CLOSE' || t.trade_type === 'BUY_CALL_CLOSE') closePrem += cash
  }
  const earlyCloseShare = sellPrem + closePrem > 0 ? closePrem / (sellPrem + closePrem) : null

  return {
    budget,
    committed,
    available: Math.max(0, budget - committed),
    utilization: util,
    assignment_stress: params.capital?.assignment_stress ?? 0,
    turn_days: turnDays,
    early_close_share: earlyCloseShare,
    conversion_rate: params.conversion?.rate_pct ?? null,
    idle_count: params.idleCount,
    uncovered_count: params.uncoveredCount,
  }
}
