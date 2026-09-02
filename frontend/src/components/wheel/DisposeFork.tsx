/** 过线 / 持仓期事件日分叉:落袋 vs 续拿(方向赌注,不是默认吃θ) */
export type DisposeForkData = {
  kind?: string
  bag?: { label?: string; copy?: string }
  hold?: { label?: string; copy?: string; directional_bet?: boolean }
  profit_target_pct?: number
  note?: string
  event?: {
    type?: string
    type_label?: string
    date?: string
    days_to_event?: number
    days_to_expiry?: number
    urgent?: boolean
  }
}

export default function DisposeFork({
  fork,
  compact,
  onBag,
  onHold,
}: {
  fork: DisposeForkData
  compact?: boolean
  onBag?: () => void
  onHold?: () => void
}) {
  if (!fork) return null
  return (
    <div className="dispose-fork" style={{ marginTop: compact ? 6 : 10 }}>
      <div className="home-todo-label" style={{ marginTop: 0 }}>
        {fork.kind === 'event_dispose'
          ? `${fork.event?.type_label || '事件'}日分叉 · 落袋 vs 续拿`
          : '过线分叉 · 落袋 vs 续拿'}
      </div>
      {fork.event?.date && (
        <div className="opp-row-meta" style={{ marginTop: 4 }}>
          {fork.event.type_label || '事件'} {fork.event.date}
          {fork.event.days_to_expiry != null ? ` · DTE${fork.event.days_to_expiry}` : ''}
          {fork.event.days_to_event === 0
            ? ' · 事件当日'
            : fork.event.days_to_event != null
              ? ` · T-${fork.event.days_to_event}`
              : ''}
        </div>
      )}
      <div className="manage-alt-grid" style={{ marginTop: 6 }}>
        <div className="manage-alt-card preferred">
          <h4>{fork.bag?.label || '落袋'}</h4>
          <p>{fork.bag?.copy}</p>
          {onBag && (
            <button type="button" className="btn btn-primary btn-sm" style={{ width: '100%' }} onClick={onBag}>
              登记买回
            </button>
          )}
        </div>
        <div className="manage-alt-card">
          <h4>{fork.hold?.label || '续拿'}</h4>
          <p>{fork.hold?.copy || '续拿是方向赌注,不是默认继续吃θ。'}</p>
          {onHold && (
            <button type="button" className="btn btn-sm" style={{ width: '100%' }} onClick={onHold}>
              登记续拿（方向赌注）
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
