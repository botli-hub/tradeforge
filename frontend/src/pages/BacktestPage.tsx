import { useEffect, useMemo, useState } from 'react'
import { getBacktestResult, getStrategies, runBacktest } from '../services/api'

interface Strategy {
  id: string
  name: string
  timeframe?: string
  symbols?: string[]
  config?: {
    symbols?: string[]
    timeframe?: string
  }
}

interface BacktestMetrics {
  total_return: number
  annual_return: number
  sharpe_ratio: number
  max_drawdown: number
  win_rate: number
  profit_factor: number
  total_trades: number
  avg_holding_days: number
}

interface BacktestTrade {
  symbol?: string
  entry_time?: string
  entry_price?: number
  exit_time?: string
  exit_price?: number
  quantity?: number
  pnl?: number
}

interface EquityPoint {
  timestamp: string
  equity: number
}

interface DataSourceItem {
  symbol: string
  requested_source: string
  data_source: string
  load_mode: string
  bar_count: number
  warning?: string
}

interface BacktestResult {
  id: string
  status: string
  strategy_name?: string
  symbols: string[]
  timeframe: string
  start_date: string
  end_date: string
  completed_at?: string
  metrics: BacktestMetrics
  trades: BacktestTrade[]
  equity_curve: EquityPoint[]
  data_sources: DataSourceItem[]
  warnings?: string[]
}

type ViewState = 'empty' | 'running' | 'error' | 'success'

const DEFAULT_START_DATE = '2024-01-01'
const DEFAULT_END_DATE = '2025-01-01'

function formatPercent(value?: number, digits: number = 2) {
  return `${((value || 0) * 100).toFixed(digits)}%`
}

function formatNumber(value?: number, digits: number = 2) {
  return Number(value || 0).toFixed(digits)
}

function formatDate(value?: string) {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 19)
}

function buildLinePath(points: EquityPoint[], width: number, height: number) {
  if (!points.length) return ''
  const values = points.map(point => point.equity)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  return points
    .map((point, index) => {
      const x = points.length === 1 ? width / 2 : (index / (points.length - 1)) * width
      const y = height - ((point.equity - min) / range) * height
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selectedStrategy, setSelectedStrategy] = useState('')
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')
  const [startDate, setStartDate] = useState(DEFAULT_START_DATE)
  const [endDate, setEndDate] = useState(DEFAULT_END_DATE)
  const [initialCapital, setInitialCapital] = useState(100000)
  const [feeRate, setFeeRate] = useState(0.0003)
  const [slippage, setSlippage] = useState(0.001)

  const [viewState, setViewState] = useState<ViewState>('empty')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [result, setResult] = useState<BacktestResult | null>(null)

  const selectedStrategyDetail = useMemo(
    () => strategies.find(strategy => strategy.id === selectedStrategy) || null,
    [strategies, selectedStrategy]
  )

  const defaultStrategySymbol =
    selectedStrategyDetail?.symbols?.[0] || selectedStrategyDetail?.config?.symbols?.[0] || ''

  useEffect(() => {
    loadStrategies()
  }, [])

  async function loadStrategies() {
    try {
      const data = await getStrategies()
      setStrategies(data)
      if (data.length > 0) {
        const first = data[0]
        setSelectedStrategy(first.id)
        setTimeframe(first.timeframe || first.config?.timeframe || '1d')
        const strategySymbol = first.symbols?.[0] || first.config?.symbols?.[0]
        if (strategySymbol) setSymbol(strategySymbol)
      }
    } catch (e) {
      console.error('Failed to load strategies:', e)
      setError(e instanceof Error ? e.message : '加载策略失败')
      setViewState('error')
    }
  }

  function applyStrategyDefaults() {
    if (defaultStrategySymbol) setSymbol(defaultStrategySymbol)
    const tf = selectedStrategyDetail?.timeframe || selectedStrategyDetail?.config?.timeframe
    if (tf) setTimeframe(tf)
  }

  async function handleRun() {
    const normalizedSymbol = symbol.trim().toUpperCase()

    if (!selectedStrategy) {
      setError('请选择策略')
      setViewState('error')
      return
    }

    if (!normalizedSymbol) {
      setError('请输入回测标的，例如 AAPL / 00700.HK / 600519.SH')
      setViewState('error')
      return
    }

    if (!startDate || !endDate) {
      setError('请填写完整的回测日期区间')
      setViewState('error')
      return
    }

    if (startDate >= endDate) {
      setError('开始日期必须早于结束日期')
      setViewState('error')
      return
    }

    setViewState('running')
    setError('')
    setResult(null)
    setProgress(8)

    const interval = window.setInterval(() => {
      setProgress(current => Math.min(current + 12, 88))
    }, 220)

    try {
      const runResponse = await runBacktest({
        strategy_id: selectedStrategy,
        symbols: [normalizedSymbol],
        timeframe,
        start_date: startDate,
        end_date: endDate,
        initial_capital: initialCapital,
        fee_rate: feeRate,
        slippage,
      })

      setProgress(95)

      const resultData = runResponse?.id ? await getBacktestResult(runResponse.id) : runResponse
      setResult(resultData)
      setProgress(100)
      setViewState('success')
    } catch (e) {
      console.error('Backtest failed:', e)
      setError(e instanceof Error ? e.message : '回测失败，请检查后端日志')
      setViewState('error')
    } finally {
      window.clearInterval(interval)
      window.setTimeout(() => setProgress(0), 500)
    }
  }

  const equityPath = useMemo(() => buildLinePath(result?.equity_curve || [], 760, 220), [result])
  const latestEquity = result?.equity_curve?.[result.equity_curve.length - 1]?.equity || initialCapital

  return (
    <div className="page">
      <h2>回测</h2>

      <div className="card">
        <div className="strategy-form-grid" style={{ marginBottom: 16 }}>
          <div className="strategy-field">
            <span>策略</span>
            <select
              value={selectedStrategy}
              onChange={e => {
                const strategyId = e.target.value
                setSelectedStrategy(strategyId)
                const next = strategies.find(item => item.id === strategyId)
                if (next?.timeframe || next?.config?.timeframe) {
                  setTimeframe(next.timeframe || next.config?.timeframe || '1d')
                }
              }}
            >
              {strategies.map(strategy => (
                <option key={strategy.id} value={strategy.id}>
                  {strategy.name}
                </option>
              ))}
            </select>
          </div>

          <div className="strategy-field">
            <span>标的</span>
            <input
              type="text"
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              placeholder="AAPL / 00700.HK / 600519.SH"
            />
          </div>

          <div className="strategy-field">
            <span>Timeframe</span>
            <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
              <option value="1d">1d</option>
              <option value="1h">1h</option>
              <option value="30m">30m</option>
              <option value="5m">5m</option>
              <option value="1m">1m</option>
            </select>
          </div>

          <div className="strategy-field">
            <span>开始日期</span>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
          </div>

          <div className="strategy-field">
            <span>结束日期</span>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>

          <div className="strategy-field">
            <span>初始资金</span>
            <input type="number" value={initialCapital} onChange={e => setInitialCapital(Number(e.target.value))} />
          </div>

          <div className="strategy-field">
            <span>手续费</span>
            <input type="number" step="0.0001" value={feeRate} onChange={e => setFeeRate(Number(e.target.value))} />
          </div>

          <div className="strategy-field">
            <span>滑点</span>
            <input type="number" step="0.0001" value={slippage} onChange={e => setSlippage(Number(e.target.value))} />
          </div>

          <div className="strategy-field">
            <span>快捷操作</span>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button className="btn-outline" type="button" onClick={applyStrategyDefaults}>
                使用当前策略默认标的
              </button>
              <button className="btn" type="button" onClick={handleRun} disabled={viewState === 'running'}>
                {viewState === 'running' ? '回测运行中...' : '开始回测'}
              </button>
            </div>
          </div>
        </div>

        <div className="status-row">
          <div className="status-line">
            <span>状态：{viewState}</span>
            <span>当前策略默认标的：{defaultStrategySymbol || '--'}</span>
          </div>
        </div>

        {viewState === 'running' && (
          <div className="progress-bar" style={{ marginTop: 16 }}>
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
        )}
      </div>

      {viewState === 'empty' && !result && (
        <div className="card empty-state">
          <h3>还没有回测结果</h3>
          <p>选择策略、输入标的和日期区间后，点击“开始回测”。</p>
        </div>
      )}

      {viewState === 'error' && (
        <div className="card strategy-notice error">
          <strong>回测失败：</strong>
          <div style={{ marginTop: 8 }}>{error || '未知错误'}</div>
        </div>
      )}

      {viewState === 'success' && result && (
        <>
          <div className="card">
            <div className="status-row" style={{ marginBottom: 16 }}>
              <div className="status-line">
                <span>策略：{result.strategy_name || selectedStrategyDetail?.name || '--'}</span>
                <span>回测区间：{result.start_date} ~ {result.end_date}</span>
                <span>完成时间：{formatDate(result.completed_at)}</span>
                <span>状态：{result.status}</span>
              </div>
            </div>

            <div className="metrics-grid">
              <div className="metric-card">
                <div className="value positive">{formatPercent(result.metrics.total_return)}</div>
                <div className="label">总收益率</div>
              </div>
              <div className="metric-card">
                <div className="value positive">{formatPercent(result.metrics.annual_return)}</div>
                <div className="label">年化收益</div>
              </div>
              <div className="metric-card">
                <div className="value negative">{formatPercent(result.metrics.max_drawdown)}</div>
                <div className="label">最大回撤</div>
              </div>
              <div className="metric-card">
                <div className="value">{result.metrics.total_trades || 0}</div>
                <div className="label">交易次数</div>
              </div>
              <div className="metric-card">
                <div className="value">{formatPercent(result.metrics.win_rate)}</div>
                <div className="label">胜率</div>
              </div>
              <div className="metric-card">
                <div className="value">{formatNumber(result.metrics.sharpe_ratio)}</div>
                <div className="label">夏普比率</div>
              </div>
              <div className="metric-card">
                <div className="value">{formatNumber(result.metrics.profit_factor)}</div>
                <div className="label">盈亏比</div>
              </div>
              <div className="metric-card">
                <div className="value">{formatNumber(result.metrics.avg_holding_days, 1)} 天</div>
                <div className="label">平均持仓</div>
              </div>
            </div>
          </div>

          <div className="card">
            <h4 style={{ marginBottom: 12 }}>资金曲线</h4>
            {result.equity_curve?.length ? (
              <>
                <div style={{ marginBottom: 10, color: '#9fb2d0' }}>
                  最新权益：<strong style={{ color: '#fff' }}>${formatNumber(latestEquity)}</strong>
                </div>
                <div style={{ background: '#0f3460', borderRadius: 12, padding: 16 }}>
                  <svg viewBox="0 0 760 220" style={{ width: '100%', height: 260 }}>
                    <defs>
                      <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#4cc9f0" stopOpacity="0.35" />
                        <stop offset="100%" stopColor="#4cc9f0" stopOpacity="0.03" />
                      </linearGradient>
                    </defs>
                    <rect x="0" y="0" width="760" height="220" fill="#0f3460" rx="12" />
                    <path d={equityPath} fill="none" stroke="#4cc9f0" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
                  </svg>
                </div>
              </>
            ) : (
              <div className="empty-state" style={{ padding: 24 }}>
                <h3>暂无资金曲线</h3>
                <p>当前回测未返回权益轨迹。</p>
              </div>
            )}
          </div>

          <div className="card">
            <h4 style={{ marginBottom: 12 }}>数据来源</h4>
            <div className="data-source-strip" style={{ marginTop: 0 }}>
              {result.data_sources?.map(item => (
                <div key={`${item.symbol}-${item.data_source}`}>
                  <strong>{item.symbol}</strong> · 实际来源 {item.data_source} · 路由 {item.requested_source} · 模式 {item.load_mode} · {item.bar_count} bars
                </div>
              ))}
            </div>
            {result.data_sources?.some(item => item.warning) && (
              <div className="chart-warning">
                {result.data_sources
                  .filter(item => item.warning)
                  .map(item => `${item.symbol}: ${item.warning}`)
                  .join('；')}
              </div>
            )}
          </div>

          <div className="card">
            <h4 style={{ marginBottom: 12 }}>交易明细</h4>
            {result.trades?.length ? (
              <div style={{ overflowX: 'auto' }}>
                <table className="trade-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>标的</th>
                      <th>入场时间</th>
                      <th>入场价</th>
                      <th>出场时间</th>
                      <th>出场价</th>
                      <th>数量</th>
                      <th>盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((trade, index) => (
                      <tr key={`${trade.symbol || 'symbol'}-${trade.entry_time || index}-${index}`}>
                        <td>{index + 1}</td>
                        <td>{trade.symbol || result.symbols?.[0] || '--'}</td>
                        <td>{formatDate(trade.entry_time)}</td>
                        <td>${formatNumber(trade.entry_price)}</td>
                        <td>{formatDate(trade.exit_time)}</td>
                        <td>${formatNumber(trade.exit_price)}</td>
                        <td>{formatNumber(trade.quantity)}</td>
                        <td className={(trade.pnl || 0) >= 0 ? 'positive' : 'negative'}>
                          {(trade.pnl || 0) >= 0 ? '+' : ''}${formatNumber(trade.pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state" style={{ padding: 24 }}>
                <h3>暂无交易明细</h3>
                <p>当前回测没有生成交易，可能是策略没有触发信号。</p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
