import type { SearchStockResult } from '../services/api'

interface Props {
  results: SearchStockResult[]
  onSelect: (symbol: string) => void
}

export default function SearchResultsList({ results, onSelect }: Props) {
  if (results.length === 0) return null

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      {results.map(result => (
        <div
          key={result.symbol}
          onClick={() => onSelect(result.symbol)}
          style={{ padding: '8px 0', cursor: 'pointer', borderBottom: '1px solid #333' }}
        >
          <span style={{ color: 'var(--green)' }}>{result.symbol}</span>
          <span style={{ marginLeft: 12, color: 'var(--text-secondary)' }}>{result.name}</span>
        </div>
      ))}
    </div>
  )
}
