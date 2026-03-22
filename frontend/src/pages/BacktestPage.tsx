import { useEffect, useMemo, useRef, useState } from 'react'
import { getBacktestResult, getHistoryCoverage, getStrategies, runBacktest } from '../services/api'

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
  buy_and_hold_return?: number
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

interface CoverageInfo {
  earliest_ts: string | null
  latest_ts: string | null
  bar_count: number
}

type ViewState = 'empty' | 'running' | 'error' | 'success'

// --- equity curve helpers ---
const PLOT = { left: 65, right: 820, top: 10, bottom: 245, width: 755, height: 235 }

function formatEquityValue(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function buildLinePath(points: EquityPoint[]) {
  if (!points.length) return ''
  const values = points.map(p => p.equity)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  return points
    .map((p, i) => {
      const x = PLOT.left + (points.length === 1 ? PLOT.width / 2 : (i / (points.length - 1)) * PLOT.width)
      const y = PLOT.bottom - ((p.equity - min) / range) * PLOT.height
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

function buildAreaPath(points: EquityPoint[]) {
  if (!points.length) return ''
  const values = points.map(p => p.equity)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const pts = points.map((p, i) => {
    const x = PLOT.left + (points.length === 1 ? PLOT.width / 2 : (i / (points.length - 1)) * PLOT.width)
    const y = PLOT.bottom - ((p.equity - min) / range) * PLOT.height
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  const first = `${PLOT.left.toFixed(2)},${PLOT.bottom}`
  const last = `${PLOT.right.toFixed(2)},${PLOT.bottom}`
  return `M ${first} L ${pts.join(' L ')} L ${last} Z`
}

function pickAxisDates(points: EquityPoint[], count = 6): { label: string; x: number }[] {
  if (!points.length) return []
  const result: { label: string; x: number }[] = []
  const n = points.length
  const step = Math.max(1, Math.floor((n - 1) / (count - 1)))
  const indices = new Set<number>()
  for (let i = 0; i < count - 1; i++) indices.add(Math.min(i * step, n - 1))
  indices.add(n - 1)
  Array.from(indices).sort((a, b) => a - b).forEach(idx => {
    const x = PLOT.left + (n === 1 ? PLOT.width / 2 : (idx / (n - 1)) * PLOT.width)
    result.push({ label: points[idx].timestamp.slice(0, 10), x })
  })
  return result
}

function getYGridValues(points: EquityPoint[], count = 4): { value: number; y: number }[] {
  if (!points.length) return []
  const values = points.map(p => p.equity)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const result = []
  for (let i = 0; i < count; i++) {
    const value = min + (range / (count - 1)) * i
    const y = PLOT.bottom - ((value - min) / range) * PLOT.height
    result.push({ value, y })
  }
  return result
}

// --- format helpers ---
function formatPercent(value?: number, digits = 2) {
  const v = value || 0
  const sign = v >= 0 ? '+' : ''
  return `${sign}${(v * 100).toFixed(digits)}%`
}

function formatNumber(value?: number, digits = 2) {
  return Number(value || 0).toFixed(digits)
}

function formatDate(value?: string) {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 19)
}

// --- component ---
export default function BacktestPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selectedStrategy, setSelectedStrategy] = useState('')
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [initialCapital, setInitialCapital] = useState(100000)
  const [feeRate, setFeeRate] = useState(0.0003)
  const [slippage, setSlippage] = useState(0.001)

  const [coverage, setCoverage] = useState<CoverageInfo | null>(null)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [coverageError, setCoverageError] = useState('')

  const [viewState, setViewState] = useState<ViewState>('empty')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [result, setResult] = useState<BacktestResult | null>(null)

  const coverageTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const selectedStrategyDetail = useMemo(
    () => strategies.find(s => s.id === selectedStrategy) || null,
    [strategies, selectedStrategy]
  )
  const defaultStrategySymbol =
    selectedStrategyDetail?.symbols?.[0] || selectedStrategyDetail?.config?.symbols?.[0] || ''

  useEffect(() => { loadStrategies() }, [])

  // Auto-fetch coverage when symbol or timeframe changes
  useEffect(() => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return

    if (coverageTimerRef.current) clearTimeout(coverageTimerRef.current)
    setCoverageLoading(true)
    setCoverageError('')

    coverageTimerRef.current = setTimeout(() => {
      getHistoryCoverage(sym, timeframe)
        .then(data => {
          setCoverage(data)
          if (data.bar_count > 0 && data.earliest_ts && data.latest_ts) {
            setStartDate(data.earliest_ts.slice(0, 10))
            setEndDate(data.latest_ts.slice(0, 10))
            setCoverageError('')
          } else {
            setCoverageError('本地无该标的数据，请先到「历史数据」页面同步 K 线')
          }
        })
        .catch(() => setCoverageError('查询本地数据范围失败'))
        .finally(() => setCoverageLoading(false))
    }, 400)

    return () => { if (coverageTimerRef.current) clearTimeout(coverageTimerRef.current) }
  }, [symbol, timeframe])

  async function loadStrategies() {
    try {
      const data = await getStrategies()
      setStrategies(data)
      if (data.length > 0) {
        const first = data[0]
        setSelectedStrategy(first.id)
        setTimeframe(first.timeframe || first.config?.timeframe || '1d')
        const sym = first.symbols?.[0] || first.config?.symbols?.[0]
        if (sym) setSymbol(sym)
      }
    } catch (e) {
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
    const sym = symbol.trim().toUpperCase()
    if (!selectedStrategy) { setError('请选择策略'); setViewState('error'); return }
    if (!sym) { setError('请输入回测标的，例如 AAPL / 00700.HK / 600519.SH'); setViewState('error'); return }
    if (!startDate || !endDate) { setError('请填写完整的回测日期区间'); setViewState('error'); return }
    if (startDate >= endDate) { setError('开始日期必须早于结束日期'); setViewState('error'); return }

    setViewState('running')
    setError('')
    setResult(null)
    setProgress(8)

    const interval = window.setInterval(() => {
      setProgress(cur => Math.min(cur + 12, 88))
    }, 220)

    try {
      const runResponse = await runBacktest({
        strategy_id: selectedStrategy,
        symbols: [sym],
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
      setError(e instanceof Error ? e.message : '回测失败，请检查后端日志')
      setViewState('error')
    } finally {
      window.clearInterval(interval)
      window.setTimeout(() => setProgress(0), 500)
    }
  }

  const equityCurve = result?.equity_curve || []
  const equityPath = useMemo(() => buildLinePath(equityCurve), [equityCurve])
  const areaPath = useMemo(() => buildAreaPath(equityCurve), [equityCurve])
  const axisDateLabels = useMemo(() => pickAxisDates(equityCurve), [equityCurve])
  const yGridValues = useMemo(() => getYGridValues(equityCurve), [equityCurve])
  const latestEquity = equityCurve[equityCurve.length - 1]?.equity || initialCapital

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
                const id = e.target.value
                setSelectedStrategy(id)
                const next = strategies.find(s => s.id === id)
                if (next?.timeframe || next?.config?.timeframe) {
                  setTimeframe(next.timeframe || next.config?.timeframe || '1d')
                }
              }}
            >
              {strategies.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
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
            <span>操作</span>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button className="btn-outline" type="button" onClick={applyStrategyDefaults}>
                使用策略默认标的
              </button>
              <button className="btn" type="button" onClick={handleRun} disabled={viewState === 'running'}>
                {viewState === 'running' ? '运行中...' : '开始回测'}
              </button>
            </div>
          </div>
        </div>

        {/* Coverage info strip */}
        <div style={{ marginTop: 4, fontSize: 12 }}>
          {coverageLoading && <span style={{ color: 'var(--text-tertiary)' }}>查询本地数据范围...</span>}
          {!coverageLoading && coverage && coverage.bar_count > 0 && (
            <span style={{ color: 'var(--green)' }}>
              本地数据: {coverage.earliest_ts?.slice(0, 10)} ~ {coverage.latest_ts?.slice(0, 10)} （{coverage.bar_count} bars）
            </span>
          )}
          {!coverageLoading && coverageError && (
            <span style={{ color: 'var(--red)' }}>{coverageError}</span>
          )}
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
          <p>选择策略、确认标的和日期区间后，点击「开始回测」。</p>
        </div>
      )}

      {viewState === 'error' && (
        <div className="card strategy-notice error">
          <strong>回测失败</strong>
          <div style={{ marginTop: 8 }}>{error || '未知错误'}</div>
        </div>
      )}

      {viewState === 'success' && result && (
        <>
          {/* Metrics */}
          <div className="card">
            <div className="status-row" style={{ marginBottom: 16 }}>
              <div className="status-line">
                <span>策略：{result.strategy_name || selectedStrategyDetail?.name || '--'}</span>
                <span>{result.start_date} ~ {result.end_date}</span>
                <span>完成：{formatDate(result.completed_at)}</span>
              </div>
            </div>

            <div className="metrics-grid">
              <div className="metric-card">
                <div className={`value ${(result.metrics.total_return || 0) >= 0 ? 'positive' : 'negative'}`}>
                  {formatPercent(result.metrics.total_return)}
                </div>
                <div className="label">总收益率</div>
              </div>
              <div className="metric-card">
                <div className={`value ${(result.metrics.annual_return || 0) >= 0 ? 'positive' : 'negative'}`}>
                  {formatPercent(result.metrics.annual_return)}
                </div>
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
                <div className="value">{formatPercent(result.metrics.win_rate, 1)}</div>
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
              {result.metrics.buy_and_hold_return !== undefined && (
                <div className="metric-card" style={{ gridColumn: 'span 4', borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
                    <div>
                      <div className={`value ${result.metrics.buy_and_hold_return >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: 20 }}>
                        {formatPercent(result.metrics.buy_and_hold_return)}
                      </div>
                      <div className="label">买入持有基准</div>
                    </div>
                    <div>
                      <div className={`value ${(result.metrics.total_return - result.metrics.buy_and_hold_return) >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: 20 }}>
                        {formatPercent(result.metrics.total_return - result.metrics.buy_and_hold_return)}
                      </div>
                      <div className="label">策略超额收益</div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Equity curve with axes */}
          <div className="card">
            <h4 style={{ marginBottom: 12 }}>资金曲线</h4>
            {equityCurve.length ? (
              <>
                <div style={{ marginBottom: 10, color: 'var(--text-secondary)', fontSize: 13 }}>
                  最新权益：<strong style={{ color: 'var(--text-primary)' }}>${formatNumber(latestEquity)}</strong>
                </div>
                <svg viewBox="0 0 840 280" style={{ width: '100%', height: 280, display: 'block' }}>
                  <defs>
                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--green)" stopOpacity="0.18" />
                      <stop offset="100%" stopColor="var(--green)" stopOpacity="0.01" />
                    </linearGradient>
                  </defs>
                  {/* Background */}
                  <rect x="0" y="0" width="840" height="280" fill="var(--bg-primary)" rx="8" />
                  {/* Grid lines & Y labels */}
                  {yGridValues.map((gv, i) => (
                    <g key={i}>
                      <line x1={PLOT.left} y1={gv.y} x2={PLOT.right} y2={gv.y} stroke="var(--border)" strokeWidth="0.6" />
                      <text x={PLOT.left - 6} y={gv.y + 4} fill="var(--text-tertiary)" fontSize="9" textAnchor="end">
                        {formatEquityValue(gv.value)}
                      </text>
                    </g>
                  ))}
                  {/* Area fill */}
                  <path d={areaPath} fill="url(#eqGrad)" />
                  {/* Line */}
                  <path d={equityPath} fill="none" stroke="var(--green)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
                  {/* X-axis labels */}
                  {axisDateLabels.map((d, i) => (
                    <text key={i} x={d.x} y="268" fill="var(--text-tertiary)" fontSize="9" textAnchor="middle">
                      {d.label}
                    </text>
                  ))}
                  {/* X-axis baseline */}
                  <line x1={PLOT.left} y1={PLOT.bottom} x2={PLOT.right} y2={PLOT.bottom} stroke="var(--border)" strokeWidth="0.6" />
                </svg>
              </>
            ) : (
              <div className="empty-state" style={{ padding: 24 }}>
                <h3>暂无资金曲线</h3>
                <p>当前回测未返回权益轨迹。</p>
              </div>
            )}
          </div>

          {/* Data sources */}
          <div className="card">
            <h4 style={{ marginBottom: 12 }}>数据来源</h4>
            <div className="data-source-strip" style={{ marginTop: 0 }}>
              {result.data_sources?.map(item => (
                <div key={`${item.symbol}-${item.data_source}`}>
                  <strong>{item.symbol}</strong> · {item.data_source} · {item.bar_count} bars
                  {item.warning && <span style={{ color: 'var(--red)', marginLeft: 8 }}>{item.warning}</span>}
                </div>
              ))}
            </div>
          </div>

          {/* Trades */}
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h4>交易明细</h4>
              {result.trades?.length ? (
                <button
                  className="btn-outline"
                  style={{ fontSize: 12, padding: '4px 14px' }}
                  onClick={() => {
                    const headers = ['#', '标的', '入场时间', '入场价', '出场时间', '出场价', '数量', '盈亏']
                    const rows = result.trades.map((t, i) => [
                      i + 1,
                      t.symbol || result.symbols?.[0] || '',
                      t.entry_time || '',
                      t.entry_price?.toFixed(2) ?? '',
                      t.exit_time || '',
                      t.exit_price?.toFixed(2) ?? '',
                      t.quantity?.toFixed(2) ?? '',
                      t.pnl?.toFixed(2) ?? '',
                    ])
                    const csv = [headers, ...rows].map(r => r.join(',')).join('\n')
                    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
                    const url = URL.createObjectURL(blob)
                    const a = document.createElement('a')
                    a.href = url
                    a.download = `trades_${result.strategy_name || 'backtest'}_${result.start_date}_${result.end_date}.csv`
                    a.click()
                    URL.revokeObjectURL(url)
                  }}
                >
                  导出 CSV
                </button>
              ) : null}
            </div>
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
                    {result.trades.map((t, i) => (
                      <tr key={`${t.symbol || 'sym'}-${t.entry_time || i}-${i}`}>
                        <td>{i + 1}</td>
                        <td>{t.symbol || result.symbols?.[0] || '--'}</td>
                        <td>{formatDate(t.entry_time)}</td>
                        <td>${formatNumber(t.entry_price)}</td>
                        <td>{formatDate(t.exit_time)}</td>
                        <td>${formatNumber(t.exit_price)}</td>
                        <td>{formatNumber(t.quantity)}</td>
                        <td className={(t.pnl || 0) >= 0 ? 'positive' : 'negative'}>
                          {(t.pnl || 0) >= 0 ? '+' : ''}${formatNumber(t.pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state" style={{ padding: 24 }}>
                <h3>暂无交易明细</h3>
                <p>当前策略在此区间未触发交易信号。</p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
