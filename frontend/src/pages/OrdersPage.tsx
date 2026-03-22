import { useEffect, useState } from 'react'
import { getAppSettings, getOrders, getTradingStatus, subscribeSettings, type AppSettings } from '../services/api'

export default function OrdersPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [orders, setOrders] = useState<any[]>([])
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
        setOrders([])
        return
      }

      const data = await getOrders()
      setOrders((data || []).slice().reverse())
      setLastRefreshAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    } catch (e: any) {
      setError(e.message || '订单加载失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 12, flexWrap: 'wrap' }}>
        <h2>订单</h2>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className={`tag ${connected ? 'ready' : 'draft'}`}>
            {connected ? `${settings.tradingAdapter.toUpperCase()} / ${settings.tradingEnv}` : '交易未连接'}
          </span>
          {lastRefreshAt && <span className="tag draft">最近刷新：{lastRefreshAt}</span>}
          <button className="btn-outline" onClick={() => refresh(false)}>{loading ? '刷新中...' : '刷新订单'}</button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ border: '1px solid rgba(233,69,96,0.35)', color: '#ffb0ba' }}>
          {error}
        </div>
      )}

      {!connected ? (
        <div className="card empty-state">
          <h3>交易未连接</h3>
          <p>请先到设置页连接 Futu 交易通道，再查看订单。</p>
        </div>
      ) : orders.length === 0 ? (
        <div className="card empty-state">
          <h3>暂无订单</h3>
          <p>当前还没有委托记录。</p>
        </div>
      ) : (
        <div className="card">
          <table className="trade-table">
            <thead>
              <tr>
                <th>订单号</th>
                <th>代码</th>
                <th>方向</th>
                <th>类型</th>
                <th>价格</th>
                <th>数量</th>
                <th>已成交</th>
                <th>状态</th>
                <th>时间</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {orders.map(order => (
                <tr key={order.order_id}>
                  <td>{order.order_id}</td>
                  <td>{order.symbol}</td>
                  <td className={order.side === 'BUY' ? 'positive' : 'negative'}>{order.side}</td>
                  <td>{order.order_type || '--'}</td>
                  <td>{order.price?.toFixed?.(2) ?? order.price}</td>
                  <td>{order.quantity}</td>
                  <td>{order.filled_quantity}</td>
                  <td>{order.status}</td>
                  <td>{String(order.create_time).replace('T', ' ').slice(0, 19)}</td>
                  <td>{order.message || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
