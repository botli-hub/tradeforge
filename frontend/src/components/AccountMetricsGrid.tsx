import type { TradingAccount } from '../services/api'

interface Props {
  account: TradingAccount | null
}

export default function AccountMetricsGrid({ account }: Props) {
  const items = [
    { label: '现金', value: account?.cash?.toFixed?.(0) ?? '--' },
    { label: '可用购买力', value: account?.buying_power?.toFixed?.(0) ?? '--' },
    { label: '持仓市值', value: account?.market_value?.toFixed?.(0) ?? '--' },
    { label: '总资产', value: account?.total_assets?.toFixed?.(0) ?? '--' },
  ]

  return (
    <div className="metrics-grid">
      {items.map(item => (
        <div key={item.label} className="metric-card">
          <div className="value">{item.value}</div>
          <div className="label">{item.label}</div>
        </div>
      ))}
    </div>
  )
}
