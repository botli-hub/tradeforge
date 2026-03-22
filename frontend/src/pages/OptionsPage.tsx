import { useEffect, useMemo, useState } from 'react'
import { getAppSettings, getOptionChain, getOptionExpirations, getOptionPayoff, subscribeSettings, type AppSettings } from '../services/api'
import StockSelect from '../components/StockSelect'

type StrategyType = 'long_call' | 'long_put' | 'bull_call_spread' | 'bear_put_spread'

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function buildSvgPath(points: { underlying_price: number; pnl: number }[]) {
  if (!points.length) return ''
  const width = 720
  const height = 280
  const minX = Math.min(...points.map(p => p.underlying_price))
  const maxX = Math.max(...points.map(p => p.underlying_price))
  const minY = Math.min(...points.map(p => p.pnl))
  const maxY = Math.max(...points.map(p => p.pnl))
  const rangeX = Math.max(maxX - minX, 1)
  const rangeY = Math.max(maxY - minY, 1)

  return points.map((point, index) => {
    const x = ((point.underlying_price - minX) / rangeX) * width
    const y = height - ((point.pnl - minY) / rangeY) * height
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
  }).join(' ')
}

export default function OptionsPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [symbol, setSymbol] = useState('AAPL')
  const [inputSymbol, setInputSymbol] = useState('AAPL')
  const [expirations, setExpirations] = useState<string[]>([])
  const [selectedExpiry, setSelectedExpiry] = useState('')
  const [chain, setChain] = useState<any | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [strategy, setStrategy] = useState<StrategyType>('long_call')
  const [quantity, setQuantity] = useState(1)
  const [selectedStrike, setSelectedStrike] = useState<number>(0)
  const [lowerStrike, setLowerStrike] = useState<number>(0)
  const [upperStrike, setUpperStrike] = useState<number>(0)
  const [payoff, setPayoff] = useState<any | null>(null)

  useEffect(() => {
    const unsubscribe = subscribeSettings(next => setSettings(next))
    return unsubscribe
  }, [])

  useEffect(() => {
    void loadExpirations(symbol)
  }, [symbol, settings.marketHost, settings.marketPort])

  useEffect(() => {
    if (selectedExpiry) {
      void loadChain(symbol, selectedExpiry)
    }
  }, [selectedExpiry, symbol, settings.marketHost, settings.marketPort])

  useEffect(() => {
    if (chain) {
      void refreshPayoff()
    }
  }, [strategy, quantity, selectedStrike, lowerStrike, upperStrike, chain])

  const strikeRows = useMemo(() => {
    const contracts = chain?.contracts || []
    const rows = new Map<number, any>()
    contracts.forEach((contract: any) => {
      const current = rows.get(contract.strike) || { strike: contract.strike }
      if (contract.option_type === 'CALL') current.call = contract
      if (contract.option_type === 'PUT') current.put = contract
      rows.set(contract.strike, current)
    })
    return Array.from(rows.values()).sort((a, b) => a.strike - b.strike)
  }, [chain])

  const strikes = useMemo(() => strikeRows.map(row => row.strike), [strikeRows])

  async function loadExpirations(nextSymbol: string) {
    setLoading(true)
    setError('')
    try {
      const res = await getOptionExpirations(nextSymbol, settings)
      setExpirations(res.expirations)
      setSelectedExpiry(prev => prev || res.expirations[0] || '')
    } catch (e: any) {
      setError(e.message || '到期日加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function loadChain(nextSymbol: string, expiry: string) {
    setLoading(true)
    setError('')
    try {
      const res = await getOptionChain(nextSymbol, expiry, settings)
      setChain(res)
      const rows = new Map<number, any>()
      ;(res.contracts || []).forEach((contract: any) => rows.set(contract.strike, true))
      const ordered = Array.from(rows.keys()).sort((a, b) => a - b)
      const spot = Number(res.spot_price || 0)
      const atm = ordered.reduce((best, strike) => Math.abs(strike - spot) < Math.abs(best - spot) ? strike : best, ordered[0] || 0)
      const atmIndex = Math.max(ordered.indexOf(atm), 0)
      setSelectedStrike(atm)
      setLowerStrike(ordered[Math.max(atmIndex - 1, 0)] || atm)
      setUpperStrike(ordered[Math.min(atmIndex + 1, ordered.length - 1)] || atm)
    } catch (e: any) {
      setError(e.message || '期权链加载失败')
      setChain(null)
    } finally {
      setLoading(false)
    }
  }

  function findContract(strike: number, optionType: 'CALL' | 'PUT') {
    return (chain?.contracts || []).find((contract: any) => contract.strike === strike && contract.option_type === optionType)
  }

  function buildLegs() {
    const call = findContract(selectedStrike, 'CALL')
    const put = findContract(selectedStrike, 'PUT')
    const lowerCall = findContract(lowerStrike, 'CALL')
    const upperCall = findContract(upperStrike, 'CALL')
    const lowerPut = findContract(lowerStrike, 'PUT')
    const upperPut = findContract(upperStrike, 'PUT')

    switch (strategy) {
      case 'long_call':
        return call ? [{ option_type: 'CALL', side: 'LONG', strike: selectedStrike, premium: call.ask, quantity }] : []
      case 'long_put':
        return put ? [{ option_type: 'PUT', side: 'LONG', strike: selectedStrike, premium: put.ask, quantity }] : []
      case 'bull_call_spread':
        return lowerCall && upperCall ? [
          { option_type: 'CALL', side: 'LONG', strike: lowerStrike, premium: lowerCall.ask, quantity },
          { option_type: 'CALL', side: 'SHORT', strike: upperStrike, premium: upperCall.bid, quantity },
        ] : []
      case 'bear_put_spread':
        return upperPut && lowerPut ? [
          { option_type: 'PUT', side: 'LONG', strike: upperStrike, premium: upperPut.ask, quantity },
          { option_type: 'PUT', side: 'SHORT', strike: lowerStrike, premium: lowerPut.bid, quantity },
        ] : []
      default:
        return []
    }
  }

  async function refreshPayoff() {
    if (!chain) return
    const legs = buildLegs()
    if (legs.length === 0) {
      setPayoff(null)
      return
    }

    try {
      const res = await getOptionPayoff({
        strategy,
        underlying_price: chain.spot_price,
        legs,
      })
      setPayoff({ ...res, legs })
    } catch (e: any) {
      setError(e.message || '收益计算失败')
    }
  }

  const path = buildSvgPath(payoff?.points || [])

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 12, flexWrap: 'wrap' }}>
        <h2>期权</h2>
        <span className="tag ready">
          期权数据源：Futu
        </span>
      </div>

      <div className="card">
        <div className="search-bar" style={{ marginBottom: 12 }}>
          <StockSelect
            value={inputSymbol}
            onChange={v => setInputSymbol(v)}
            style={{ flex: 1 }}
          />
          <button className="btn" onClick={() => setSymbol(inputSymbol.trim().toUpperCase() || 'AAPL')}>加载期权链</button>
        </div>
        <div className="option-toolbar">
          <div className="option-field">
            <label>到期日</label>
            <select value={selectedExpiry} onChange={e => setSelectedExpiry(e.target.value)}>
              {expirations.map(expiry => <option key={expiry} value={expiry}>{expiry}</option>)}
            </select>
          </div>
          <div className="option-field">
            <label>策略模板</label>
            <select value={strategy} onChange={e => setStrategy(e.target.value as StrategyType)}>
              <option value="long_call">Long Call</option>
              <option value="long_put">Long Put</option>
              <option value="bull_call_spread">Bull Call Spread</option>
              <option value="bear_put_spread">Bear Put Spread</option>
            </select>
          </div>
          <div className="option-field">
            <label>张数</label>
            <input type="number" min={1} value={quantity} onChange={e => setQuantity(Number(e.target.value) || 1)} />
          </div>
        </div>
        {chain && (
          <div className="status-line" style={{ marginTop: 12 }}>
            <span className="tag ready">标的：{chain.symbol} {chain.name ? `· ${chain.name}` : ''}</span>
            <span className="tag positive">现价：{formatMoney(chain.spot_price)}</span>
            <span className="tag draft">到期剩余：{chain.days_to_expiry} 天</span>
            <span className="tag draft">定价来源：{chain.pricing_source}</span>
          </div>
        )}
        {chain?.detail && <div style={{ marginTop: 10, color: '#ffb066', fontSize: 12 }}>{chain.detail}</div>}
      </div>

      {error && (
        <div className="card strategy-notice error">
          {error}
        </div>
      )}

      <div className="option-layout">
        <div className="card option-chain-card">
          <h3 style={{ marginBottom: 12 }}>期权链</h3>
          {loading ? (
            <div style={{ color: 'var(--text-secondary)' }}>加载中...</div>
          ) : strikeRows.length === 0 ? (
            <div style={{ color: 'var(--text-secondary)' }}>暂无期权链数据</div>
          ) : (
            <div className="option-table-wrap">
              <table className="trade-table option-chain-table">
                <thead>
                  <tr>
                    <th>Call 买</th>
                    <th>Call 卖</th>
                    <th>Call 最新</th>
                    <th>IV</th>
                    <th>Delta</th>
                    <th>Strike</th>
                    <th>Put Delta</th>
                    <th>Put IV</th>
                    <th>Put 最新</th>
                    <th>Put 买</th>
                    <th>Put 卖</th>
                  </tr>
                </thead>
                <tbody>
                  {strikeRows.map(row => (
                    <tr key={row.strike} className={Math.abs(row.strike - (chain?.spot_price || 0)) < 0.01 ? 'option-atm' : ''}>
                      <td>{formatMoney(row.call?.bid)}</td>
                      <td>{formatMoney(row.call?.ask)}</td>
                      <td>{formatMoney(row.call?.last)}</td>
                      <td>{row.call?.iv ? `${(row.call.iv * 100).toFixed(1)}%` : '--'}</td>
                      <td>{row.call?.delta ?? '--'}</td>
                      <td style={{ fontWeight: 700 }}>{row.strike}</td>
                      <td>{row.put?.delta ?? '--'}</td>
                      <td>{row.put?.iv ? `${(row.put.iv * 100).toFixed(1)}%` : '--'}</td>
                      <td>{formatMoney(row.put?.last)}</td>
                      <td>{formatMoney(row.put?.bid)}</td>
                      <td>{formatMoney(row.put?.ask)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card option-builder-card">
          <h3 style={{ marginBottom: 12 }}>组合分析</h3>

          {(strategy === 'long_call' || strategy === 'long_put') && (
            <div className="option-field" style={{ marginBottom: 12 }}>
              <label>行权价</label>
              <select value={selectedStrike} onChange={e => setSelectedStrike(Number(e.target.value))}>
                {strikes.map(strike => <option key={strike} value={strike}>{strike}</option>)}
              </select>
            </div>
          )}

          {(strategy === 'bull_call_spread' || strategy === 'bear_put_spread') && (
            <div className="spread-config">
              <div className="option-field">
                <label>下侧行权价</label>
                <select value={lowerStrike} onChange={e => setLowerStrike(Number(e.target.value))}>
                  {strikes.map(strike => <option key={strike} value={strike}>{strike}</option>)}
                </select>
              </div>
              <div className="option-field">
                <label>上侧行权价</label>
                <select value={upperStrike} onChange={e => setUpperStrike(Number(e.target.value))}>
                  {strikes.map(strike => <option key={strike} value={strike}>{strike}</option>)}
                </select>
              </div>
            </div>
          )}

          <div className="option-legs">
            {(payoff?.legs || []).map((leg: any, index: number) => (
              <div key={index} className="option-leg-card">
                <div><strong>{leg.side}</strong> {leg.option_type}</div>
                <div>Strike: {leg.strike}</div>
                <div>Premium: {formatMoney(leg.premium)}</div>
                <div>Qty: {leg.quantity}</div>
              </div>
            ))}
          </div>

          <button className="btn" style={{ marginTop: 12 }} onClick={() => refreshPayoff()}>重新计算收益</button>

          {payoff && (
            <>
              <div className="metrics-grid" style={{ marginTop: 16 }}>
                <div className="metric-card">
                  <div className="value">{payoff.summary.max_profit === null ? '∞' : formatMoney(payoff.summary.max_profit)}</div>
                  <div className="label">最大收益</div>
                </div>
                <div className="metric-card">
                  <div className="value negative">{formatMoney(payoff.summary.max_loss)}</div>
                  <div className="label">最大亏损</div>
                </div>
                <div className="metric-card">
                  <div className="value">{(payoff.summary.breakeven_points || []).join(', ') || '--'}</div>
                  <div className="label">盈亏平衡点</div>
                </div>
                <div className="metric-card">
                  <div className="value">{chain?.days_to_expiry ?? '--'}</div>
                  <div className="label">距到期天数</div>
                </div>
              </div>

              <div className="payoff-chart-wrap">
                <svg viewBox="0 0 720 280" className="payoff-chart">
                  <line x1="0" y1="140" x2="720" y2="140" stroke="var(--border)" strokeWidth="1" />
                  <path d={path} fill="none" stroke="var(--green)" strokeWidth="3" />
                </svg>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
