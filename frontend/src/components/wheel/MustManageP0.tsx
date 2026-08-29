/** 今日 P0 · 必须处理：浮盈两本账并排（action_code 仅参考） */
import type { WheelOpenPositionItem } from '../../services/api'
import { Badge, type SemColor } from './WheelUi'
import { isReleaseCandidate } from '../../services/wheelProduct'
import PathsCompare from './PathsCompare'

function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export type MustManageRow = {
  id?: string
  kind?: string
  tags?: string[]
  categories?: string[]
  action_code?: string | null
  action_hint?: string | null
  action_priority?: number
  headline?: string
  symbol?: string
  side?: string
  strike?: number | null
  dte?: number | null
  profit_pct?: number | null
  actionable?: boolean
  check?: WheelOpenPositionItem
}

type Props = {
  mustManage: WheelOpenPositionItem[]
  fallbackRows: MustManageRow[]
  manageCount: number
  onOpenItem: (item: WheelOpenPositionItem) => void
  onOpenRow: (row: MustManageRow) => void
  onCopyMemo: (item: WheelOpenPositionItem) => void
  onShowMore?: () => void
}

export default function MustManageP0({
  mustManage, fallbackRows, manageCount,
  onOpenItem, onOpenRow, onCopyMemo, onShowMore,
}: Props) {
  const fromBoard = mustManage || []
  const fromRows = [...fallbackRows].sort(
    (a, b) => (a.action_priority ?? 9) - (b.action_priority ?? 9),
  )
  const useBoard = fromBoard.length > 0
  const list = useBoard ? fromBoard.slice(0, 3) : fromRows.slice(0, 3)

  return (
    <>
      <div className="panel-title" style={{ marginBottom: 8 }}>必须处理</div>
      <div className="home-todo-label" style={{ marginTop: 0 }}>持仓浮盈对账 · action_code 仅参考</div>
      {!list.length && (
        <div className="home-todo-empty">暂无紧急项 — 持仓健康</div>
      )}
      {list.map((raw, idx) => {
        const item = useBoard ? (raw as WheelOpenPositionItem) : (raw as MustManageRow).check
        const row = useBoard ? null : (raw as MustManageRow)
        const books = (item as WheelOpenPositionItem & { books?: any })?.books
        const paths = (item as WheelOpenPositionItem)?.paths
        const rec = paths?.recommend
        const code = (item?.action_code || row?.action_code || '').toUpperCase()
        const isHoldish = code === 'HOLD_THETA' || code === 'NONE'
        const badgeText = books ? '浮盈对账' : (row?.tags?.[0] || (isHoldish ? '参考' : '该管'))
        const badgeColor = (books || isHoldish ? 'orange' : (row?.categories?.includes('CLOSE') ? 'green' : 'orange')) as SemColor
        const key = item?.cycle_id || row?.id || String(idx)
        return (
          <div key={key} className="opp-row" style={{ margin: '0 0 6px' }}>
            <div className="opp-row-main">
              <div className="opp-row-title">
                <Badge color={badgeColor}>{badgeText}</Badge>
                {isReleaseCandidate(item?.profit_pct ?? row?.profit_pct) && !books && paths?.close?.fillable !== false && (
                  <Badge color="green" title="浮盈≥50%可腾仓">可腾</Badge>
                )}
                {item?.symbol || row?.symbol} {item?.side || row?.side}
                {(item?.strike ?? row?.strike) != null && (
                  <span style={{ opacity: 0.7 }}>${item?.strike ?? row?.strike}</span>
                )}
                {code && (
                  <span style={{ fontSize: 12, fontWeight: 500, opacity: 0.7 }}>参考 {code}</span>
                )}
              </div>
              {paths ? (
                <PathsCompare paths={paths} compact />
              ) : books ? (
                <div className="books-grid" style={{ marginTop: 6, marginBottom: 0 }}>
                  <div className="books-col">
                    <div className="books-col-title">卖方账</div>
                    <div>已收 {books.seller?.premium_captured_pct ?? '--'}%</div>
                    <div>剩余 ${fmt(books.seller?.remaining_premium_usd, 0)} · 年化 {books.seller?.remaining_ann ?? '--'}%</div>
                    <div>占用 ${fmt(books.seller?.capital_tied, 0)} · 平仓释放 ${fmt(books.seller?.freed_if_close, 0)}</div>
                  </div>
                  <div className="books-col">
                    <div className="books-col-title">股东判断</div>
                    <div>{books.owner?.stance === 'income' ? '只收租' : '允许接货'}</div>
                    <div>{books.owner?.assign_means}</div>
                    <div>{books.owner?.holding_is_price_bet ? '续拿=股价方向赌注' : '未把续拿当方向赌注'}</div>
                  </div>
                </div>
              ) : (
                <div className="opp-row-meta">
                  <span>{item?.action_hint || row?.action_hint || row?.headline}</span>
                  {(item?.dte ?? row?.dte) != null && <span>DTE {item?.dte ?? row?.dte}</span>}
                  {(item?.profit_pct ?? row?.profit_pct) != null && (
                    <span>浮盈 {item?.profit_pct ?? row?.profit_pct}%</span>
                  )}
                </div>
              )}
            </div>
            <div className="opp-row-actions">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => {
                  if (item) onOpenItem(item)
                  else if (row) onOpenRow(row)
                }}
              >
                {(code === 'ROLL' || code === 'ROLL_ADJUST' || rec === 'roll')
                  ? '看 Roll'
                  : rec === 'assign' ? '接货' : '处理'}
              </button>
              {item && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => onCopyMemo(item)}
                >
                  备忘
                </button>
              )}
            </div>
          </div>
        )
      })}
      {manageCount > 3 && onShowMore && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onShowMore}>
          全部 {manageCount} 项 →
        </button>
      )}
    </>
  )
}
