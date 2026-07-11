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
  | 'EXPIRE' | 'ASSIGNED' | 'CALLED_AWAY' | 'SELL_SHARES'

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
  capital?: WheelCapital
}

export interface WheelSuggestion {
  contract_code: string
  expiry: string
  dte: number
  strike: number
  delta: number
  bid: number
  ask: number | null
  iv: number | null
  open_interest: number
  volume: number
  contract_size: number
  annualized: number
  annualized_margin?: number | null
  spread_pct?: number | null
  covers_earnings?: boolean
  otm_pct: number
  assigned_cost?: number
  if_called_total?: number
  score?: number
  score_factors?: {
    annualized: number
    liquidity: number
    spread_pct: number | null
    earnings: number
    trend: number
    iv_bonus: number
    delta_pref: number
  }
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
  margin_ratio?: number
  earnings_date?: string | null
  days_to_earnings?: number | null
  earnings_warn?: boolean
  delta_preference?: string | null
  trend?: TrendProfile | null
  trend_warning?: string | null
}

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
  profit_pct: number | null
  spot: number
  itm: boolean
  profit_hit: boolean
  expiring: boolean
}

export async function checkWheelOpenPositions(host: string, port: number) {
  return request<{ items: WheelOpenPositionItem[]; profit_target_pct: number }>(
    `/api/wheel/open-positions/check?host=${encodeURIComponent(host)}&port=${port}`
  )
}

export interface WheelRollCandidate {
  contract_code: string
  expiry: string
  dte: number
  strike: number
  delta: number
  bid: number
  net_credit_per_contract: number
  annualized: number | null
}

export interface WheelRollOptions {
  cycle_id: string
  symbol: string
  side: 'PUT' | 'CALL'
  current: {
    contract_code: string
    strike: number
    expiry: string
    dte: number | null
    open_price: number
    buyback_ask: number
    delta: number
    contract_size: number
  }
  candidates: WheelRollCandidate[]
  warnings?: string[]
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
  }
  wheel_position: {
    profit_target_pct: number
    margin_ratio: number
    earnings_warn_days: number
    weekly_report: boolean
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

export async function getWheelRollOptions(cycleId: string, host: string, port: number) {
  return request<WheelRollOptions>(
    `/api/wheel/roll-options?cycle_id=${encodeURIComponent(cycleId)}&host=${encodeURIComponent(host)}&port=${port}`
  )
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
