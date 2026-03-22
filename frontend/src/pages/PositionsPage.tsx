import { useEffect, useState } from 'react'
import { getAccount, getAppSettings, getPositions, getTradingStatus, subscribeSettings, type AppSettings } from '../services/api'

export default function PositionsPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [positions, setPositions] = useState<any[]>([])
  const [account, setAccount] = useState<any | null>(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastRefreshAt, setLastRefreshAt] = useState('')

  const pollMs = settings.tradingAdapter === 'futu' ? 3000 : 8000

  useEffect(() => {
    const unsubscribe = subscribeSettings(next => setSettings(next))
    return unsubscribe
  }, [])

  useEffect(() => {
    void refresh(false)
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh(true)
    }, pollMs)
    return () => window.clearInterval(timer)
  }, [pollMs])

  async function refresh(silent = false) {
    if (!silent) {
      setLoading(true)
      setError('')
    }

    try {
      const status = await getTradingStatus()
      setConnected(status.connected)

      if (!status.connected) {
        setPositions([])
        setAccount(null)
        return
      }

      const [positionData, accountData] = await Promise.all([
        getPositions(),
        getAccount(),
      ])
      setPositions(positionData || [])
      setAccount(accountData || null)
      setLastRefreshAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    } catch (e: any) {
      setError(e.message || '持仓加载失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 12, flexWrap: 'wrap' }}>
        <h2>持仓</h2>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className={`tag ${connected ? 'ready' : 'draft'}`}>
            {connected ? `${settings.tradingAdapter.toUpperCase()} / ${settings.tradingEnv}` : '交易未连接'}
          </span>
          {lastRefreshAt && <span className="tag draft">最近刷新：{lastRefreshAt}</span>}
          <button className="btn-outline" onClick={() => refresh(false)}>{loading ? '刷新中...' : '刷新持仓'}</button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ border: '1px solid rgba(233,69,96,0.35)', color: '#ffb0ba' }}>
          {error}
        </div>
      )}

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

      {!connected ? (
        <div className="card empty-state">
          <h3>交易未连接</h3>
          <p>请先到设置页连接 Futu 交易通道，再查看持仓。</p>
        </div>
      ) : positions.length === 0 ? (
        <div className="card empty-state">
          <h3>暂无持仓</h3>
          <p>当前账户没有持仓。</p>
        </div>
      ) : (
        <div className="card">
          <table className="trade-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>方向</th>
                <th>数量</th>
                <th>成本价</th>
                <th>现价</th>
                <th>浮盈亏</th>
                <th>已实现盈亏</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position, idx) => (
                <tr key={`${position.symbol}-${idx}`}>
                  <td>{position.symbol}</td>
                  <td className={position.direction === 'BUY' ? 'positive' : 'negative'}>{position.direction}</td>
                  <td>{position.quantity}</td>
                  <td>{position.avg_cost?.toFixed?.(2) ?? position.avg_cost}</td>
                  <td>{position.current_price?.toFixed?.(2) ?? position.current_price}</td>
                  <td className={position.unrealized_pnl >= 0 ? 'positive' : 'negative'}>
                    {position.unrealized_pnl?.toFixed?.(2) ?? position.unrealized_pnl}
                  </td>
                  <td className={position.realized_pnl >= 0 ? 'positive' : 'negative'}>
                    {position.realized_pnl?.toFixed?.(2) ?? position.realized_pnl}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
