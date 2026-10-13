import { visibleSymbolCycleDots } from './symbolCycleDots'

const DOT_COLOR: Record<string, string> = {
  CSP_OPEN: '#38bdf8',
  HOLDING: '#fbbf24',
  CC_OPEN: '#a78bfa',
}

const DOT_LABEL: Record<string, string> = {
  CSP_OPEN: '卖Put中',
  HOLDING: '持股',
  CC_OPEN: '卖Call中',
}

/** Small status dots on a symbol card. Historical IDLE/CLOSED cycles are omitted. */
export function SymbolCycleDots({ cycles }: { cycles: { id: string; status: string }[] }) {
  if (cycles.length === 0) {
    return <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>未开轮</span>
  }
  const open = visibleSymbolCycleDots(cycles)
  if (open.length === 0) return null
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 3,
        justifyContent: 'flex-end',
        alignContent: 'center',
        maxWidth: 120,
        flex: '0 1 120px',
      }}
      aria-label="在场轮子"
    >
      {open.map(c => (
        <span
          key={c.id}
          title={DOT_LABEL[c.status] || c.status}
          style={{
            width: 9,
            height: 9,
            borderRadius: '50%',
            background: DOT_COLOR[c.status] || 'var(--text-secondary)',
            display: 'inline-block',
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  )
}
