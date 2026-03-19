export const API_BASE = 'http://127.0.0.1:8000'
const SETTINGS_KEY = 'tradeforge.settings'
const SETTINGS_EVENT = 'tradeforge:settings-changed'

export type AdapterType = 'mock' | 'futu' | 'finnhub'
export type TradingEnv = 'SIM' | 'REAL'

export interface AppSettings {
  initialCapital: number
  feeRate: number
  slippage: number
  theme: string
  language: string
  marketDataSource: AdapterType
  marketHost: string
  marketPort: number
  tradingAdapter: AdapterType
  tradingEnv: TradingEnv
  tradingHost: string
  tradingPort: number
  defaultOrderQuantity: number
  confirmSignals: boolean
}

export interface TradingStatus {
  connected: boolean
  adapter: string | null
}

export interface OrderPayload {
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price?: number
  order_type?: 'LIMIT' | 'MARKET' | 'STOP'
}

const DEFAULT_SETTINGS: AppSettings = {
  initialCapital: 100000,
  feeRate: 0.0003,
  slippage: 0.001,
  theme: 'dark',
  language: 'zh',
  marketDataSource: 'mock',
  marketHost: '127.0.0.1',
  marketPort: 11111,
  tradingAdapter: 'mock',
  tradingEnv: 'SIM',
  tradingHost: '127.0.0.1',
  tradingPort: 11111,
  defaultOrderQuantity: 100,
  confirmSignals: true,
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
  return {
    ...DEFAULT_SETTINGS,
    ...(parsed || {}),
  }
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
  return request<any[]>(`/api/market/search?${qs}`)
}

export async function getQuote(symbol: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<any>(`/api/market/quote?${qs}`)
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
  return request<any[]>(`/api/market/klines?${qs}`)
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

export async function getTradingStatus() {
  return request<TradingStatus>(`/api/trading/status`)
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
  return request<any[]>(`/api/trading/orders`)
}

export async function getPositions() {
  return request<any[]>(`/api/trading/positions`)
}

export async function getAccount() {
  return request<any>(`/api/trading/account`)
}

export async function getStrategies() {
  return request<any[]>(`/api/strategies`)
}

export async function getStrategy(id: string) {
  return request<any>(`/api/strategies/${id}`)
}

export async function evaluateStrategySignal(strategyId: string, symbol: string, settings = getAppSettings()) {
  const qs = buildMarketQuery({
    symbol,
    adapter: settings.marketDataSource,
    host: settings.marketHost,
    port: settings.marketPort,
  })
  return request<any>(`/api/strategies/${strategyId}/signal?${qs}`)
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
