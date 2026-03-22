import { useEffect, useState } from 'react'
import { getStocks, StockItem } from '../services/api'

interface Props {
  value: string
  onChange: (symbol: string) => void
  style?: React.CSSProperties
  className?: string
}

const MARKET_LABEL: Record<string, string> = {
  US: '美股',
  HK: '港股',
  CN: 'A股',
}

export default function StockSelect({ value, onChange, style, className }: Props) {
  const [stocks, setStocks] = useState<StockItem[]>([])

  useEffect(() => {
    getStocks({ enabled_only: true })
      .then(setStocks)
      .catch(() => {/* silently ignore — backend may not be running */})
  }, [])

  const grouped = stocks.reduce<Record<string, StockItem[]>>((acc, s) => {
    ;(acc[s.market] = acc[s.market] ?? []).push(s)
    return acc
  }, {})

  const marketOrder = ['US', 'HK', 'CN']

  function labelFor(s: StockItem) {
    if (s.market === 'US') return s.symbol
    return `${s.symbol} ${s.name}`
  }

  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      style={style}
      className={className}
    >
      {value && !stocks.find(s => s.symbol === value) && (
        <option value={value}>{value}</option>
      )}
      {marketOrder.map(market =>
        grouped[market]?.length ? (
          <optgroup key={market} label={MARKET_LABEL[market] ?? market}>
            {grouped[market].map(s => (
              <option key={s.symbol} value={s.symbol}>
                {labelFor(s)}
              </option>
            ))}
          </optgroup>
        ) : null
      )}
      {stocks.length === 0 && (
        <option value="" disabled>加载中...</option>
      )}
    </select>
  )
}
