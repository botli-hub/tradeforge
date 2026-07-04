export type AdapterType = 'futu' | 'finnhub'
export type TradingEnv = 'SIM' | 'REAL'
export type OrderSide = 'BUY' | 'SELL'
export type OrderType = 'LIMIT' | 'MARKET' | 'STOP'

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
  refreshIntervalSec: number
}

export interface TradingStatus {
  connected: boolean
  adapter: string | null
}

export interface OrderPayload {
  symbol: string
  side: OrderSide
  quantity: number
  price?: number
  order_type?: OrderType
}

export interface SearchStockResult {
  symbol: string
  name: string
  market?: string
}

export interface KlineBar {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  adapter?: string
  source?: string
}

export interface QuoteData {
  symbol: string
  name: string
  price: number
  change: number
  change_pct: number
  volume: number
  amount: number
  bid: number
  ask: number
  high: number
  low: number
  open: number
  pre_close: number
  adapter?: string
  storage?: string
}

export interface StrategySummary {
  id: string
  name: string
  status?: string
  mode?: 'visual' | 'formula'
  version?: number
  timeframe?: string
  symbols?: string[]
  config?: {
    mode?: 'visual' | 'formula'
    version?: number | string
    symbols?: string[]
    timeframe?: string
    [key: string]: unknown
  }
  [key: string]: unknown
}

export interface StrategySignal {
  strategy_name: string
  signal: 'BUY' | 'SELL' | 'NONE'
  signal_key?: string
  reason?: string
  latest_bar?: {
    close?: number
  }
}

export interface TradingOrder {
  order_id: string
  symbol: string
  side: string
  price: number
  quantity: number
  status: string
  create_time: string
}

export interface TradingPosition {
  symbol: string
  direction: string
  quantity: number
  avg_cost: number
  current_price: number
  unrealized_pnl: number
}

export interface TradingAccount {
  cash: number
  buying_power: number
  market_value: number
  total_assets: number
}

export interface StockItem {
  symbol: string
  name: string
  market: 'US' | 'HK' | 'CN'
  enabled: boolean
  subscribed: boolean
  asset_type?: string
  source_symbol?: string
  currency?: 'USD' | 'HKD' | 'CNY'
  lot_size?: number | null
  status?: string
  source_hint?: string | null
  last_scheduled_sync_at?: string | null
  last_scheduled_status?: string | null
  last_error?: string | null
}

export interface Plan2032Holding {
  id?: number
  symbol: string
  name: string
  shares: number
  target2032: number
  dividend_yield: number
  category: string
  currency: 'USD' | 'HKD' | 'CNY'
  pe?: number | null
  moat?: string
  risk?: number
  note?: string
  sort_order?: number
}
