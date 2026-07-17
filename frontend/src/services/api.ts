export const API_BASE = 'http://127.0.0.1:8000'
const SETTINGS_KEY = 'tradeforge.settings'
const SETTINGS_EVENT = 'tradeforge:settings-changed'
export type {
  AdapterType,
  AppSettings,
  KlineBar,
  OrderPayload,
  OrderSide,
  OrderType,
  QuoteData,
  SearchStockResult,
  StockItem,
  Plan2032Holding,
  StrategySignal,
  StrategySummary,
  TradingEnv,
  TradingOrder,
} from './types'
import type {
  AppSettings,
  KlineBar,
  OrderPayload,
  QuoteData,
  SearchStockResult,
  StockItem,
  Plan2032Holding,
  StrategySignal,
  StrategySummary,
  TradingOrder,
} from './types'

const DEFAULT_SETTINGS: AppSettings = {
  initialCapital: 100000,
  feeRate: 0.0003,
  slippage: 0.001,
  theme: 'dark',
  language: 'zh',
  marketDataSource: 'finnhub',
  marketHost: '127.0.0.1',
  marketPort: 11111,
  tradingAdapter: 'futu',
  tradingEnv: 'SIM',
  tradingHost: '127.0.0.1',
  tradingPort: 11111,
  defaultOrderQuantity: 100,
  confirmSignals: true,
  refreshIntervalSec: 0,
}

function parseJson<T>(text: string): T | null {
  if (!text) return null
  try {
    return JSON.parse(text) as T
  } catch {
    return null
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  const text = await res.text()
  const data = parseJson<any>(text)

  if (!res.ok) {
    const detail = data?.detail || data?.message || text || `请求失败: ${res.status}`
    throw new Error(detail)
  }

  return (data ?? ({} as T)) as T
}

export function getAppSettings(): AppSettings {
  const raw = localStorage.getItem(SETTINGS_KEY)
  const parsed = raw ? parseJson<Partial<AppSettings>>(raw) : null
  const merged = { ...DEFAULT_SETTINGS, ...(parsed || {}) }
  // Migrate away from removed 'mock' adapter
  if ((merged.marketDataSource as string) === 'mock') merged.marketDataSource = 'finnhub'
  if ((merged.tradingAdapter as string) === 'mock') merged.tradingAdapter = 'futu'
  return merged
}

export function saveAppSettings(next: Partial<AppSettings> | AppSettings): AppSettings {
  const merged = {
    ...getAppSettings(),
    ...next,
  }
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged))
  window.dispatchEvent(new CustomEvent(SETTINGS_EVENT, { detail: merged }))
  return merged
}

export function subscribeSettings(callback: (settings: AppSettings) => void) {
  const handler = (event: Event) => {
    const customEvent = event as CustomEvent<AppSettings>
    callback(customEvent.detail || getAppSettings())
  }
  window.addEventListener(SETTINGS_EVENT, handler)
  return () => window.removeEventListener(SETTINGS_EVENT, handler)
}

function buildMarketQuery(params: Record<string, string | number | undefined>) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      qs.set(key, String(value))
    }
  })
  return qs.toString()
}

export async function getMarketStatus(settings = getAppSettings()) {
  const qs = buildMarketQuery({
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<{ connected: boolean; adapter: string; host: string; port: number }>(`/api/market/status?${qs}`)
}

export async function searchStocks(q: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    q,
    adapter: settings.marketDataSource,
  })
  return request<SearchStockResult[]>(`/api/market/search?${qs}`)
}

export async function getQuote(symbol: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<QuoteData>(`/api/market/quote?${qs}`)
}

export async function getQuotes(symbols: string[], settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbols: symbols.join(','),
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<{ items: QuoteData[] }>(`/api/market/quotes?${qs}`)
}

export async function getKlines(symbol: string, timeframe: string = '1d', limit: number = 365, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    timeframe,
    limit,
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<KlineBar[]>(`/api/market/klines?${qs}`)
}

export async function getOptionExpirations(symbol: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<{ symbol: string; expirations: string[]; adapter: string }>(`/api/options/expirations?${qs}`)
}

export async function getOptionChain(symbol: string, expiry: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    expiry,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<any>(`/api/options/chain?${qs}`)
}

export async function getOptionPayoff(data: any) {
  return request<any>(`/api/options/payoff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function connectTrading(settings = getAppSettings()) {
  return request<{ status: string; adapter: string }>(`/api/trading/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      adapter: settings.tradingAdapter,
      trd_env: settings.tradingEnv,
      host: settings.tradingHost,
      port: settings.tradingPort,
    }),
  })
}

export async function disconnectTrading() {
  return request<{ status: string }>(`/api/trading/disconnect`, {
    method: 'POST',
  })
}

export async function placeOrder(payload: OrderPayload) {
  return request<{ order_id: string; status: string }>(`/api/trading/order`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function getOrders() {
  return request<TradingOrder[]>(`/api/trading/orders`)
}

export async function getStrategies() {
  return request<StrategySummary[]>(`/api/strategies`)
}

export async function getStrategy(id: string) {
  return request<StrategySummary>(`/api/strategies/${id}`)
}

export async function evaluateStrategySignal(strategyId: string, symbol: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<StrategySignal>(`/api/strategies/${strategyId}/signal?${qs}`)
}

export async function createStrategy(data: any) {
  return request<any>(`/api/strategies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function updateStrategy(id: string, data: any) {
  return request<any>(`/api/strategies/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function deleteStrategy(id: string) {
  return request<any>(`/api/strategies/${id}`, {
    method: 'DELETE'
  })
}

export async function runBacktest(data: any) {
  return request<any>(`/api/backtest/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function getBacktestResult(id: string) {
  return request<any>(`/api/backtest/${id}`)
}

export async function getBacktestTrades(id: string) {
  return request<any>(`/api/backtest/${id}/trades`)
}

export async function getHistoryCoverage(symbol: string, timeframe: string, source?: string) {
  const qs = buildMarketQuery({ symbol, timeframe, source })
  return request<any>(`/api/history/coverage?${qs}`)
}

export async function getHistoryJobs(limit: number = 50) {
  const qs = buildMarketQuery({ limit })
  return request<any[]>(`/api/history/jobs?${qs}`)
}

export async function previewHistorySource(symbol: string, adapter?: string) {
  const qs = buildMarketQuery({ symbol, adapter })
  return request<{ symbol: string; source: string }>(`/api/history/preview-source?${qs}`)
}

export async function backfillHistory(data: {
  symbol: string
  timeframe: string
  start_date: string
  end_date: string
  host?: string
  port?: number
  source?: string
}) {
  return request<any>(`/api/history/backfill`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function getHistorySubscriptions(enabledOnly: boolean = false) {
  const qs = buildMarketQuery({ enabled_only: enabledOnly ? 'true' : undefined })
  return request<any[]>(`/api/history/subscriptions${qs ? `?${qs}` : ''}`)
}

export async function addHistorySubscription(data: { symbol: string; name?: string; source_hint?: string; enabled?: boolean }) {
  return request<any>(`/api/history/subscriptions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function setHistorySubscriptionEnabled(symbol: string, enabled: boolean) {
  const qs = buildMarketQuery({ enabled: enabled ? 'true' : 'false' })
  return request<any>(`/api/history/subscriptions/${encodeURIComponent(symbol)}/enable?${qs}`, {
    method: 'POST',
  })
}

export async function getHistorySchedulerStatus() {
  return request<any>(`/api/history/scheduler/status`)
}

export async function runHistoryScheduler(settings = getAppSettings()) {
  const qs = buildMarketQuery({ host: settings.marketHost, port: settings.marketPort })
  return request<any>(`/api/history/scheduler/run?${qs}`, {
    method: 'POST',
  })
}

export async function getStocks(params?: { market?: string; enabled_only?: boolean; subscribed?: boolean }) {
  const qs = buildMarketQuery({
    market: params?.market,
    enabled_only: params?.enabled_only ? 'true' : undefined,
  })
  return request<StockItem[]>(`/api/stocks${qs ? `?${qs}` : ''}`)
}

export async function addStock(data: { symbol: string; name: string; market: string }) {
  return request<StockItem>(`/api/stocks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function deleteStock(symbol: string) {
  return request<{ ok: boolean }>(`/api/stocks/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}

export async function setStockEnabled(symbol: string, enabled: boolean) {
  return request<{ ok: boolean }>(`/api/stocks/${encodeURIComponent(symbol)}/enable?enabled=${enabled}`, {
    method: 'POST',
  })
}

export async function setStockSubscribed(symbol: string, subscribed: boolean) {
  return request<{ ok: boolean }>(`/api/stocks/${encodeURIComponent(symbol)}/subscribe?subscribed=${subscribed}`, {
    method: 'POST',
  })
}

// ── LEAPS 信号监控 ────────────────────────────────────────────────────────────

export interface LeapsWatchlistItem {
  symbol: string
  name: string
  floor_price: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface LeapsSuggestion {
  contract_code: string
  strike: number
  expiry: string
  premium: number
  delta: number
  annualized_yield: number
  cost_basis: number
  dte: number
}

export interface LeapsSignal {
  id: string
  symbol: string
  contract_code: string
  signal_level: 'PRIMARY' | 'SECONDARY' | 'WHEEL_PUT' | 'WHEEL_CALL'
  trigger_price: number
  ema_value: number
  ema_type: string
  iv_rank: number
  underlying_price: number
  floor_price: number
  suggestions: LeapsSuggestion[]
  is_intraday: boolean
  created_at: string
}

export interface LeapsCooldown {
  contract_code: string
  symbol: string
  cooldown_until: string
  created_at: string
  updated_at: string
}

export interface LeapsStatus {
  watchlist_total: number
  watchlist_enabled: number
  recent_signals: LeapsSignal[]
  active_cooldowns: number
}

export interface LeapsCandidate {
  symbol: string
  name: string
  market: string
  /** 股票池是否启用 */
  enabled?: boolean
  /** 是否已是 Wheel 标的 */
  in_wheel?: boolean
}

export async function getLeapsWatchlist() {
  return request<LeapsWatchlistItem[]>('/api/leaps/watchlist')
}

export async function getLeapsCandidates() {
  return request<LeapsCandidate[]>('/api/leaps/watchlist/candidates')
}

export async function addLeapsWatchlistItem(body: {
  symbol: string
  name?: string
  floor_price: number
  enabled?: boolean
}) {
  return request<LeapsWatchlistItem>('/api/leaps/watchlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function deleteLeapsWatchlistItem(symbol: string) {
  return request<{ ok: boolean }>(`/api/leaps/watchlist/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}

// ── Wheel 策略 ────────────────────────────────────────────────────────────────

export type WheelCycleStatus = 'IDLE' | 'CSP_OPEN' | 'HOLDING' | 'CC_OPEN' | 'CLOSED'

export type WheelTradeType =
  | 'SELL_PUT' | 'BUY_PUT_CLOSE' | 'SELL_CALL' | 'BUY_CALL_CLOSE'
  | 'EXPIRE' | 'ASSIGNED' | 'CALLED_AWAY' | 'SELL_SHARES' | 'BUY_SHARES'

export interface WheelCycle {
  id: string
  symbol: string
  status: WheelCycleStatus
  shares: number
  share_cost: number
  total_premium: number
  total_fees: number
  realized_pnl: number | null
  open_contract_code: string | null
  open_option_type: 'PUT' | 'CALL' | null
  open_strike: number | null
  open_expiry: string | null
  open_qty: number
  open_price: number
  open_contract_size: number
  started_at: string
  closed_at: string | null
  cost_basis: number | null
  open_dte: number | null
  duration_days: number | null
  uncovered_days?: number | null
}

export interface WheelTarget {
  symbol: string
  name: string
  market: string
  floor_price: number
  max_capital: number
  delta_min: number
  delta_max: number
  dte_min: number
  dte_max: number
  min_annualized: number
  min_open_interest: number
  enabled: boolean | number
  active_cycles?: WheelCycle[]
  idle_days?: number | null
  volatility_brief?: {
    atm_iv: number | null
    iv_date: string | null
    hv20: number | null
    iv_rank: number | null
    iv_rank_source: 'iv_history' | 'hv_proxy' | null
    iv_hv_ratio: number | null
  } | null
}

export interface WheelTrade {
  id: string
  cycle_id: string
  symbol: string
  trade_type: WheelTradeType
  contract_code: string | null
  strike: number | null
  expiry: string | null
  qty: number
  price: number
  fee: number
  contract_size: number
  note: string | null
  traded_at: string
  is_roll?: boolean
}

export interface WheelCapital {
  per_symbol: Record<string, { csp_collateral: number; holding_cost: number }>
  csp_collateral: number
  holding_cost: number
  total_committed: number
  assignment_stress: number
}

export interface WheelStats {
  active_cycles: number
  closed_cycles: number
  premium_month: number
  premium_total: number
  realized_pnl_total: number
  expiring_soon: { symbol: string; open_contract_code: string; open_option_type: string; open_strike: number; open_expiry: string; dte: number }[]
  monthly_premium?: { ym: string; premium: number }[]
  symbol_ranking?: {
    symbol: string; premium: number; realized_pnl: number
    closed_cycles: number; first_trade: string; active_days: number | null
  }[]
  capital?: WheelCapital
  /** 近30日触线信号 → 同合约卖出登记转化 */
  conversion?: {
    signal_count_30d: number
    converted_30d: number
    rate_pct: number
    avg_signal_to_trade_hours: number | null
  }
}

export interface WheelSuggestion {
  contract_code: string
  expiry: string
  dte: number
  strike: number
  delta: number
  bid: number
  ask: number | null
  premium_used?: number
  premium_pricing?: string
  iv: number | null
  open_interest: number
  volume: number
  contract_size: number
  annualized: number
  annualized_cash?: number
  annualized_margin?: number | null
  spread_pct?: number | null
  covers_earnings?: boolean
  pop?: number
  ev_pct?: number | null
  robust_score?: number
  buffer_atr?: number | null
  limit_price_hint?: number
  otm_pct: number
  assigned_cost?: number
  if_called_total?: number
  score?: number
  score_factors?: Record<string, number | null>
}

export interface TrendProfile {
  ema50: number | null
  ema200: number | null
  above_ema50: boolean | null
  above_ema200: boolean | null
  trend: 'UP' | 'WEAK' | 'DOWN'
  pct_vs_ema50: number | null
  pct_vs_ema200: number | null
}

export interface VolatilityProfile {
  symbol: string
  spot: number
  atm_iv: number | null       // 期望波动率(隐含) %
  hv20: number | null         // 实际波动率 20日 %
  hv60: number | null         // 实际波动率 60日 %
  ema20: number | null
  iv_rank: number | null      // 0-100,IV 历史不足时为 null
  iv_history_days: number
  iv_hv_ratio: number | null
  kline_days: number
  expiry_used?: string
}

export interface WheelSuggestResponse {
  symbol: string
  side: 'PUT' | 'CALL'
  spot_price: number | null
  cost_basis: number | null
  filters: Record<string, unknown>
  suggestions: WheelSuggestion[]
  message?: string
  volatility?: VolatilityProfile | null
  term_structure?: {
    shape?: string | null
    term_spread?: number | null
    near?: { expiry?: string; dte?: number; atm_iv?: number }
    next?: { expiry?: string; dte?: number; atm_iv?: number }
    hint?: string | null
  } | null
  skew?: {
    put_skew?: number | null
    call_skew?: number | null
    warn?: string | null
  } | null
  margin_ratio?: number
  earnings_date?: string | null
  days_to_earnings?: number | null
  earnings_warn?: boolean
  earnings_filtered_count?: number
  dividend_warn?: { date: string; days_to_ex: number; amount?: number } | null
  delta_preference?: string | null
  trend?: TrendProfile | null
  trend_warning?: string | null
  headroom_ratio?: number | null
  floor_suggest?: {
    suggested_floor: number
    current_floor?: number
    rationale?: string
  } | null
  call_anchors?: {
    anchors: { label: string; strike: number; note: string }[]
    tip?: string
  } | null
}

/** 持仓决策 action_code(与后端 wheel_decision 对齐) */
export type WheelActionCode =
  | 'CLOSE' | 'ROLL' | 'ROLL_ADJUST' | 'HOLD_THETA' | 'REPLACE'
  | 'PREPARE_ASSIGN' | 'NONE' | string

export interface WheelOpenPositionItem {
  cycle_id: string
  symbol: string
  side: 'PUT' | 'CALL'
  contract_code: string
  strike: number
  expiry: string | null
  dte: number | null
  open_price: number
  current_price: number
  buyback_ask: number
  delta?: number
  theta?: number
  remaining_annualized?: number | null
  low_yield?: boolean
  roll_21dte?: boolean
  deep_itm?: boolean
  shallow_itm?: boolean
  early_assign_risk?: boolean
  action_hint?: string | null
  /** CLOSE|ROLL|ROLL_ADJUST|HOLD_THETA|REPLACE|PREPARE_ASSIGN|NONE */
  action_code?: WheelActionCode | null
  action_priority?: number
  /** 次要提示(如吃θ时仍可止盈腾仓) */
  secondary_hint?: string | null
  /** no_roll | roll_out | adjust_strike */
  prefer_card?: string | null
  /** 0–100:规则越硬、证据越足越高 */
  decision_confidence?: number | null
  thin_otm?: boolean
  otm_buffer_pct?: number | null
  /** CSP: strike 是否高于接货底线 */
  strike_above_floor?: boolean
  floor_price?: number | null
  /** 组合资金占用偏紧 */
  capital_tight?: boolean
  capital_util_pct?: number | null
  portfolio_put_blocked?: boolean
  symbol_headroom?: number | null
  /** CSP 平仓约释放担保金 */
  freed_capital_est?: number | null
  /** 换仓/平仓后下一步文案 */
  replace_hint?: string | null
  /** yes|no|caution|unknown — 以今天纪律还会不会新开此腿 */
  would_open_today?: 'yes' | 'no' | 'caution' | 'unknown' | string | null
  would_open_reasons?: string[]
  /** 接货/交货清单 */
  assign_checklist?: {
    side?: string
    strike?: number
    assign_notional?: number
    collateral_covers?: boolean | null
    floor_ok?: boolean | null
    floor_price?: number | null
    post_holding_pct?: number | null
    over_symbol_cap?: boolean | null
    next_step_hint?: string | null
    notes?: string[]
    qty?: number
    contract_size?: number
  } | null
  reasons?: string[]
  profit_pct: number | null
  spot: number
  itm: boolean
  profit_hit: boolean
  expiring: boolean
  dividend_warn?: { date: string; days_to_ex: number } | null
  decision_tree?: Record<string, unknown>
  moneyness_pct?: number
}

export interface WheelPortfolioContext {
  utilization_pct?: number | null
  capital_tight?: boolean
  portfolio_put_blocked?: boolean
  idle_cash?: number | null
  over_portfolio?: boolean
  equity?: number | null
  assignment_stress?: number | null
  capital_tight_util_pct?: number
}

export async function checkWheelOpenPositions(host: string, port: number) {
  return request<{
    items: WheelOpenPositionItem[]
    profit_target_pct: number
    portfolio_context?: WheelPortfolioContext
  }>(
    `/api/wheel/open-positions/check?host=${encodeURIComponent(host)}&port=${port}`
  )
}

export interface WheelRollPricing {
  close_price: number
  open_price: number
  net_credit_per_share: number
  net_credit_per_contract: number
}

export interface WheelRollCandidate {
  contract_code: string
  expiry: string
  dte: number
  strike: number
  delta: number | null
  bid: number
  ask?: number
  spread_pct?: number | null
  open_interest?: number
  net_credit_per_contract: number
  net_credit_conservative?: number
  credit_per_day?: number
  annualized: number | null
  branch?: string
  band?: 'preferred' | 'wide' | 'fallback' | string
  same_strike?: boolean
  worse_direction?: boolean
  rank_score?: number
  pricing?: {
    optimistic: WheelRollPricing
    default: WheelRollPricing
    conservative: WheelRollPricing
  }
  if_called_total?: number | null
  if_assigned_cost?: number | null
  new_cost_basis_est?: number | null
  covers_earnings?: boolean
  covers_dividend?: boolean
  limit_hints?: {
    close_limit: number
    open_limit: number
    net_credit_target: number
    note?: string
  }
  preview?: Record<string, unknown>
  draft_legs?: {
    trade_type: string
    contract_code?: string
    strike?: number
    expiry?: string
    qty?: number
    price?: number
    is_roll?: boolean
  }[]
}

export interface WheelRollCard {
  key: string
  title: string
  available?: boolean
  blurb?: string
  summary?: string
  candidate?: WheelRollCandidate | null
  pros?: string[]
  cons?: string[]
  options?: {
    close_now?: {
      action: string
      buyback_cost_per_contract?: number
      locked_premium_est?: number | null
      pros?: string[]
      cons?: string[]
      when?: string
    }
    let_expire?: {
      action: string
      buyback_cost_per_contract?: number
      pros?: string[]
      cons?: string[]
      when?: string
    }
  }
  recommended_sub?: string
}

export interface WheelRollOptions {
  cycle_id: string
  symbol: string
  side: 'PUT' | 'CALL'
  spot_price?: number | null
  qty?: number
  allow_down_strike?: boolean
  decision?: {
    headline?: string
    detail?: string
    recommended_action?: string
    scenario?: string
    prefer_card?: string
    profit_pct?: number | null
    remaining_annualized?: number | null
    itm?: boolean
    deep_itm?: boolean
  }
  cards?: {
    roll_out?: WheelRollCard
    adjust_strike?: WheelRollCard
    no_roll?: WheelRollCard
  }
  highlighted_card?: string
  default_candidate?: WheelRollCandidate | null
  strike_floor?: {
    call_min_strike?: number | null
    cost_basis?: number | null
    share_cost?: number | null
    put_max_strike?: number | null
    rule?: string
  }
  delta_filter?: {
    mode: string
    preferred: [number, number]
    target: [number, number]
    hard_max: number
    current_delta: number
  }
  liquidity?: { max_spread_pct: number; min_oi: number }
  events?: { earnings_date?: string | null; dividend?: { date?: string; amount?: number } | null }
  pricing_legend?: Record<string, string>
  current: {
    contract_code: string
    strike: number
    expiry: string
    dte: number | null
    open_price: number
    buyback_ask: number
    buyback_bid?: number
    delta: number
    contract_size: number
    cost_basis?: number | null
    share_cost?: number | null
    shares?: number
    profit_pct?: number | null
    remaining_annualized?: number | null
    itm?: boolean
  }
  candidates: WheelRollCandidate[]
  debit_candidates?: WheelRollCandidate[]
  branches?: Record<string, WheelRollCandidate[]>
  same_strike_highlights?: WheelRollCandidate[]
  roll_history?: {
    date: string
    net_credit: number
    close_strike?: number
    open_strike?: number
    open_expiry?: string
  }[]
  alternatives?: {
    let_expire?: { buyback_cost_per_contract?: number; when?: string; pros?: string[] }
    close_now?: { buyback_cost_per_contract?: number; locked_premium_est?: number | null; when?: string }
  }
  warnings?: string[]
  skipped_counts?: Record<string, number>
}

// ── 后端配置(存本地数据库,含敏感信息) ─────────────────────────────────────────

export interface BackendConfig {
  telegram: { bot_token: string; chat_id: string; proxy?: string }
  finnhub_api_key: string
  finnhub_base_url: string
  yahoo_base_url: string
  futu: { host: string; port: number }
  wheel_timing: {
    dte_min: number
    dte_max: number
    contract_max_per_symbol: number
    iv_percentile_threshold: number
    cooldown_trading_days: number
    auto_scan_minutes: number
    align_target_dte?: boolean
    dte_pad_days?: number
    push_strong_only?: boolean
    push_min_iv_rank?: number
    /** 相对现价下方幅度 0.20=20% → strike ≥ spot×0.8 */
    strike_range_down?: number
    /** 相对现价上方幅度 0.10=10% → strike ≤ spot×1.1 */
    strike_range_up?: number
    /** 每标的最多扫几个到期日(默认6;旧为3) */
    max_expiries?: number
    prefer_core_dte?: boolean
    ema50_min_bars?: number
    ema200_min_bars?: number
    allow_partial_ema?: boolean
  }
  wheel_position: {
    profit_target_pct: number
    margin_ratio: number
    earnings_warn_days: number
    weekly_report: boolean
    soft_profit_pct?: number
    hard_roll_dte?: number
    gamma_warn_dte?: number
    hold_theta_min_profit_pct?: number
    dividend_warn_days?: number
    alert_push_minutes?: number
    notify_mode?: 'realtime' | 'digest' | string
  }
  wheel_scan?: {
    max_spread_pct: number
    spread_soft_pct: number
    earnings_penalty: number
    iv_rank_bonus: number
    trend_penalty_below_ema50: number
    trend_penalty_below_ema200: number
    top_per_symbol: number
    top_overall: number
    chain_cache_ttl_sec: number
    symbol_interval_sec: number
    auto_push_minutes: number
    telegram_top_n?: number
    earnings_hard_filter?: boolean
    premium_pricing?: 'mid' | 'bid'
    pop_weight?: number
    buffer_atr_min?: number
    buffer_weight?: number
    headroom_boost?: number
    sort_mode?: 'score' | 'robust'
    log_suggestions?: boolean
  }
  wheel_portfolio?: {
    total_equity?: number
    max_portfolio_pct?: number
    max_symbol_pct?: number
    high_corr_threshold?: number
  }
  wheel_profiles?: {
    active?: string
    presets?: Record<string, unknown>
  }
}

export async function getBackendConfig() {
  return request<BackendConfig>('/api/config/backend')
}

export async function saveBackendConfig(body: Partial<BackendConfig>) {
  return request<BackendConfig>('/api/config/backend', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function getWheelRollOptions(
  cycleId: string,
  host: string,
  port: number,
  opts?: { allow_down_strike?: boolean; max_spread_pct?: number; qty?: number },
) {
  const qs = new URLSearchParams({
    cycle_id: cycleId,
    host,
    port: String(port),
  })
  if (opts?.allow_down_strike) qs.set('allow_down_strike', 'true')
  if (opts?.max_spread_pct != null) qs.set('max_spread_pct', String(opts.max_spread_pct))
  if (opts?.qty != null) qs.set('qty', String(opts.qty))
  return request<WheelRollOptions>(`/api/wheel/roll-options?${qs}`)
}

export async function getWheelTargets() {
  return request<WheelTarget[]>('/api/wheel/targets')
}

export async function getWheelCandidates() {
  return request<LeapsCandidate[]>('/api/wheel/targets/candidates')
}

export async function addWheelTarget(body: Partial<WheelTarget> & { symbol: string; floor_price: number }) {
  return request<WheelTarget>('/api/wheel/targets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function updateWheelTarget(symbol: string, body: Partial<WheelTarget>) {
  return request<WheelTarget>(`/api/wheel/targets/${encodeURIComponent(symbol)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function deleteWheelTarget(symbol: string) {
  return request<{ ok: boolean }>(`/api/wheel/targets/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}

export async function getWheelCycles(symbol?: string) {
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
  return request<WheelCycle[]>(`/api/wheel/cycles${qs}`)
}

export async function getWheelTrades(params?: { cycle_id?: string; symbol?: string }) {
  const qs = new URLSearchParams()
  if (params?.cycle_id) qs.set('cycle_id', params.cycle_id)
  if (params?.symbol) qs.set('symbol', params.symbol)
  const s = qs.toString()
  return request<WheelTrade[]>(`/api/wheel/trades${s ? '?' + s : ''}`)
}

export async function recordWheelTrade(body: {
  symbol: string
  trade_type: WheelTradeType
  contract_code?: string
  strike?: number
  expiry?: string
  qty?: number
  price?: number
  fee?: number
  contract_size?: number
  note?: string
  traded_at?: string
  cycle_id?: string
  new_cycle?: boolean
}) {
  return request<WheelCycle>('/api/wheel/trades', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function updateWheelTrade(tradeId: string, body: {
  trade_type?: WheelTradeType
  contract_code?: string
  strike?: number
  expiry?: string
  qty?: number
  price?: number
  fee?: number
  contract_size?: number
  note?: string
  traded_at?: string
}) {
  return request<WheelCycle>(`/api/wheel/trades/${encodeURIComponent(tradeId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function deleteWheelTrade(tradeId: string) {
  return request<{ deleted: boolean }>(`/api/wheel/trades/${encodeURIComponent(tradeId)}`, {
    method: 'DELETE',
  })
}

export async function getWheelStats() {
  return request<WheelStats>('/api/wheel/stats')
}

export interface WheelScanOpportunity extends WheelSuggestion {
  symbol: string
  name?: string | null
  side: 'PUT' | 'CALL'
  cycle_id?: string | null
  spot_price: number | null
  trend?: 'UP' | 'WEAK' | 'DOWN' | null
  iv_rank?: number | null
  earnings_warn?: boolean
  exceeds_capital?: boolean
}

export interface WheelScanResult {
  scanned_at: string
  targets_scanned: number
  opportunities: WheelScanOpportunity[]
  total_found: number
  skipped: { symbol: string; reason: string }[]
  errors: { symbol: string; side: string; error: string }[]
  telegram_sent?: boolean
}

export async function getWheelPoolScan(host: string, port: number, refresh = false, useLast = false) {
  return request<WheelScanResult>(
    `/api/wheel/scan?host=${encodeURIComponent(host)}&port=${port}&refresh=${refresh}&use_last=${useLast}`
  )
}

export interface WheelScanProgress {
  running: boolean
  phase: 'idle' | 'pool' | 'done' | 'error' | string
  symbol?: string | null
  side?: string | null
  expiry?: string | null
  contract_i?: number
  contract_n?: number
  target_i?: number
  target_n?: number
  message?: string
  updated_at?: string | null
}

export async function getWheelScanProgress() {
  return request<WheelScanProgress>('/api/wheel/scan/progress')
}

export interface WheelContractQuote {
  symbol: string
  contract_code: string
  side?: string
  strike?: number
  expiry?: string
  bid?: number | null
  ask?: number | null
  last?: number | null
  delta?: number | null
  spot_price?: number | null
}

/** OpenD 补单合约实时报价 */
export async function getWheelContractQuote(
  symbol: string,
  contractCode: string,
  host: string,
  port: number,
  side?: 'PUT' | 'CALL',
) {
  const qs = new URLSearchParams({
    symbol,
    contract_code: contractCode,
    host,
    port: String(port),
  })
  if (side) qs.set('side', side)
  return request<WheelContractQuote>(`/api/wheel/quote?${qs}`)
}

/** 统一可交易机会流(触线+打分合流) */
export type OppFilter = 'actionable' | 'all' | 'dual' | 'timing' | 'score' | 'watch' | 'blocked'

export interface WheelOpportunity {
  id: string
  source: 'dual' | 'timing' | 'score' | string
  grade: 'dual' | 'timing' | 'score' | 'watch' | 'blocked' | string
  actionable: boolean
  symbol: string
  side: 'PUT' | 'CALL' | string
  contract_code?: string | null
  strike?: number | null
  expiry?: string | null
  dte?: number | null
  delta?: number | null
  bid?: number | null
  premium_used?: number | null
  spread_pct?: number | null
  annualized?: number | null
  score?: number | null
  score_factors?: Record<string, number | null>
  pop?: number | null
  iv_rank?: number | null
  trend?: string | null
  covers_earnings?: boolean
  exceeds_capital?: boolean
  flags?: string[]
  timing?: {
    ema_type?: string | null
    ema_value?: number | null
    trigger_price?: number | null
    strength?: 'STRONG' | 'READY' | 'WATCH' | string
    times_triggered?: number
    last_seen?: string
    below_floor?: boolean
  } | null
  cycle_id?: string | null
  contract_short?: string | null
  group_size?: number
  group_rank?: number
  /** 同标的同方向最优一条 */
  is_top_pick?: boolean
  /** 最近事件时间(触线 last_seen)，后端已按此倒序 */
  event_at?: string | null
  context?: {
    stage?: string
    headroom?: number | null
    max_capital?: number
    committed?: number
    cost_basis?: number | null
    floor_price?: number | null
  }
}

export interface WheelOpportunitiesResult {
  built_at: string
  headline: string
  summary: {
    actionable: number
    actionable_put: number
    actionable_call: number
    dual: number
    watch: number
    blocked: number
    total: number
    idle_slots: number
    min_score_threshold: number
    portfolio_put_blocked?: boolean
  }
  portfolio?: {
    portfolio_put_blocked?: boolean
    assignment_stress?: number
    utilization_pct?: number | null
    over_portfolio?: boolean
    stress_block?: boolean
    equity?: number | null
  }
  /** 每标的同方向主推 */
  primary_picks?: WheelOpportunity[]
  idle_slots: { symbol: string; headroom?: number | null; stage?: string }[]
  items: WheelOpportunity[]
  actionable_items?: WheelOpportunity[]
  pool?: {
    scanned_at?: string | null
    from_cache?: boolean
    error?: string | null
    targets_scanned?: number
    total_found?: number
  }
  rules?: Record<string, unknown>
  filter_applied?: { filter: string; side?: string | null; count: number }
}

export async function getWheelOpportunities(
  host: string,
  port: number,
  opts?: {
    refresh?: boolean
    run_pool?: boolean
    filter?: OppFilter
    side?: 'PUT' | 'CALL'
    hide_blocked?: boolean
  },
) {
  const qs = new URLSearchParams({
    host,
    port: String(port),
    refresh: String(!!opts?.refresh),
    run_pool: String(opts?.run_pool !== false),
    filter: opts?.filter || 'actionable',
    hide_blocked: String(opts?.hide_blocked !== false),
  })
  if (opts?.side) qs.set('side', opts.side)
  return request<WheelOpportunitiesResult>(`/api/wheel/opportunities?${qs}`)
}

export async function pushWheelPoolScan(host: string, port: number) {
  return request<WheelScanResult>(
    `/api/wheel/scan/push?host=${encodeURIComponent(host)}&port=${port}`,
    { method: 'POST' }
  )
}

export async function getWheelSuggest(symbol: string, side: 'put' | 'call', host: string, port: number, cycleId?: string) {
  const extra = cycleId ? `&cycle_id=${encodeURIComponent(cycleId)}` : ''
  return request<WheelSuggestResponse>(
    `/api/wheel/suggest/${side}?symbol=${encodeURIComponent(symbol)}&host=${encodeURIComponent(host)}&port=${port}${extra}`
  )
}

export async function updateLeapsWatchlistItem(
  symbol: string,
  data: { floor_price?: number; enabled?: boolean; name?: string }
) {
  return request<LeapsWatchlistItem>(`/api/leaps/watchlist/${encodeURIComponent(symbol)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function getVolatilityProfile(symbol: string, host: string, port: number) {
  return request<VolatilityProfile>(
    `/api/options/volatility?symbol=${encodeURIComponent(symbol)}&host=${encodeURIComponent(host)}&port=${port}`
  )
}

export async function triggerWheelTimingScan(symbol?: string) {
  return request<{ status: string }>('/api/leaps/wheel-scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol: symbol || null }),
  })
}

export interface WheelScanReportRow {
  symbol: string
  side: 'PUT' | 'CALL' | '-'
  spot?: number | null
  contracts?: number
  in_cooldown?: number
  no_history?: number
  bars_insufficient?: number
  iv_filtered?: number
  not_touching?: number
  signals?: number
  note?: string | null
  dte?: string
  core_dte?: string
  expiries_scanned?: string[]
  expiries_skipped?: string[]
  strike_lo?: number | null
  strike_hi?: number | null
  ema_partial_hits?: number
}

export interface WheelScanStatus {
  running: boolean
  started_at: string | null
  finished_at: string | null
  signals_found: number
  report: WheelScanReportRow[]
  error: string | null
  telegram_configured?: boolean
  telegram_sent?: number
  /** 触线细进度 */
  phase?: 'idle' | 'timing' | 'done' | 'error' | string
  symbol?: string | null
  side?: string | null
  expiry?: string | null
  contract_i?: number
  contract_n?: number
  target_i?: number
  target_n?: number
  message?: string
}

export async function getWheelScanStatus() {
  return request<WheelScanStatus>('/api/leaps/wheel-scan/status')
}

export interface WheelTimingHistoryItem {
  contract_code: string
  symbol: string
  side: 'PUT' | 'CALL'
  strike: number | null
  expiry: string | null
  ema_type: string | null
  ema_value: number | null
  trigger_price: number | null
  iv_rank: number | null
  underlying_price: number | null
  delta: number | null
  bid: number | null
  annualized: number | null
  dte: number | null
  below_floor: number | null
  times_triggered: number
  first_seen: string
  last_seen: string
}

export interface WheelTimingHistoryPage {
  total: number
  page: number
  page_size: number
  items: WheelTimingHistoryItem[]
}

export async function getWheelTimingHistory(page = 1, pageSize = 20, symbol?: string) {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (symbol) qs.set('symbol', symbol)
  return request<WheelTimingHistoryPage>(`/api/leaps/wheel-timing/history?${qs}`)
}

export async function getWheelTimingSignals(limit = 20) {
  return request<LeapsSignal[]>(`/api/leaps/signals?levels=WHEEL_PUT,WHEEL_CALL&limit=${limit}`)
}

// ── Wheel 优化:组合/对账/准入/回测/Profile ──────────────────────────────────

export async function getWheelPortfolio(equity?: number) {
  const qs = equity && equity > 0 ? `?equity=${equity}` : ''
  return request<{
    equity: number
    total_committed: number
    utilization_pct: number
    max_portfolio_pct: number
    over_portfolio: boolean
    idle_cash: number
    idle_pct: number
    per_symbol: {
      symbol: string
      committed: number
      max_capital: number
      headroom: number | null
      headroom_ratio: number | null
      pct_of_equity: number
      over_symbol_cap: boolean
      over_symbol_pct: boolean
    }[]
    violations: unknown[]
    assignment_stress: number
  }>(`/api/wheel/portfolio${qs}`)
}

export async function getWheelStress(equity?: number) {
  const qs = equity && equity > 0 ? `?equity=${equity}` : ''
  return request<{
    scenarios: {
      shock_pct: number
      csp_itm_count: number
      assign_capital_needed: number
      total_capital_if_assigned: number
      itm_positions: { symbol: string; strike: number; assign_cost: number }[]
    }[]
    equity_ref: number | null
    note?: string
  }>(`/api/wheel/portfolio/stress${qs}`)
}

export async function getWheelCorrelation() {
  return request<{
    symbols: string[]
    pairs: { a: string; b: string; corr: number }[]
    high_corr: { a: string; b: string; corr: number }[]
  }>('/api/wheel/portfolio/correlation')
}

export async function getWheelAdmission(symbol?: string) {
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
  return request<any>(`/api/wheel/admission${qs}`)
}

export async function getWheelFloorSuggest(symbol: string) {
  return request<{
    suggested_floor: number
    current_floor?: number
    spot?: number
    rationale?: string
    delta_vs_current?: number | null
  }>(`/api/wheel/floor-suggest?symbol=${encodeURIComponent(symbol)}`)
}

export async function getWheelHealth() {
  return request<{
    active_cycles: number
    closed_cycles: number
    premium_net_total: number
    realized_pnl_total: number
    win_rate: number | null
    avg_duration_days: number | null
    assign_rate: number | null
    called_away_rate: number | null
    symbol_heat: { symbol: string; closed_cycles: number; realized_pnl: number }[]
    tip?: string
  }>('/api/wheel/attribution/health')
}

export async function getWheelReconcile(host: string, port: number, trdEnv = 'SIMULATE') {
  return request<{
    ok: boolean
    error?: string
    diffs: { type: string; severity: string; message: string; symbol?: string; cycle_id?: string }[]
    drafts: Record<string, unknown>[]
    summary: { diff_count: number; draft_count: number; warnings: number }
    futu?: { option_count: number; stock_count: number; errors: string[] }
  }>(`/api/wheel/reconcile?host=${encodeURIComponent(host)}&port=${port}&trd_env=${encodeURIComponent(trdEnv)}`)
}

export async function applyWheelReconcileDraft(body: Record<string, unknown>) {
  return request('/api/wheel/reconcile/apply-draft', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function registerWheelRoll(body: {
  cycle_id: string
  buyback_price: number
  sell_contract_code: string
  sell_strike: number
  sell_expiry: string
  sell_price: number
  qty?: number
  fee_close?: number
  fee_open?: number
  contract_size?: number
}) {
  return request<WheelCycle>('/api/wheel/roll/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function runWheelBacktest(symbol: string, params?: Record<string, unknown>) {
  return request<{
    ok: boolean
    error?: string
    final_equity?: number
    total_return_pct?: number
    cagr_pct?: number
    max_drawdown_pct?: number
    assign_count?: number
    trade_count?: number
    note?: string
  }>('/api/wheel/backtest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, params }),
  })
}

export async function getWheelProfiles() {
  return request<{
    active: string
    presets: string[]
    detail: Record<string, unknown>
  }>('/api/wheel/profiles')
}

export async function activateWheelProfile(name: string) {
  return request<{ ok: boolean; active: string }>('/api/wheel/profiles/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export async function pushWheelPositionAlerts(host: string, port: number) {
  return request<{ sent: boolean; count: number }>(
    `/api/wheel/alerts/push?host=${encodeURIComponent(host)}&port=${port}`,
    { method: 'POST' },
  )
}

export async function getLeapsSignals(symbol?: string, limit = 50) {
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`
  return request<LeapsSignal[]>(`/api/leaps/signals${qs}`)
}

export async function getLeapsCooldowns() {
  return request<LeapsCooldown[]>('/api/leaps/cooldowns')
}

export async function getLeapsStatus() {
  return request<LeapsStatus>('/api/leaps/status')
}

export async function triggerLeapsScan(symbol?: string, is_intraday = false) {
  return request<{ status: string; symbol: string | null; is_intraday: boolean }>(
    '/api/leaps/scan',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol ?? null, is_intraday }),
    }
  )
}

export async function resendLeapsSignal(signalId: string) {
  return request<{ sent: boolean; reason?: string; message: string }>(
    `/api/leaps/signals/${encodeURIComponent(signalId)}/notify`
  )
}

// ── 2032 投资计划 ─────────────────────────────────────────────────────────────

export async function getPlan2032Holdings() {
  return request<Plan2032Holding[]>(`/api/plan2032/holdings`)
}

export async function savePlan2032Holdings(holdings: Plan2032Holding[]) {
  return request<Plan2032Holding[]>(`/api/plan2032/holdings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ holdings }),
  })
}
