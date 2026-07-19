/** 持仓管理决策弹窗 — 分层主建议 / 详情 / 三选一 */
import type { WheelOpenPositionItem, WheelOpportunitiesResult } from '../../services/api'

function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export type ManageModalPickFn = (
  opps: WheelOpportunitiesResult | null | undefined,
  mc: Pick<WheelOpenPositionItem, 'symbol' | 'side' | 'freed_capital_est' | 'portfolio_put_blocked'>,
  limit?: number,
) => {
  opp: {
    id?: string
    symbol: string
    side?: string
    strike?: number | null
    annualized?: number | null
    contract_code?: string | null
  }
  putBlocked: boolean
}[]

type Props = {
  mc: WheelOpenPositionItem
  serverOpps: WheelOpportunitiesResult | null
  portfolioPutBlocked?: boolean
  rollLoading?: boolean
  executeLoading?: boolean
  onDismiss: () => void
  onExpire: () => void
  onBuyback: () => void
  onRoll: () => void
  onGoOpps: () => void
  /** 一键记账主建议(跳过填表) */
  onQuickExecute?: () => void
  /** 复制买回/执行备忘到剪贴板 */
  onCopyMemo?: () => void
  pickReplaceCandidates: ManageModalPickFn
}

export default function ManageDecisionModal({
  mc,
  serverOpps,
  portfolioPutBlocked,
  rollLoading,
  executeLoading,
  onDismiss,
  onExpire,
  onBuyback,
  onRoll,
  onGoOpps,
  onQuickExecute,
  onCopyMemo,
  pickReplaceCandidates,
}: Props) {
  const releaseReady = mc.profit_pct != null && mc.profit_pct >= 50
  const isCall = mc.side === 'CALL'
  const code = (mc.action_code || '').toUpperCase()
  const underwater = mc.profit_pct != null && mc.profit_pct < 0
  const prefer: 'expire' | 'close' | 'roll' =
    code === 'HOLD_THETA' || code === 'NONE' ? 'expire'
      : (code === 'CLOSE' || code === 'REPLACE') ? 'close'
        : (code === 'ROLL' || code === 'ROLL_ADJUST' || code === 'PREPARE_ASSIGN') ? 'roll'
          : (mc.profit_hit ? 'close' : 'expire')
  const buy = fmt(mc.buyback_ask || mc.current_price)
  const expireBody = isCall
    ? (mc.itm
      ? '到期若仍 ITM:正股可能被 call 走。仅当你愿意按 strike 交货时再放任。'
      : underwater
        ? '仍 OTM:到期作废可收回浮亏。确认愿按 strike 交货;否则买回或 Roll。'
        : 'OTM 到期作废,留下持股吃光剩余权利金。临期且买回摩擦大时往往优于硬止盈。')
    : (mc.itm
      ? '到期若仍 ITM:可能被指派接货。确认愿接货且有资金,再放任;否则 Roll/平仓。'
      : underwater
        ? '仍 OTM:到期作废可收回浮亏。确认愿按 strike 接货;否则止损买回或 Roll。'
        : 'OTM 到期作废,现金担保释放。临期 OTM 可放任吃 θ。')
  const closeBody = isCall
    ? (underwater
      ? `买回约 $${buy}/股,确认亏损约 ${mc.profit_pct}%。结束 Call 义务、保留持股(不释放大额现金)。`
      : `买回约 $${buy}/股,落袋浮盈 ${mc.profit_pct ?? '--'}%。结束 Call 义务、拿回上行空间;持股占用仍在,不是腾担保金。`)
    : (underwater
      ? `买回约 $${buy}/股,确认亏损约 ${mc.profit_pct}%。释放 CSP 担保;适合不愿在 ${mc.strike} 接货或要腾资金。`
      : `买回约 $${buy}/股,落袋浮盈 ${mc.profit_pct ?? '--'}%。释放 CSP 现金担保,便于再开 Put 或换标的。`)
  const rollBody = isCall
    ? (underwater
      ? '买回 + 卖更远/更高 strike:用时间换空间,仍想持股收租时的防守。'
      : '买回 + 卖更远到期(可 roll up)。仍想持股收租、但 DTE/ITM 不舒服时用。')
    : (underwater
      ? '买回 + 卖更远/更低 strike:经典 CSP 防守;确认仍愿接货再 roll。'
      : '买回 + 卖更远到期(可 roll down)。临期/ITM 风险升、尚未想接货时用。')
  const preferLabel = prefer === 'expire' ? '放任到期' : prefer === 'close' ? '买回平仓' : 'Roll 展期'
  const primaryWhy = (mc.reasons && mc.reasons[0])
    || (isCall && prefer === 'close' && !underwater
      ? '权利金目标已达成;Call 买回结束义务,不显著降低组合占用。'
      : !isCall && prefer === 'close' && !underwater
        ? '权利金目标已达成,买回可释放担保金周转。'
        : (mc.action_hint || '按规则建议操作'))
  const conf = mc.decision_confidence
  const doPrimary = () => {
    // 一键执行(非 Roll 需候选时走原路径)
    if (onQuickExecute && prefer !== 'roll') {
      onQuickExecute()
      return
    }
    if (prefer === 'expire') onExpire()
    else if (prefer === 'close') onBuyback()
    else onRoll()
  }
  const nextLegs = (code === 'CLOSE' || code === 'REPLACE')
    ? pickReplaceCandidates(serverOpps, mc, 2) : []
  const putBlk = !!(mc.portfolio_put_blocked || serverOpps?.summary?.portfolio_put_blocked || portfolioPutBlocked)

  return (
    <div className="manage-modal-overlay" onClick={onDismiss}>
      <div className="manage-modal card" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 16 }}>
              {mc.symbol} {isCall ? 'Covered Call' : 'Cash-Secured Put'} ${mc.strike}
            </h3>
            <div className="manage-modal-meta" style={{ marginTop: 8, marginBottom: 0 }}>
              <span className="manage-chip">{mc.itm ? 'ITM' : 'OTM'}</span>
              <span className="manage-chip">DTE <strong>{mc.dte ?? '--'}</strong></span>
              <span className="manage-chip">浮盈 <strong>{mc.profit_pct ?? '--'}%</strong></span>
              <span className="manage-chip">买回 <strong>${buy}</strong></span>
              {mc.remaining_annualized != null && (
                <span className="manage-chip">剩余年化 <strong>{mc.remaining_annualized}%</strong></span>
              )}
              {mc.capital_tight && (
                <span className="manage-chip warn">
                  资金紧{mc.capital_util_pct != null ? ` ${Math.round(mc.capital_util_pct)}%` : ''}
                  {isCall ? ' · 对 CC 不释放担保' : ''}
                </span>
              )}
              {mc.strike_above_floor && <span className="manage-chip warn">超过愿接价</span>}
              {releaseReady && (
                <span className="manage-chip" style={{ background: 'var(--green-dim)', color: 'var(--green)', fontWeight: 700 }}
                  title="浮盈≥50%,组合年化视角可落袋腾担保">
                  可腾
                </span>
              )}
            </div>
          </div>
          <button type="button" className="btn btn-sm" onClick={onDismiss}>关闭</button>
        </div>

        <div className={`manage-primary ${prefer !== 'expire' || underwater ? 'warn' : ''}`}>
          <div className="manage-primary-title">
            推荐 · {preferLabel}
            {conf != null && (
              <span style={{ fontSize: 11, fontWeight: 600, marginLeft: 8, opacity: 0.85 }}
                title="规则把握度,非胜率预测">
                把握 {conf}%
              </span>
            )}
          </div>
          <div className="manage-primary-why">
            <b>{mc.action_hint || preferLabel}</b>
            <div style={{ marginTop: 4 }}>{primaryWhy}</div>
            {mc.secondary_hint && (
              <div style={{ marginTop: 4, opacity: 0.9 }}>备选: {mc.secondary_hint}</div>
            )}
          </div>
          <button type="button" className="btn btn-primary" style={{ width: '100%' }}
            disabled={(prefer === 'roll' && rollLoading) || !!executeLoading}
            onClick={doPrimary}>
            {executeLoading ? '执行中…'
              : prefer === 'expire' ? (onQuickExecute ? '一键登记到期' : '登记到期')
                : prefer === 'close' ? (onQuickExecute ? '一键买回记账' : '登记买回')
                  : '打开 Roll 对比'}
          </button>
          {prefer === 'close' && (
            <button type="button" className="btn btn-ghost btn-sm" style={{ width: '100%', marginTop: 6 }}
              onClick={onBuyback}>改价格再登记…</button>
          )}
          {onCopyMemo && (prefer === 'close' || prefer === 'roll' || releaseReady) && (
            <button type="button" className="btn btn-secondary btn-sm" style={{ width: '100%', marginTop: 6 }}
              onClick={onCopyMemo}
              title="复制合约/方向/限价到剪贴板,去富途下单">
              复制执行备忘
            </button>
          )}
        </div>

        <details className="manage-details">
          <summary>详情与纪律</summary>
          <div className="manage-details-body">
            <div style={{ marginBottom: 6 }}>合约 {mc.contract_code || '—'}</div>
            {mc.would_open_today && (
              <div style={{ marginBottom: 8 }}>
                <b>今天还会开吗 · </b>
                {mc.would_open_today === 'yes' ? '规则仍会开'
                  : mc.would_open_today === 'no' ? '规则已否决'
                    : mc.would_open_today === 'caution' ? '谨慎' : '数据不足'}
                <div style={{ marginTop: 2 }}>
                  {(mc.would_open_reasons && mc.would_open_reasons[0])
                    || '对照开仓纪律的反事实检验'}
                </div>
              </div>
            )}
            {(mc.reasons || []).slice(0, 4).map((r, i) => (
              <div key={i} style={{ marginBottom: 2 }}>· {r}</div>
            ))}
            {mc.assign_checklist && (code === 'PREPARE_ASSIGN' || code === 'ROLL_ADJUST' || mc.itm) && (() => {
              const cl = mc.assign_checklist!
              const isPut = (cl.side || mc.side) === 'PUT'
              return (
                <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
                  <b>{isPut ? '接货清单' : '交货清单'}</b>
                  <div>① 愿按 strike ${mc.strike} {isPut ? '接货' : '交货'}吗?</div>
                  <div>② {isPut
                    ? (cl.floor_ok === false ? '愿接价未通过' : cl.floor_ok === true ? '在愿接价内' : '愿接价未设')
                    : 'Call 看成本底线,与 floor 无关'}</div>
                  <div>③ 集中度/下一步可接受吗?
                    {cl.post_holding_pct != null ? ` (约净值 ${cl.post_holding_pct}%)` : ''}
                  </div>
                  <div style={{ marginTop: 4 }}>
                    {isPut ? '接货' : '交货'}名义 ${fmt(cl.assign_notional, 0)}
                    {cl.collateral_covers && isPut ? ' · 担保通常已覆盖' : ''}
                  </div>
                  {cl.next_step_hint && <div style={{ marginTop: 2 }}>{cl.next_step_hint}</div>}
                </div>
              )
            })()}
            {(code === 'CLOSE' || code === 'REPLACE') && (
              <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
                <b>平仓后 · 下一腿</b>
                {isCall && (
                  <div style={{ marginTop: 2 }}>CC 买回主要结束义务,不显著释放组合现金占用。</div>
                )}
                {mc.replace_hint && <div style={{ marginTop: 2 }}>{mc.replace_hint}</div>}
                {mc.freed_capital_est != null && mc.freed_capital_est > 0 && (
                  <div style={{ marginTop: 2 }}>约释放担保 <b>${fmt(mc.freed_capital_est, 0)}</b></div>
                )}
                {nextLegs.length === 0 ? (
                  <div style={{ marginTop: 4 }}>
                    {putBlk ? '组合已停新 Put。' : '暂无缓存机会,可先扫描。'}
                  </div>
                ) : nextLegs.map(({ opp, putBlocked: blocked }) => (
                  <div key={opp.id || `${opp.symbol}-${opp.strike}`} style={{ marginTop: 4, opacity: blocked ? 0.55 : 1 }}>
                    {opp.symbol} {opp.side}
                    {opp.strike != null && ` $${opp.strike}`}
                    {opp.annualized != null && ` · 年化 ${fmt(opp.annualized, 0)}%`}
                    {blocked && ' · 停 Put'}
                  </div>
                ))}
                <button type="button" className="btn btn-sm" style={{ marginTop: 8 }} onClick={onGoOpps}>
                  去看机会
                </button>
              </div>
            )}
          </div>
        </details>

        <details className="manage-details" open>
          <summary>全部方案(三选一)</summary>
          <div className="manage-details-body">
            <div className="manage-alt-grid">
              <div className={`manage-alt-card ${prefer === 'expire' ? 'preferred' : ''}`}>
                <h4>① 放任到期{prefer === 'expire' ? ' · 推荐' : ''}</h4>
                <p>{expireBody}</p>
                <button type="button" className={`btn ${prefer === 'expire' ? 'btn-primary' : ''} btn-sm`}
                  style={{ width: '100%' }} onClick={onExpire}>登记到期</button>
              </div>
              <div className={`manage-alt-card ${prefer === 'close' ? 'preferred' : ''}`}>
                <h4 style={{ color: prefer === 'close' ? undefined : undefined }}>
                  ② 买回平仓{prefer === 'close' ? ' · 推荐' : ''}
                </h4>
                <p>
                  {closeBody}
                  {mc.remaining_annualized != null && mc.remaining_annualized < 15 && ' 剩余年化偏低。'}
                </p>
                <button type="button" className={`btn ${prefer === 'close' ? 'btn-primary' : ''} btn-sm`}
                  style={{ width: '100%' }} onClick={onBuyback}>登记买回</button>
              </div>
              <div className={`manage-alt-card ${prefer === 'roll' ? 'preferred roll' : ''}`}>
                <h4>③ Roll 展期{prefer === 'roll' ? ' · 推荐' : ''}</h4>
                <p>
                  {rollBody}
                  {isCall && mc.itm && ' ITM 可考虑 roll up/out。'}
                  {!isCall && mc.itm && ' ITM 可考虑 roll down/out。'}
                </p>
                <button type="button" className={`btn ${prefer === 'roll' ? 'btn-primary' : ''} btn-sm`}
                  style={{ width: '100%' }} disabled={rollLoading} onClick={onRoll}>打开 Roll 对比</button>
              </div>
            </div>
          </div>
        </details>
      </div>
    </div>
  )
}
