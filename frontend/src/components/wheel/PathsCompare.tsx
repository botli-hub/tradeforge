/** 接货 / 买回 / Roll 三列。推荐列高亮。 */
import type { WheelOpenPositionItem } from '../../services/api'

function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export default function PathsCompare({
  paths,
  compact,
}: {
  paths: NonNullable<WheelOpenPositionItem['paths']>
  compact?: boolean
}) {
  const rec = paths.recommend
  const cols: Array<{ key: 'assign' | 'close' | 'roll'; data: typeof paths.assign }> = [
    { key: 'assign', data: paths.assign },
    { key: 'close', data: paths.close },
    { key: 'roll', data: paths.roll },
  ]
  return (
    <div className="paths-grid" style={compact ? { margin: '6px 0 0' } : undefined}>
      {cols.map(({ key, data }) => (
        <div key={key} className={`paths-col${rec === key ? ' pick' : ''}`}>
          <div className="paths-col-title">
            {data?.label || key}
            {rec === key ? ' · 推荐' : ''}
          </div>
          {key === 'assign' && (
            <>
              <div>有效成本 ${fmt(paths.assign?.effective_cost)}</div>
              <div>vs 现价 {fmt(paths.assign?.vs_spot)}</div>
              <div>{paths.assign?.floor_ok ? '在计划内' : '偏离愿接/愿卖'}</div>
              {!compact && <div>过户/成交 ${fmt(paths.assign?.cash_due, 0)}</div>}
            </>
          )}
          {key === 'close' && (
            <>
              <div>保守买回 ${fmt(paths.close?.price)}</div>
              <div>点差 {paths.close?.spread_pct != null ? `${paths.close.spread_pct}%` : '--'}</div>
              <div>{paths.close?.fillable ? '可成交' : '点差宽/不可靠'}</div>
              {!compact && <div>PnL ${fmt(paths.close?.pnl_usd, 0)} · 释放 ${fmt(paths.close?.freed, 0)}</div>}
            </>
          )}
          {key === 'roll' && (
            <>
              <div>买回成本 ${fmt(paths.roll?.close_cost, 0)}</div>
              <div>净贷方需新卖 ≥ ${fmt(paths.roll?.min_new_premium)}</div>
              {paths.roll?.strike_cap != null && <div>strike 上限 {fmt(paths.roll.strike_cap)}</div>}
            </>
          )}
        </div>
      ))}
      {paths.recommend_reason && (
        <div className="paths-why">{paths.recommend_reason}</div>
      )}
    </div>
  )
}
