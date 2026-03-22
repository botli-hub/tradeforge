import { useEffect, useRef, useState } from 'react'
import { createChart, IChartApi, ISeriesApi, CandlestickData } from 'lightweight-charts'
import SignalConfirmModal from '../components/SignalConfirmModal'
import {
  AppSettings,
  addHistorySubscription,
  evaluateStrategySignal,
  getAccount,
  getAppSettings,
  getHistorySubscriptions,
  getKlines,
  getOrders,
  getPositions,
  getQuote,
  getStrategies,
  getTradingStatus,
  placeOrder,
  previewHistorySource,
  saveAppSettings,
  searchStocks,
  setHistorySubscriptionEnabled,
  subscribeSettings,
} from '../services/api'

export default function MarketPage() {
  const initialSettings = getAppSettings()
  const [settings, setSettings] = useState<AppSettings>(initialSettings)
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')
  const [klines, setKlines] = useState<any[]>([])
  const [quote, setQuote] = useState<any | null>(null)
  const [orders, setOrders] = useState<any[]>([])
  const [positions, setPositions] = useState<any[]>([])
  const [account, setAccount] = useState<any | null>(null)
  const [strategies, setStrategies] = useState<any[]>([])
  const [selectedStrategyId, setSelectedStrategyId] = useState('')
  const [signalInfo, setSignalInfo] = useState<any | null>(null)
  const [watchlist, setWatchlist] = useState<any[]>([])
  const [notice, setNotice] = useState('')
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
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
    void refreshWatchlist()
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
      if (!selectedStrategyId && list?.length > 0) {
        setSelectedStrategyId(list[0].id)
      }
    } catch (e: any) {
      setError(e.message || '策略列表加载失败')
    }
  }

  async function refreshWatchlist() {
    try {
      const list = await getHistorySubscriptions(true)
      setWatchlist(list || [])
    } catch {
      // 观察池获取失败时不打断行情页
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
        lastSignalKeyRef.current = signal.signal_key
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
          setQuote((prev: any) => ({
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

  async function handleSubscribeCurrent() {
    try {
      const preview = await previewHistorySource(symbol, settings.marketDataSource)
      await addHistorySubscription({
        symbol,
        name: quote?.name || symbol,
        source_hint: preview.source,
        enabled: true,
      })
      setNotice(`已加入观察池：${symbol}（每天 08:00 更新 1d / 1h / 30m / 5m / 1m）`)
      await refreshWatchlist()
    } catch (e: any) {
      setError(e.message || '加入观察池失败')
    }
  }

  async function handleToggleWatchlist(targetSymbol: string, enabled: boolean) {
    try {
      await setHistorySubscriptionEnabled(targetSymbol, enabled)
      setNotice(`${targetSymbol} 已${enabled ? '启用' : '停用'}观察池更新`)
      await refreshWatchlist()
    } catch (e: any) {
      setError(e.message || '更新观察池失败')
    }
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
  const routeModeLabel = '自动路由'

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
  const enabledWatchlist = watchlist.filter(item => item.enabled)
  const currentSubscription = watchlist.find(item => item.symbol === symbol)
  const isCurrentSubscribed = Boolean(currentSubscription?.enabled)

  return (
    <div className="page active">
      <h2>行情</h2>

      <div className="card compact-card">
        <div className="status-line">
          <span className="tag ready">路由模式：{routeModeLabel}</span>
          <span className="tag ready">报价实际来源：{quoteSource}</span>
          <span className="tag ready">K线实际来源：{klineSource}</span>
          <span className={`tag ${tradingConnected ? 'ready' : 'draft'}`}>
            交易：{tradingConnected ? `${adapterLabel} / ${envLabel}` : '未连接'}
          </span>
          <span className="tag ready refresh-tag">
            <span>实时刷新</span>
            <select
              className="tag-select"
              value={String(settings.refreshIntervalSec || 0)}
              onChange={e => handleRefreshIntervalChange(Number(e.target.value))}
            >
              {refreshOptions.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </span>
          {lastRefreshAt && <span className="tag draft">最近刷新：{lastRefreshAt}</span>}
        </div>
      </div>

      {notice && (
        <div className="card" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
          {notice}
        </div>
      )}

      <div className="card compact-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <div style={{ color: '#fff', fontWeight: 700 }}>观察池</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 4 }}>
              加入观察池后，会自动进入每天 08:00 的历史数据更新任务。
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {isCurrentSubscribed ? (
              <button className="btn-outline" onClick={() => handleToggleWatchlist(symbol, false)}>
                取消关注 {symbol}
              </button>
            ) : (
              <button className="btn" onClick={() => handleSubscribeCurrent()}>
                关注 {symbol}
              </button>
            )}
            <button className="btn-outline" onClick={() => refreshWatchlist()}>
              刷新观察池
            </button>
          </div>
        </div>

        <div className="watchlist-strip">
          {enabledWatchlist.length === 0 ? (
            <span className="watchlist-empty">暂无观察池标的</span>
          ) : (
            enabledWatchlist.map(item => (
              <div key={item.symbol} className={`watchlist-chip ${item.symbol === symbol ? 'active' : ''}`}>
                <button className="watchlist-chip-main" onClick={() => selectStock(item.symbol)}>
                  {item.symbol}
                </button>
                <button className="watchlist-chip-close" onClick={() => handleToggleWatchlist(item.symbol, false)}>
                  ×
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="card compact-card">
        <div className="settings-row" style={{ borderBottom: 'none', padding: 0 }}>
          <label>信号策略</label>
          <select value={selectedStrategyId} onChange={e => setSelectedStrategyId(e.target.value)} style={{ minWidth: 260 }}>
            <option value="">不启用自动信号</option>
            {strategies.map(strategy => (
              <option key={strategy.id} value={strategy.id}>
                {strategy.name} · {strategy.timeframe || '1d'}
              </option>
            ))}
          </select>
        </div>
        {signalInfo && (
          <div style={{ marginTop: 12, color: 'var(--text-secondary)', fontSize: 13 }}>
            当前策略：<strong>{signalInfo.strategy_name}</strong> | 最新信号：<strong>{signalInfo.signal}</strong>
          </div>
        )}
      </div>

      <div className="search-bar">
        <input
          type="text"
          placeholder="输入股票代码，如 AAPL、TSLA、00700"
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
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

      {searchResults.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          {searchResults.map(s => (
            <div
              key={s.symbol}
              onClick={() => selectStock(s.symbol)}
              style={{ padding: '8px 0', cursor: 'pointer', borderBottom: '1px solid #333' }}
            >
              <span style={{ color: 'var(--green)' }}>{s.symbol}</span>
              <span style={{ marginLeft: 12, color: 'var(--text-secondary)' }}>{s.name}</span>
            </div>
          ))}
        </div>
      )}

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

      <div className="metrics-grid">
        <div className="metric-card">
          <div className="value">{account?.cash?.toFixed?.(0) ?? '--'}</div>
          <div className="label">现金</div>
        </div>
        <div className="metric-card">
          <div className="value">{account?.buying_power?.toFixed?.(0) ?? '--'}</div>
          <div className="label">可用购买力</div>
        </div>
        <div className="metric-card">
          <div className="value">{account?.market_value?.toFixed?.(0) ?? '--'}</div>
          <div className="label">持仓市值</div>
        </div>
        <div className="metric-card">
          <div className="value">{account?.total_assets?.toFixed?.(0) ?? '--'}</div>
          <div className="label">总资产</div>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>当前持仓</h3>
        {positions.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)' }}>暂无持仓</div>
        ) : (
          <table className="trade-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>方向</th>
                <th>数量</th>
                <th>成本</th>
                <th>现价</th>
                <th>浮盈亏</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, idx) => (
                <tr key={`${p.symbol}-${idx}`}>
                  <td>{p.symbol}</td>
                  <td>{p.direction}</td>
                  <td>{p.quantity}</td>
                  <td>{p.avg_cost?.toFixed?.(2) ?? p.avg_cost}</td>
                  <td>{p.current_price?.toFixed?.(2) ?? p.current_price}</td>
                  <td className={p.unrealized_pnl >= 0 ? 'positive' : 'negative'}>
                    {p.unrealized_pnl?.toFixed?.(2) ?? p.unrealized_pnl}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>最近订单</h3>
        {orders.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)' }}>暂无订单</div>
        ) : (
          <table className="trade-table">
            <thead>
              <tr>
                <th>订单号</th>
                <th>代码</th>
                <th>方向</th>
                <th>价格</th>
                <th>数量</th>
                <th>状态</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {orders.slice(0, 8).map(o => (
                <tr key={o.order_id}>
                  <td>{o.order_id}</td>
                  <td>{o.symbol}</td>
                  <td>{o.side}</td>
                  <td>{o.price?.toFixed?.(2) ?? o.price}</td>
                  <td>{o.quantity}</td>
                  <td>{o.status}</td>
                  <td>{String(o.create_time).slice(11, 19)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

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
