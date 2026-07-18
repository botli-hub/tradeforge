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

function fmtSuggestedFloor(t: {
  suggested_floor?: number | string | null
  suggested_floor_delta?: number | string | null
}) {
  const price = t.suggested_floor == null || t.suggested_floor === ''
    ? NaN
    : Number(t.suggested_floor)
  if (!Number.isFinite(price) || price <= 0) return null
  const dRaw = t.suggested_floor_delta
  const d = dRaw == null || dRaw === '' ? null : Number(dRaw)
  const dOk = d != null && Number.isFinite(d)
  const dTxt = !dOk ? ''
    : d > 0 ? ` (+${fmt(d, Math.abs(d) < 1 ? 2 : 1)})`
      : d < 0 ? ` (${fmt(d, Math.abs(d) < 1 ? 2 : 1)})`
        : ' (±0)'
  return { price, deltaTxt: dTxt, delta: dOk ? d : null }
}

/** 现价 · 愿接 · 参考 — 全站统一 */
export function TargetPriceStrip({
  spot,
  floor,
  suggested,
  suggestedDelta,
  size = 'md',
}: {
  spot?: number | null
  floor?: number | null
  suggested?: number | null
  suggestedDelta?: number | null
  size?: 'sm' | 'md' | 'lg'
}) {
  const sf = fmtSuggestedFloor({
    suggested_floor: suggested,
    suggested_floor_delta: suggestedDelta ?? (
      suggested != null && floor != null ? Number(suggested) - Number(floor) : null
    ),
  })
  const refClass = sf?.delta == null ? 'ps-ref-flat'
    : Math.abs(sf.delta) < 0.5 ? 'ps-ref-flat'
      : sf.delta > 0 ? 'ps-ref-up' : 'ps-ref-down'
  const sz = size === 'sm' ? 'compact' : size === 'lg' ? 'lg' : ''
  return (
    <span className={`price-strip ${sz}`.trim()} title="现价=日K收盘 · 愿接=你的最高接货价 · 参考=市场结构建议">
      <span className="ps-item">
        现价 <b>${spot != null && Number.isFinite(Number(spot)) ? fmt(Number(spot)) : '--'}</b>
      </span>
      <span className="ps-item">
        愿接 <b>${floor != null && Number.isFinite(Number(floor)) ? fmt(Number(floor)) : '--'}</b>
      </span>
      <span className={`ps-item ${refClass}`}>
        参考 <b>{sf ? `$${fmt(sf.price)}${sf.deltaTxt}` : '--'}</b>
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
