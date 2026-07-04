import type { AppSettings, StrategySignal, StrategySummary } from '../services/api'

interface Props {
  settings: AppSettings
  refreshOptions: { label: string; value: number }[]
  lastRefreshAt: string
  tradingConnected: boolean
  quoteSource: string
  klineSource: string
  strategies: StrategySummary[]
  selectedStrategyId: string
  signalInfo: StrategySignal | null
  onChangeRefreshInterval: (value: number) => void
  onChangeStrategy: (id: string) => void
}

export default function MarketStatusBar(props: Props) {
  const {
    settings,
    refreshOptions,
    lastRefreshAt,
    tradingConnected,
    quoteSource,
    klineSource,
    strategies,
    selectedStrategyId,
    signalInfo,
    onChangeRefreshInterval,
    onChangeStrategy,
  } = props

  const adapterLabel = 'Futu'
  const envLabel = settings.tradingEnv === 'REAL' ? '实盘' : '模拟盘'
  const routeModeLabel = '自动路由'

  return (
    <>
      <div className="card compact-card">
        <div className="status-line">
          <span className="tag ready">路由模式：{routeModeLabel}</span>
          <span className="tag ready">报价实际来源：{quoteSource}</span>
          <span className="tag ready">K线实际来源：{klineSource}</span>
          <span className={`tag ${tradingConnected ? 'ready' : 'draft'}`}>
            交易：{tradingConnected ? `${adapterLabel} / ${envLabel}` : '未连接'}
          </span>
          <span className="tag ready refresh-tag">
            <span>实时刷新</span>
            <select
              className="tag-select"
              value={String(settings.refreshIntervalSec || 0)}
              onChange={e => onChangeRefreshInterval(Number(e.target.value))}
            >
              {refreshOptions.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </span>
          {lastRefreshAt && <span className="tag draft">最近刷新：{lastRefreshAt}</span>}
        </div>
      </div>

      <div className="card compact-card">
        <div className="settings-row" style={{ borderBottom: 'none', padding: 0 }}>
          <label>信号策略</label>
          <select value={selectedStrategyId} onChange={e => onChangeStrategy(e.target.value)} style={{ minWidth: 260 }}>
            <option value="">不启用自动信号</option>
            {strategies.map(strategy => (
              <option key={strategy.id} value={strategy.id}>
                {strategy.name} · {strategy.timeframe || '1d'}
              </option>
            ))}
          </select>
        </div>
        {signalInfo && (
          <div style={{ marginTop: 12, color: 'var(--text-secondary)', fontSize: 13 }}>
            当前策略：<strong>{signalInfo.strategy_name}</strong> | 最新信号：<strong>{signalInfo.signal}</strong>
          </div>
        )}
      </div>
    </>
  )
}
