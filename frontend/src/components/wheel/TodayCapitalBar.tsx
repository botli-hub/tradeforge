/** 今日页资金条:活权益(现金+持股+期权盯市) / 闲钱 / 利用率,与必须处理、待挂 CC、优先开仓分开。 */
import { fmt, fmtMoney } from './WheelUi'

type Capital = {
  utilization_pct?: number | null
  idle_cash?: number | null
  equity?: number | null
  cash?: number | null
  stock_mv?: number | null
  option_mtm?: number | null
  starting_cash?: number | null
  capital_tight?: boolean
  portfolio_put_blocked?: boolean
  buying_power?: number | null
}

type EventItem = { date?: string; symbol?: string; label?: string; kind?: string; days?: number }

export default function TodayCapitalBar({
  capital,
  putBlocked,
  stale,
  staleAgeMinutes,
  concentrationWarnings,
  events,
}: {
  capital?: Capital | null
  putBlocked?: boolean
  stale?: boolean
  staleAgeMinutes?: number | null
  concentrationWarnings?: string[]
  events?: EventItem[]
}) {
  const util = capital?.utilization_pct
  const blocked = !!(putBlocked || capital?.portfolio_put_blocked)
  const tight = !!capital?.capital_tight
  const utilTone = blocked ? 'danger' : tight || (util != null && util >= 75) ? 'warn' : 'ok'

  return (
    <div className="panel today-panel">
      <div className="panel-title">资金</div>
      <div className="capital-bar">
        <div className="capital-bar-item ok">
          <div className="k">权益</div>
          <div className="v">{capital?.equity != null ? `$${fmtMoney(capital.equity)}` : '—'}</div>
        </div>
        <div className="capital-bar-item">
          <div className="k">闲钱</div>
          <div className="v">{capital?.idle_cash != null ? `$${fmtMoney(capital.idle_cash)}` : '—'}</div>
        </div>
        <div className={`capital-bar-item ${utilTone}`}>
          <div className="k">利用率</div>
          <div className="v">{util != null ? `${fmt(util, 0)}%` : '—'}</div>
        </div>
        <div className={`capital-bar-item ${blocked ? 'danger' : 'ok'}`}>
          <div className="k">新 Put</div>
          <div className="v">{blocked ? '停' : '开'}</div>
        </div>
      </div>
      {(capital?.cash != null || capital?.stock_mv != null || capital?.option_mtm != null) && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
          现金 ${fmtMoney(capital?.cash ?? 0)}
          {' · '}持股 ${fmtMoney(capital?.stock_mv ?? 0)}
          {' · '}期权 ${fmtMoney(capital?.option_mtm ?? 0)}
          {capital?.buying_power != null ? ` · BP $${fmtMoney(capital.buying_power)}` : ''}
        </div>
      )}
      {blocked && (
        <div className="banner error" style={{ marginTop: 10, marginBottom: 0 }}>
          组合压力高:已暂停新开 Put
          {util != null && ` · 利用率 ${fmt(util, 0)}%`}
        </div>
      )}
      {stale && (
        <div className="banner warn" style={{ marginTop: 10, marginBottom: 0 }}>
          行情缓存(OpenD 弱网) · 决策可看但价格可能旧
          {staleAgeMinutes != null ? ` · ${staleAgeMinutes} 分钟前` : ''}
        </div>
      )}
      {(concentrationWarnings?.length || 0) > 0 && (
        <div className="banner warn" style={{ marginTop: 10, marginBottom: 0, fontSize: 13 }}>
          集中度: {(concentrationWarnings || []).slice(0, 2).join('；')}
        </div>
      )}
      {(events?.length || 0) > 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
          近事件: {(events || []).slice(0, 4).map(e =>
            `${e.symbol} ${e.label}${e.days === 0 ? '今' : `${e.days}d`}`).join(' · ')}
        </div>
      )}
    </div>
  )
}
