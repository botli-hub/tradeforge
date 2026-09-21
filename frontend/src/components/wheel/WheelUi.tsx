/** Wheel 共享 UI 原语 — Badge / 价格条 / 语义色（走 CSS token） */
import type { ReactNode } from 'react'

export type SemColor = 'green' | 'orange' | 'red' | 'blue' | 'purple'

/** @deprecated 优先用 CSS class w-badge / var(--green)；保留兼容旧内联 */
export const C = {
  green: 'var(--green, #00C805)',
  orange: '#fb923c',
  red: 'var(--red, #FF5000)',
  blue: '#38bdf8',
  purple: '#a78bfa',
} as const

export function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtMoney(v: number) {
  if (!Number.isFinite(v)) return '--'
  if (Math.abs(v) >= 1000) {
    return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  }
  return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

export function Badge({
  color = 'blue',
  children,
  title,
}: {
  color?: SemColor
  children: ReactNode
  title?: string
}) {
  return (
    <span title={title} className={`w-badge w-badge-${color}`}>
      {children}
    </span>
  )
}

export function Stat({
  label,
  value,
  color,
}: {
  label: string
  value: string
  color?: SemColor
}) {
  return (
    <div className="w-stat">
      <div className="w-stat-label">{label}</div>
      <div className={`w-stat-value${color ? ` is-${color}` : ''}`}>{value}</div>
    </div>
  )
}

export function StatusDot({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span className="w-status-dot">
      <span className={`w-status-dot-mark ${ok == null ? 'muted' : ok ? 'ok' : 'bad'}`} />
      {label}
    </span>
  )
}


/** 现价 · 愿接(推荐价) — 全站统一;不再展示可手改 floor / 双轨参考 */
export function TargetPriceStrip({
  spot,
  floor,
  suggested,
  suggestedDelta: _suggestedDelta,
  size = 'md',
}: {
  spot?: number | null
  /** @deprecated 愿接=推荐价;传入 floor 仅作 fallback */
  floor?: number | null
  suggested?: number | null
  suggestedDelta?: number | null
  size?: 'sm' | 'md' | 'lg'
}) {
  // 愿接唯一源=推荐价;无推荐时回退缓存 floor
  const willing = (suggested != null && Number.isFinite(Number(suggested)) && Number(suggested) > 0)
    ? Number(suggested)
    : (floor != null && Number.isFinite(Number(floor)) && Number(floor) > 0 ? Number(floor) : null)
  const sz = size === 'sm' ? 'compact' : size === 'lg' ? 'lg' : ''
  return (
    <span className={`price-strip ${sz}`.trim()} title="现价=日K收盘 · 愿接=推荐价(市场结构,不可手改)">
      <span className="ps-item">
        现价 <b>${spot != null && Number.isFinite(Number(spot)) ? fmt(Number(spot)) : '--'}</b>
      </span>
      <span className="ps-item ps-ref-flat">
        愿接 <b>${willing != null ? fmt(willing) : '--'}</b>
        <span style={{ opacity: 0.7, marginLeft: 4, fontSize: '0.9em' }}>推荐价</span>
      </span>
    </span>
  )
}

/** 风险标签：硬=红 badge，软=灰点（title 出全文） */
export function RiskMarks({ hard, soft, maxSoft = 2 }: {
  hard?: string[]
  soft?: string[]
  maxSoft?: number
}) {
  const h = hard || []
  const s = soft || []
  if (!h.length && !s.length) return null
  return (
    <span className="w-risk-marks">
      {h.slice(0, 2).map(t => (
        <Badge key={t} color="red" title={t}>{t.length > 6 ? `${t.slice(0, 6)}…` : t}</Badge>
      ))}
      {s.length > 0 && (
        <span
          className="w-risk-soft-dot"
          title={s.join(' · ')}
        >
          {s.length > maxSoft ? `·${s.length}` : '·'}
        </span>
      )}
    </span>
  )
}
