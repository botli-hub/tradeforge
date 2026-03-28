import { useEffect, useRef, useState } from 'react'
import { createChart, IChartApi, ISeriesApi, CandlestickData } from 'lightweight-charts'
import AccountMetricsGrid from '../components/AccountMetricsGrid'
import MarketStatusBar from '../components/MarketStatusBar'
import OrdersTable from '../components/OrdersTable'
import PositionsTable from '../components/PositionsTable'
import SearchResultsList from '../components/SearchResultsList'
import SignalConfirmModal from '../components/SignalConfirmModal'
import StockSelect from '../components/StockSelect'
import {
  AppSettings,
  KlineBar,
  QuoteData,
  SearchStockResult,
  StrategySignal,
  StrategySummary,
  TradingAccount,
  TradingOrder,
  TradingPosition,
  evaluateStrategySignal,
  getAccount,
  getAppSettings,
  getKlines,
  getOrders,
  getPositions,
  getQuote,
  getStrategies,
  getTradingStatus,
  placeOrder,
  saveAppSettings,
  searchStocks,
  subscribeSettings,
} from '../services/api'

export default function MarketPage() {
  const initialSettings = getAppSettings()
  const [settings, setSettings] = useState<AppSettings>(initialSettings)
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')
  const [klines, setKlines] = useState<KlineBar[]>([])
  const [quote, setQuote] = useState<QuoteData | null>(null)
  const [orders, setOrders] = useState<TradingOrder[]>([])
  const [positions, setPositions] = useState<TradingPosition[]>([])
  const [account, setAccount] = useState<TradingAccount | null>(null)
  const [strategies, setStrategies] = useState<StrategySummary[]>([])
  const [selectedStrategyId, setSelectedStrategyId] = useState('')
  const [signalInfo, setSignalInfo] = useState<StrategySignal | null>(null)
  const [notice, setNotice] = useState('')
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<SearchStockResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [tradingConnected, setTradingConnected] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [signalSide, setSignalSide] = useState<'BUY' | 'SELL'>('BUY')
  const [orderType, setOrderType] = useState<'LIMIT' | 'MARKET'>('LIMIT')
  const [orderQuantity, setOrderQuantity] = useState(initialSettings.defaultOrderQuantity || 100)
  const [orderPrice, setOrderPrice] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [signalText, setSignalText] = useState('')
  const [lastSignalAt, setLastSignalAt] = useState('')
  const [lastRefreshAt, setLastRefreshAt] = useState('')
  const [chartWarning, setChartWarning] = useState('')

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const lastSignalKeyRef = useRef('')

  const inferMarket = (value: string) => {
    const text = value.trim().toUpperCase()
    if (text.endsWith('.SH') || text.endsWith('.SZ') || (/^\d{6}$/.test(text))) return 'CN'
    if (text.endsWith('.HK') || (/^\d{1,5}$/.test(text))) return 'HK'
    return 'US'
  }

  const refreshOptions = [
    { label: '手动', value: 0 },
    { label: '10s', value: 10 },
    { label: '30s', value: 30 },
    { label: '1分钟', value: 60 },
    { label: '5分钟', value: 300 },
  ]

  const pollMs = Math.max(0, Number(settings.refreshIntervalSec || 0) * 1000)

  const toChartTime = (value?: string) => {
    if (!value) return null
    const ms = new Date(value).getTime()
    if (Number.isNaN(ms)) return null
    return Math.floor(ms / 1000) as any
  }

  useEffect(() => {
    const unsubscribe = subscribeSettings(next => {
      setSettings(next)
      setOrderQuantity(next.defaultOrderQuantity)
    })
    return unsubscribe
  }, [])

  useEffect(() => {
    void loadStrategiesList()
    void refreshTradingPanels()
  }, [])

  useEffect(() => {
    lastSignalKeyRef.current = ''
    setSignalInfo(null)
    setSignalText('')
  }, [symbol, timeframe, selectedStrategyId])

  useEffect(() => {
    void fetchMarketData(false)
  }, [symbol, timeframe, settings.marketDataSource, settings.marketHost, settings.marketPort])

  useEffect(() => {
    if (selectedStrategyId) {
      void fetchStrategySignal(false)
    }
  }, [selectedStrategyId])

  useEffect(() => {
    if (!pollMs) {
      return
    }

    const timer = window.setInterval(() => {
      void fetchMarketData(true)
      void refreshTradingPanels(true)
    }, pollMs)
    return () => window.clearInterval(timer)
  }, [pollMs, symbol, timeframe, settings.marketDataSource, settings.marketHost, settings.marketPort, selectedStrategyId])

  useEffect(() => {
    if (!chartContainerRef.current) return

    try {
      chartRef.current = createChart(chartContainerRef.current, {
        layout: {
          background: { color: '#000000' },
          textColor: '#666666',
        },
        grid: {
          vertLines: { color: '#111111' },
          horzLines: { color: '#111111' },
        },
        width: chartContainerRef.current.clientWidth,
        height: 360,
      })

      candleSeriesRef.current = chartRef.current.addCandlestickSeries({
        upColor: '#00C805',
        downColor: '#FF5000',
        borderUpColor: '#00C805',
        borderDownColor: '#FF5000',
        wickUpColor: '#00C805',
        wickDownColor: '#FF5000',
      })
      setChartWarning('')
    } catch (e: any) {
      setChartWarning(e?.message || '图表初始化失败，已降级为数据模式')
      chartRef.current = null
      candleSeriesRef.current = null
    }

    return () => {
      try {
        chartRef.current?.remove()
      } catch {
        // noop
      }
    }
  }, [])

  useEffect(() => {
    if (!candleSeriesRef.current || klines.length === 0) return

    try {
      const deduped = new Map<any, CandlestickData>()
      klines.forEach(k => {
        const ts = toChartTime(k.timestamp)
        if (ts === null) return
        deduped.set(ts, {
          time: ts,
          open: Number(k.open),
          high: Number(k.high),
          low: Number(k.low),
          close: Number(k.close),
        })
      })

      const data = Array.from(deduped.entries())
        .sort((a, b) => Number(a[0]) - Number(b[0]))
        .map(([, item]) => item)

      if (data.length === 0) {
        setChartWarning('当前没有可渲染的图表数据，已保留文字数据展示')
        return
      }

      candleSeriesRef.current.setData(data)
      setChartWarning('')
    } catch (e: any) {
      setChartWarning(e?.message || '图表渲染失败，已降级为文字数据展示')
    }
  }, [klines])

  async function loadStrategiesList() {
    try {
      const list = await getStrategies()
      setStrategies(list || [])
      if (!selectedStrategyId && list.length > 0) {
        setSelectedStrategyId(list[0].id)
      }
    } catch (e: any) {
      setError(e.message || '策略列表加载失败')
    }
  }


  async function refreshTradingPanels(silent = false) {
    try {
      const status = await getTradingStatus()
      setTradingConnected(status.connected)

      if (!status.connected) {
        setOrders([])
        setPositions([])
        setAccount(null)
        return
      }

      const [ordersData, positionsData, accountData] = await Promise.all([
        getOrders(),
        getPositions(),
        getAccount(),
      ])
      setOrders((ordersData || []).slice().reverse())
      setPositions(positionsData || [])
      setAccount(accountData || null)
    } catch (e: any) {
      setTradingConnected(false)
      if (!silent) {
        setError(e.message || '交易状态获取失败')
      }
    }
  }

  async function fetchStrategySignal(silent = false) {
    if (!selectedStrategyId) {
      setSignalInfo(null)
      return
    }

    try {
      const signal = await evaluateStrategySignal(selectedStrategyId, symbol, settings)
      setSignalInfo(signal)

      if (signal.signal && signal.signal !== 'NONE') {
        setSignalText(`${signal.strategy_name}：${signal.signal === 'BUY' ? '买入' : '卖出'}信号 | ${signal.reason}`)
        setLastSignalAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      } else if (!silent) {
        setSignalText(`${signal.strategy_name}：当前无信号`)
      }

      if (
        tradingConnected &&
        settings.confirmSignals &&
        signal.signal !== 'NONE' &&
        signal.signal_key !== lastSignalKeyRef.current
      ) {
        lastSignalKeyRef.current = signal.signal_key || ''
        setSignalSide(signal.signal as 'BUY' | 'SELL')
        setOrderType('LIMIT')
        setOrderQuantity(settings.defaultOrderQuantity)
        setOrderPrice(Number(signal.latest_bar?.close || quote?.price || 0))
        setShowModal(true)
      }
    } catch (e: any) {
      if (!silent) {
        setError(e.message || '策略信号评估失败')
      }
    }
  }

  async function fetchMarketData(silent = false) {
    if (!silent) {
      setLoading(true)
      setError('')
    }

    try {
      const [klineRes, quoteRes] = await Promise.allSettled([
        getKlines(symbol, timeframe, 365, settings),
        getQuote(symbol, settings),
      ])

      let latestPrice = 0
      const errorMessages: string[] = []
      let latestKlines = klines

      if (klineRes.status === 'fulfilled') {
        latestKlines = Array.isArray(klineRes.value) ? klineRes.value : []
        setKlines(latestKlines)
        latestPrice = Number(latestKlines[latestKlines.length - 1]?.close || 0)
      } else {
        errorMessages.push(`K线失败: ${klineRes.reason?.message || 'unknown error'}`)
      }

      if (quoteRes.status === 'fulfilled') {
        setQuote(quoteRes.value)
        latestPrice = Number(quoteRes.value?.price || latestPrice)
      } else {
        errorMessages.push(`实时行情失败: ${quoteRes.reason?.message || 'unknown error'}`)

        const fallbackBar = latestKlines[latestKlines.length - 1]
        if (fallbackBar) {
          setQuote(prev => ({
            symbol,
            name: prev?.name || symbol,
            price: Number(fallbackBar.close || prev?.price || 0),
            change: prev?.change || 0,
            change_pct: prev?.change_pct || 0,
            volume: Number(fallbackBar.volume || prev?.volume || 0),
            amount: prev?.amount || 0,
            bid: Number(fallbackBar.close || prev?.bid || 0),
            ask: Number(fallbackBar.close || prev?.ask || 0),
            high: Number(fallbackBar.high || prev?.high || 0),
            low: Number(fallbackBar.low || prev?.low || 0),
            open: Number(fallbackBar.open || prev?.open || 0),
            pre_close: Number(prev?.pre_close || fallbackBar.close || 0),
            adapter: fallbackBar.adapter || fallbackBar.source || prev?.adapter || 'local',
            storage: 'local-fallback',
          }))
          if (!silent) {
            setNotice('实时行情获取失败，已自动降级到本地最近K线数据展示')
          }
        }
      }

      if (latestPrice > 0) {
        setOrderPrice(latestPrice)
      }

      if (klineRes.status === 'rejected' && quoteRes.status === 'rejected') {
        if (!silent) {
          setError(`${errorMessages.join(' | ')}，已保留当前页面已有数据`)
        }
      } else if (errorMessages.length > 0 && !silent) {
        setError(errorMessages.join(' | '))
      }

      setLastRefreshAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      await fetchStrategySignal(true)
    } catch (e: any) {
      if (!silent) {
        setError(`${e.message || '行情加载失败'}，已保留当前页面已有数据`)
      }
    } finally {
      if (!silent) {
        setLoading(false)
      }
    }
  }

  async function handleSearch() {
    if (!searchQ) return
    try {
      const results = await searchStocks(searchQ, settings)
      setSearchResults(results)
    } catch (e: any) {
      setError(e.message || '搜索失败')
    }
  }

  function handleRefreshIntervalChange(value: number) {
    const next = saveAppSettings({ refreshIntervalSec: value })
    setSettings(next)
  }

  function selectStock(nextSymbol: string) {
    setSymbol(nextSymbol)
    setSearchResults([])
    setSearchQ('')
  }

  function openSignalModal(side: 'BUY' | 'SELL') {
    if (!tradingConnected) {
      setError('交易账户未连接，请先到设置页连接 Futu 交易通道')
      return
    }

    setSignalSide(side)
    setOrderType('LIMIT')
    setOrderQuantity(settings.defaultOrderQuantity)
    setOrderPrice(Number(quote?.price || klines[klines.length - 1]?.close || 0))
    setSignalText(`手动触发${side === 'BUY' ? '买入' : '卖出'}信号`)
    setLastSignalAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))

    if (settings.confirmSignals) {
      setShowModal(true)
    } else {
      void handleConfirmOrder(side)
    }
  }

  async function handleConfirmOrder(side = signalSide) {
    setSubmitting(true)
    setError('')
    try {
      await placeOrder({
        symbol,
        side,
        quantity: orderQuantity,
        price: orderType === 'MARKET' ? 0 : orderPrice,
        order_type: orderType,
      })
      setShowModal(false)
      setSignalText(`${side === 'BUY' ? '买入' : '卖出'}委托已提交`)
      await refreshTradingPanels(true)
    } catch (e: any) {
      setError(e.message || '下单失败')
    } finally {
      setSubmitting(false)
    }
  }

  const lastPrice = quote?.price ?? klines[klines.length - 1]?.close
  const adapterLabel = 'Futu'
  const envLabel = settings.tradingEnv === 'REAL' ? '实盘' : '模拟盘'

  const formatSourceLabel = (source?: string) => {
    if (!source) return '--'
    if (source === 'futu') return 'Futu'
    if (source === 'finnhub') return 'Finnhub'
    if (source === 'yahoo') return 'Yahoo'
    return source
  }

  const expectedQuoteSource = inferMarket(symbol) === 'US' ? 'finnhub' : 'futu'
  const expectedKlineSource = inferMarket(symbol) === 'US' ? 'yahoo' : 'futu'
  const quoteSource = formatSourceLabel(quote?.adapter || expectedQuoteSource)
  const klineSource = formatSourceLabel(klines[klines.length - 1]?.adapter || expectedKlineSource)
  return (
    <div className="page active">
      <h2>行情</h2>

      <MarketStatusBar
        settings={settings}
        refreshOptions={refreshOptions}
        lastRefreshAt={lastRefreshAt}
        tradingConnected={tradingConnected}
        quoteSource={quoteSource}
        klineSource={klineSource}
        strategies={strategies}
        selectedStrategyId={selectedStrategyId}
        signalInfo={signalInfo}
        onChangeRefreshInterval={handleRefreshIntervalChange}
        onChangeStrategy={setSelectedStrategyId}
      />

      {notice && (
        <div className="card" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
          {notice}
        </div>
      )}

      <div className="search-bar">
        <StockSelect
          value={symbol}
          onChange={nextSymbol => selectStock(nextSymbol)}
          style={{ flex: 1 }}
        />
        <input
          type="text"
          placeholder="搜索其他代码..."
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          style={{ flex: 1 }}
        />
        <button className="btn" onClick={handleSearch}>搜索</button>
      </div>

      {error && (
        <div className="card strategy-notice error">
          {error}
        </div>
      )}

      {signalText && (
        <div className="card" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <span>{signalText}</span>
            {lastSignalAt && <span style={{ color: 'var(--text-secondary)' }}>触发时间：{lastSignalAt}</span>}
          </div>
        </div>
      )}

      <SearchResultsList results={searchResults} onSelect={selectStock} />

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12, alignItems: 'center' }}>
          <div>
            <span style={{ fontSize: 24, fontWeight: 'bold' }}>{symbol}</span>
            <span style={{ marginLeft: 10, color: 'var(--text-secondary)' }}>{quote?.name || symbol}</span>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 24 }}>${lastPrice?.toFixed?.(2) ?? '--'}</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{loading ? '加载中...' : `周期 ${timeframe}`}</div>
          </div>
        </div>

        <div ref={chartContainerRef} className="chart-area" />

        {chartWarning && (
          <div className="chart-warning">
            图表提示：{chartWarning}
          </div>
        )}

        <div className="data-source-strip">
          <span>当前报价：<strong>{quoteSource}</strong></span>
          <span>当前K线：<strong>{klineSource}</strong></span>
        </div>

        <div className="chart-controls">
          {['1m', '5m', '15m', '1h', '1d'].map(t => (
            <button
              key={t}
              className={`btn-outline ${timeframe === t ? 'active' : ''}`}
              onClick={() => setTimeframe(t)}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="indicator-tags">
          <span className="indicator-tag">真实策略信号</span>
          <span className="indicator-tag">自动评估</span>
          <span className="indicator-tag">确认弹窗</span>
        </div>

        <div style={{ display: 'flex', gap: 12, marginTop: 16, flexWrap: 'wrap' }}>
          <button className="btn" onClick={() => openSignalModal('BUY')}>手动买入</button>
          <button className="btn-outline" onClick={() => openSignalModal('SELL')}>手动卖出</button>
          <button className="btn-outline" onClick={() => fetchMarketData(false)}>刷新行情</button>
          <button className="btn-outline" onClick={() => fetchStrategySignal(false)}>评估策略信号</button>
          <button className="btn-outline" onClick={() => refreshTradingPanels(false)}>刷新订单/持仓</button>
        </div>
      </div>

      <AccountMetricsGrid account={account} />
      <PositionsTable positions={positions} />
      <OrdersTable orders={orders} />

      <SignalConfirmModal
        open={showModal}
        symbol={symbol}
        side={signalSide}
        price={orderPrice}
        quantity={orderQuantity}
        orderType={orderType}
        adapterLabel={adapterLabel}
        envLabel={envLabel}
        onChangePrice={setOrderPrice}
        onChangeQuantity={setOrderQuantity}
        onChangeOrderType={setOrderType}
        onCancel={() => setShowModal(false)}
        onConfirm={() => handleConfirmOrder()}
        submitting={submitting}
      />
    </div>
  )
}
