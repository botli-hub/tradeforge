/** 今日页:持股待挂 CC。按时机分档,不一律「优先处理」。找 Call 仍由用户拍板。 */
type Row = {
  cycle_id?: string | number
  symbol?: string
  shares?: number
  cost_basis?: number
  min_call_strike?: number
  uncovered_days?: number
  cc_contracts?: number
  cc_grade?: string
  cc_tag?: string
  cc_hint?: string
  stance?: string
  show_find_call?: boolean
  timing_ready?: boolean
  cushion_pct?: number
  cc_timing?: { cushion_pct?: number }
}

export default function HoldingCcBanner({
  rows,
  loading,
  onFindCall,
}: {
  rows: Row[]
  loading?: boolean
  onFindCall: (row: Row) => void
}) {
  if (!rows.length) return null
  const hot = rows.filter(r => r.cc_grade === 'priority' || r.cc_grade === 'ready' || r.timing_ready)
  const wait = rows.filter(r => !(r.cc_grade === 'priority' || r.cc_grade === 'ready' || r.timing_ready))
  const stanceLabel = (s?: string) => s === 'income' ? '只收租' : '允许接货'

  const line = (p: Row) => {
    const cush = p.cc_timing?.cushion_pct ?? p.cushion_pct
    return (
      <div key={String(p.cycle_id)} style={{
        marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8,
        alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ fontSize: 13 }}>
          <span style={{
            fontSize: 11, padding: '1px 6px', borderRadius: 4, marginRight: 6,
            background: p.cc_grade === 'priority' ? 'var(--accent)' : 'var(--bg-secondary)',
            color: p.cc_grade === 'priority' ? '#fff' : 'var(--text-secondary)',
          }}>{p.cc_tag || (p.timing_ready ? '时机到' : '待时机')}</span>
          <b>{p.symbol}</b> {p.shares}股
          {p.stance ? ` · ${stanceLabel(p.stance)}` : ''}
          {p.cost_basis != null ? ` · CB≈$${Number(p.cost_basis).toFixed(2)}` : ''}
          {cush != null ? ` · 现价/成本 ${cush >= 0 ? '+' : ''}${Number(cush).toFixed(1)}%` : ''}
          {p.min_call_strike != null ? ` · strike≥$${Number(p.min_call_strike).toFixed(2)}` : ''}
          {p.uncovered_days != null ? ` · 裸奔${p.uncovered_days}天` : ''}
          {p.cc_hint ? <span style={{ opacity: 0.75 }}> · {p.cc_hint}</span> : null}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {p.show_find_call && (p.cc_contracts || 0) >= 1 && (
            <button type="button" className="btn btn-primary btn-sm"
              disabled={loading}
              onClick={() => onFindCall(p)}>
              找 Call
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="banner info" style={{ marginBottom: 10 }}>
      <b>持股待挂 CC</b>
      <span style={{ fontSize: 12, marginLeft: 8, opacity: 0.8 }}>
        与 Put 优先开仓分档 · 参考,不自动下单
      </span>
      {hot.length > 0 && hot.map(line)}
      {wait.length > 0 && (
        <>
          <div style={{ fontSize: 12, marginTop: 8, opacity: 0.75 }}>待时机(允许接货不默认天天挂)</div>
          {wait.map(line)}
        </>
      )}
    </div>
  )
}
