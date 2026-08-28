/** 今日页:持股待挂 CC。独立一档,不塞进必须处理/优先开仓。 */
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
  const hot = rows.filter(r => r.cc_grade === 'priority' || r.cc_grade === 'ready' || r.timing_ready)
  const wait = rows.filter(r => !(r.cc_grade === 'priority' || r.cc_grade === 'ready' || r.timing_ready))
  const stanceLabel = (s?: string) => s === 'income' ? '只收租' : '允许接货'

  const line = (p: Row) => {
    const cush = p.cc_timing?.cushion_pct ?? p.cushion_pct
    return (
      <div key={String(p.cycle_id)} className="opp-row" style={{ margin: '0 0 6px' }}>
        <div className="opp-row-main">
          <div className="opp-row-title">
            <span style={{
              fontSize: 11, padding: '1px 6px', borderRadius: 4, marginRight: 6,
              background: p.cc_grade === 'priority' ? 'var(--accent)' : 'var(--bg-secondary)',
              color: p.cc_grade === 'priority' ? '#fff' : 'var(--text-secondary)',
            }}>{p.cc_tag || (p.timing_ready ? '时机到' : '待时机')}</span>
            <b>{p.symbol}</b> {p.shares}股
          </div>
          <div className="opp-row-meta">
            {p.stance ? <span>{stanceLabel(p.stance)}</span> : null}
            {p.cost_basis != null ? <span>CB≈${Number(p.cost_basis).toFixed(2)}</span> : null}
            {cush != null ? <span>现价/成本 {cush >= 0 ? '+' : ''}{Number(cush).toFixed(1)}%</span> : null}
            {p.min_call_strike != null ? <span>strike≥${Number(p.min_call_strike).toFixed(2)}</span> : null}
            {p.uncovered_days != null ? <span>裸奔{p.uncovered_days}天</span> : null}
            {p.cc_hint ? <span style={{ opacity: 0.75 }}>{p.cc_hint}</span> : null}
          </div>
        </div>
        <div className="opp-row-actions">
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
    <div className="panel today-panel">
      <div className="panel-title">待挂 CC</div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
        持股卖 Call · 与 Put 优先开仓分档 · 参考,不自动下单
      </div>
      {rows.length === 0 && (
        <div className="home-todo-empty">暂无持股待挂</div>
      )}
      {hot.length > 0 && hot.map(line)}
      {wait.length > 0 && (
        <>
          <div style={{ fontSize: 12, margin: '8px 0 4px', opacity: 0.75 }}>其它持股(不足一张等)</div>
          {wait.map(line)}
        </>
      )}
    </div>
  )
}
