import { useState, useEffect } from 'react'
import { getStrategies, runBacktest, getBacktestResult } from '../services/api'

interface Strategy {
  id: string
  name: string
}

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selectedStrategy, setSelectedStrategy] = useState('')
  const [symbol, setSymbol] = useState('AAPL')
  const startDate = '2024-01-01'
  const endDate = '2025-01-01'
  const [initialCapital, setInitialCapital] = useState(100000)
  const [feeRate, setFeeRate] = useState(0.0003)
  const [slippage, setSlippage] = useState(0.001)
  
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<any>(null)

  useEffect(() => {
    loadStrategies()
  }, [])

  async function loadStrategies() {
    try {
      const data = await getStrategies()
      setStrategies(data)
      if (data.length > 0) {
        setSelectedStrategy(data[0].id)
      }
    } catch (e) {
      console.error('Failed to load strategies:', e)
    }
  }

  async function handleRun() {
    if (!selectedStrategy) {
      alert('请选择策略')
      return
    }

    setRunning(true)
    setProgress(0)
    setResult(null)

    // 模拟进度
    const interval = setInterval(() => {
      setProgress(p => Math.min(p + 10, 90))
    }, 200)

    try {
      const data = await runBacktest({
        strategy_id: selectedStrategy,
        symbol,
        timeframe: '1d',
        start_date: startDate,
        end_date: endDate,
        initial_capital: initialCapital,
        fee_rate: feeRate,
        slippage
      })

      clearInterval(interval)
      setProgress(100)

      // 获取结果
      const resultData = await getBacktestResult(data.id)
      setResult(resultData.metrics)

    } catch (e) {
      console.error('Backtest failed:', e)
      alert('回测失败')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="page">
      <h2>回测</h2>

      {/* 配置面板 */}
      <div className="card">
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 16}}>
          <div>
            <label style={{display: 'block', color: '#888', fontSize: 12, marginBottom: 6}}>选择策略</label>
            <select 
              value={selectedStrategy} 
              onChange={e => setSelectedStrategy(e.target.value)}
              style={{width: '100%'}}
            >
              {strategies.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{display: 'block', color: '#888', fontSize: 12, marginBottom: 6}}>标的</label>
            <input 
              type="text" 
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              style={{width: '100%'}}
            />
          </div>
          <div>
            <label style={{display: 'block', color: '#888', fontSize: 12, marginBottom: 6}}>时间区间</label>
            <input 
              type="text" 
              value={`${startDate} ~ ${endDate}`}
              style={{width: '100%'}}
            />
          </div>
        </div>

        <div style={{display: 'flex', gap: 12, alignItems: 'flex-end'}}>
          <div>
            <label style={{display: 'block', color: '#888', fontSize: 12, marginBottom: 6}}>初始资金</label>
            <input 
              type="number" 
              value={initialCapital}
              onChange={e => setInitialCapital(Number(e.target.value))}
              style={{width: 120}}
            />
          </div>
          <div>
            <label style={{display: 'block', color: '#888', fontSize: 12, marginBottom: 6}}>手续费</label>
            <input 
              type="number" 
              value={feeRate}
              step="0.0001"
              onChange={e => setFeeRate(Number(e.target.value))}
              style={{width: 80}}
            />
          </div>
          <div>
            <label style={{display: 'block', color: '#888', fontSize: 12, marginBottom: 6}}>滑点</label>
            <input 
              type="number" 
              value={slippage}
              step="0.001"
              onChange={e => setSlippage(Number(e.target.value))}
              style={{width: 80}}
            />
          </div>
          <button 
            className="btn" 
            style={{marginLeft: 'auto'}}
            onClick={handleRun}
            disabled={running}
          >
            {running ? '运行中...' : '开始回测'}
          </button>
        </div>

        {/* 进度条 */}
        {running && (
          <div className="progress-bar" style={{marginTop: 16}}>
            <div className="progress-fill" style={{width: `${progress}%`}} />
          </div>
        )}
      </div>

      {/* 回测结果 */}
      {result && (
        <>
          <div className="card">
            <h4 style={{marginBottom: 12}}>回测报告</h4>
            
            <div className="metrics-grid">
              <div className="metric-card">
                <div className="value positive">{result.total_return ? (result.total_return * 100).toFixed(1) : 0}%</div>
                <div className="label">总收益率</div>
              </div>
              <div className="metric-card">
                <div className="value positive">{result.annual_return ? (result.annual_return * 100).toFixed(1) : 0}%</div>
                <div className="label">年化收益</div>
              </div>
              <div className="metric-card">
                <div className="value">{result.sharpe_ratio || 0}</div>
                <div className="label">夏普比率</div>
              </div>
              <div className="metric-card">
                <div className="value negative">{result.max_drawdown ? (result.max_drawdown * 100).toFixed(1) : 0}%</div>
                <div className="label">最大回撤</div>
              </div>
              <div className="metric-card">
                <div className="value">{result.win_rate ? (result.win_rate * 100).toFixed(0) : 0}%</div>
                <div className="label">胜率</div>
              </div>
              <div className="metric-card">
                <div className="value">{result.profit_factor || 0}</div>
                <div className="label">盈亏比</div>
              </div>
              <div className="metric-card">
                <div className="value">{result.total_trades || 0}</div>
                <div className="label">交易次数</div>
              </div>
              <div className="metric-card">
                <div className="value">{result.avg_holding_days || 0}天</div>
                <div className="label">平均持仓</div>
              </div>
            </div>
          </div>

          {/* 交易明细 */}
          {result.trades && result.trades.length > 0 && (
            <div className="card">
              <h4 style={{marginBottom: 12}}>交易明细</h4>
              <table className="trade-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>入场时间</th>
                    <th>买入价</th>
                    <th>出场时间</th>
                    <th>卖出价</th>
                    <th>盈亏</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t: any, i: number) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td>{t.entry_time?.split('T')[0]}</td>
                      <td>${t.entry_price?.toFixed(2)}</td>
                      <td>{t.exit_time?.split('T')[0]}</td>
                      <td>${t.exit_price?.toFixed(2)}</td>
                      <td className={t.pnl >= 0 ? 'positive' : 'negative'}>
                        {t.pnl >= 0 ? '+' : ''}${t.pnl?.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
