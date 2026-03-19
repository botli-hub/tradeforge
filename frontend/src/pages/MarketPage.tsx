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
  searchStocks,
  setHistorySubscriptionEnabled,
  subscribeSettings,
} from '../services/api'

export default function MarketPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
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
  const [orderQuantity, setOrderQuantity] = useState(settings.defaultOrderQuantity)
  const [orderPrice, setOrderPrice] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [signalText, setSignalText] = useState('')
  const [lastSignalAt, setLastSignalAt] = useState('')
  const [lastRefreshAt, setLastRefreshAt] = useState('')

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const lastSignalKeyRef = useRef('')

  const pollMs = settings.marketDataSource === 'futu' ? 3000 : 10000

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
    const timer = window.setInterval(() => {
      void fetchMarketData(true)
      void refreshTradingPanels(true)
    }, pollMs)
    return () => window.clearInterval(timer)
  }, [pollMs, symbol, timeframe, settings.marketDataSource, settings.marketHost, settings.marketPort, selectedStrategyId])

  useEffect(() => {
    if (!chartContainerRef.current) return

    chartRef.current = createChart(chartContainerRef.current, {
      layout: {
        background: { color: '#0f3460' },
        textColor: '#888',
      },
      grid: {
        vertLines: { color: '#1a1a2e' },
        horzLines: { color: '#1a1a2e' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 360,
    })

    candleSeriesRef.current = chartRef.current.addCandlestickSeries({
      upColor: '#4cc9f0',
      downColor: '#e94560',
      borderUpColor: '#4cc9f0',
      borderDownColor: '#e94560',
      wickUpColor: '#4cc9f0',
      wickDownColor: '#e94560',
    })

    return () => {
      chartRef.current?.remove()
    }
  }, [])

  useEffect(() => {
    if (!candleSeriesRef.current || klines.length === 0) return

    const data: CandlestickData[] = klines.map(k => ({
      time: k.timestamp.split('T')[0] as any,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))

    candleSeriesRef.current.setData(data)
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
      let errorMessages: string[] = []

      if (klineRes.status === 'fulfilled') {
        setKlines(klineRes.value)
        latestPrice = Number(klineRes.value[klineRes.value.length - 1]?.close || 0)
      } else if (!silent) {
        errorMessages.push(`K线失败: ${klineRes.reason?.message || 'unknown error'}`)
      }

      if (quoteRes.status === 'fulfilled') {
        setQuote(quoteRes.value)
        latestPrice = Number(quoteRes.value?.price || latestPrice)
      } else if (!silent) {
        errorMessages.push(`报价失败: ${quoteRes.reason?.message || 'unknown error'}`)
      }

      if (latestPrice > 0) {
        setOrderPrice(latestPrice)
      }

      if (klineRes.status === 'rejected' && quoteRes.status === 'rejected' && !silent) {
        setKlines([])
        setQuote(null)
      }

      if (errorMessages.length > 0 && !silent) {
        setError(errorMessages.join(' | '))
      }

      setLastRefreshAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      await fetchStrategySignal(true)
    } catch (e: any) {
      setError(e.message || '行情加载失败')
      if (!silent) {
        setKlines([])
        setQuote(null)
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
      setError('交易账户未连接，请先去设置页连接 mock / futu 交易通道')
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
  const adapterLabel = settings.tradingAdapter === 'futu' ? 'Futu' : 'Mock'
  const envLabel = settings.tradingEnv === 'REAL' ? '实盘' : '模拟盘'
  const dataLabel = settings.marketDataSource === 'futu'
    ? `Futu ${settings.marketHost}:${settings.marketPort}`
    : settings.marketDataSource === 'finnhub'
      ? 'Finnhub'
      : 'Mock'

  const formatSourceLabel = (source?: string) => {
    if (!source) return '--'
    if (source === 'futu') return 'Futu'
    if (source === 'finnhub') return 'Finnhub'
    if (source === 'yahoo') return 'Yahoo'
    if (source === 'mock') return 'Mock'
    return source
  }

  const quoteSource = formatSourceLabel(quote?.adapter || settings.marketDataSource)
  const klineSource = formatSourceLabel(klines[klines.length - 1]?.adapter || settings.marketDataSource)
  const enabledWatchlist = watchlist.filter(item => item.enabled)
  const currentSubscription = watchlist.find(item => item.symbol === symbol)
  const isCurrentSubscribed = Boolean(currentSubscription?.enabled)

  return (
    <div className="page active">
      <h2>行情</h2>

      <div className="card compact-card">
        <div className="status-line">
          <span className={`tag ${settings.marketDataSource === 'futu' ? 'ready' : 'draft'}`}>
            主数据源：{dataLabel}
          </span>
          <span className="tag ready">报价源：{quoteSource}</span>
          <span className="tag ready">K线源：{klineSource}</span>
          <span className={`tag ${tradingConnected ? 'ready' : 'draft'}`}>
            交易：{tradingConnected ? `${adapterLabel} / ${envLabel}` : '未连接'}
          </span>
          <span className="tag ready">实时刷新：{pollMs / 1000}s</span>
          {lastRefreshAt && <span className="tag draft">最近刷新：{lastRefreshAt}</span>}
        </div>
      </div>

      {notice && (
        <div className="card" style={{ border: '1px solid rgba(76,201,240,0.35)', color: '#cde9f5' }}>
          {notice}
        </div>
      )}

      <div className="card compact-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <div style={{ color: '#fff', fontWeight: 700 }}>观察池</div>
            <div style={{ color: '#9fb2d0', fontSize: 12, marginTop: 4 }}>
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
          <div style={{ marginTop: 12, color: '#9eb6d6', fontSize: 13 }}>
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
        <div className="card" style={{ border: '1px solid rgba(233,69,96,0.35)', color: '#ffb0ba' }}>
          {error}
        </div>
      )}

      {signalText && (
        <div className="card" style={{ border: '1px solid rgba(76,201,240,0.35)', color: '#cde9f5' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <span>{signalText}</span>
            {lastSignalAt && <span style={{ color: '#8fa8c6' }}>触发时间：{lastSignalAt}</span>}
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
              <span style={{ color: '#4cc9f0' }}>{s.symbol}</span>
              <span style={{ marginLeft: 12, color: '#888' }}>{s.name}</span>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12, alignItems: 'center' }}>
          <div>
            <span style={{ fontSize: 24, fontWeight: 'bold' }}>{symbol}</span>
            <span style={{ marginLeft: 10, color: '#888' }}>{quote?.name || symbol}</span>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 24 }}>${lastPrice?.toFixed?.(2) ?? '--'}</div>
            <div style={{ color: '#888', fontSize: 12 }}>{loading ? '加载中...' : `周期 ${timeframe}`}</div>
          </div>
        </div>

        <div ref={chartContainerRef} className="chart-area" />

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
          <div style={{ color: '#888' }}>暂无持仓</div>
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
          <div style={{ color: '#888' }}>暂无订单</div>
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
