import type { TradingOrder } from '../services/api'

interface Props {
  orders: TradingOrder[]
}

export default function OrdersTable({ orders }: Props) {
  return (
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
            {orders.slice(0, 8).map(order => (
              <tr key={order.order_id}>
                <td>{order.order_id}</td>
                <td>{order.symbol}</td>
                <td>{order.side}</td>
                <td>{order.price?.toFixed?.(2) ?? order.price}</td>
                <td>{order.quantity}</td>
                <td>{order.status}</td>
                <td>{String(order.create_time).slice(11, 19)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
