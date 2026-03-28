import type { TradingPosition } from '../services/api'

interface Props {
  positions: TradingPosition[]
}

export default function PositionsTable({ positions }: Props) {
  return (
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
            {positions.map((position, idx) => (
              <tr key={`${position.symbol}-${idx}`}>
                <td>{position.symbol}</td>
                <td>{position.direction}</td>
                <td>{position.quantity}</td>
                <td>{position.avg_cost?.toFixed?.(2) ?? position.avg_cost}</td>
                <td>{position.current_price?.toFixed?.(2) ?? position.current_price}</td>
                <td className={position.unrealized_pnl >= 0 ? 'positive' : 'negative'}>
                  {position.unrealized_pnl?.toFixed?.(2) ?? position.unrealized_pnl}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
