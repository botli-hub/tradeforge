/** CSP 平仓机会成本:释放担保 / 可再开对照. 决策辅助,不自动下单. */
function fmt(v: number | null | undefined, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export type CspOppCost = {
  aid_only?: boolean
  collateral_released?: number | null
  captured_if_close_usd?: number | null
  remaining_ann_if_hold?: number | null
  captured_ann?: number | null
  reopen?: {
    label?: string
    target_ann?: number | null
    est_credit_same_dte?: number | null
    note?: string | null
  } | null
  note?: string
  error?: boolean
}

export default function CspOpportunityCost({
  cost,
  compact,
}: {
  cost?: CspOppCost | null
  compact?: boolean
}) {
  if (!cost) return null
  return (
    <div className="books-col" style={{ marginTop: compact ? 4 : 8 }}>
      <div className="books-col-title">CSP 机会成本（辅助）</div>
      {cost.error ? (
        <div>暂不可算</div>
      ) : (
        <>
          <div>释放担保 ${fmt(cost.collateral_released)}</div>
          <div>若平仓兑现 ${fmt(cost.captured_if_close_usd)}</div>
          <div>续拿剩余年化 {cost.remaining_ann_if_hold ?? '--'}%</div>
          {cost.reopen && (
            <div>
              {cost.reopen.label || '可再开'}
              {cost.reopen.target_ann != null ? ` · 目标年化 ${cost.reopen.target_ann}%` : ''}
              {cost.reopen.est_credit_same_dte != null ? ` · 同DTE约 $${fmt(cost.reopen.est_credit_same_dte)}` : ''}
            </div>
          )}
          {!compact && cost.reopen?.note && <div>{cost.reopen.note}</div>}
          <div style={{ opacity: 0.75 }}>{cost.note || '决策辅助,不自动下单'}</div>
        </>
      )}
    </div>
  )
}
