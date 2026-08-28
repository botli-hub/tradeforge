/** 今日页优先开仓:与必须处理 / 待挂 CC / 资金条分开。CALL 不进此档。 */
import EmptyState from '../ui/EmptyState'
import { Badge, fmt, type SemColor } from './WheelUi'
import { DTE_BUCKET_META, type DteBucket, type TradeTier } from '../../services/wheelProduct'

export type TodayOpenPick = {
  id: string
  symbol: string
  side?: string
  strike?: number | null
  annualized?: number | null
  bid?: number | null
  dte?: number | null
  daily_rent?: number | null
  dte_bucket?: DteBucket
  trade_tier?: TradeTier
  signalText: string
  signalColor: SemColor
  signalTitle?: string
}

export default function TodayPrimaryOpens({
  picks,
  putBlocked,
  scanning,
  headline,
  onScan,
  onRegister,
  onCopyMemo,
  onMore,
}: {
  picks: TodayOpenPick[]
  putBlocked?: boolean
  scanning?: boolean
  headline?: string
  onScan: () => void
  onRegister: (id: string) => void
  onCopyMemo: (id: string) => void
  onMore: () => void
}) {
  return (
    <div className="panel today-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div className="panel-title" style={{ margin: 0 }}>优先开仓</div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onMore}>更多</button>
      </div>
      {putBlocked && (
        <div className="home-todo-empty" style={{ marginBottom: 8 }}>
          压力解除前不推新 Put · 可看待挂 CC 或触线
        </div>
      )}
      {!putBlocked && picks.length === 0 ? (
        <EmptyState
          title="暂无优先开仓"
          description="需要可交易高分 + 触线确认。可先扫描，或查看可排单/观察。"
        >
          <button type="button" className="btn btn-primary btn-sm" disabled={scanning} onClick={onScan}>
            扫描
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onMore}>看可排单</button>
        </EmptyState>
      ) : (
        picks.map(r => (
          <div key={r.id} className="opp-row" style={{ margin: '0 0 6px' }}>
            <div className="opp-row-main">
              <div className="opp-row-title">
                <Badge color={r.signalColor} title={r.signalTitle}>{r.signalText}</Badge>
                {r.trade_tier === 'PRIORITY' && <Badge color="green">优先</Badge>}
                {r.trade_tier === 'QUEUE' && <Badge color="blue">可排单</Badge>}
                {r.symbol}
                <span style={{ fontWeight: 500, color: r.side === 'PUT' ? 'var(--green)' : 'var(--purple)' }}>
                  {r.side === 'PUT' ? '卖Put' : r.side === 'CALL' ? '卖Call' : r.side}
                </span>
                {r.strike != null && <span style={{ opacity: 0.75 }}>${r.strike}</span>}
              </div>
              <div className="opp-row-meta">
                {r.annualized != null && <span>年化 <b style={{ color: 'var(--green)' }}>{fmt(r.annualized, 1)}%</b></span>}
                {r.bid != null && <span>bid {fmt(r.bid, 2)}</span>}
                {r.daily_rent != null && <span>日租 {fmt(r.daily_rent, 2)}</span>}
                {r.dte != null && <span>DTE {r.dte}</span>}
                {r.dte_bucket && <span>{DTE_BUCKET_META[r.dte_bucket]?.label}</span>}
              </div>
            </div>
            <div className="opp-row-actions">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => onCopyMemo(r.id)}>备忘</button>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => onRegister(r.id)}>登记</button>
            </div>
          </div>
        ))
      )}
      {headline && (
        <div style={{ marginTop: 6, fontSize: 13, color: 'var(--text-tertiary)' }}>{headline}</div>
      )}
    </div>
  )
}
