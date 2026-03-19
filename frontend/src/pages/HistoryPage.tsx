import { useEffect, useState } from 'react'
import {
  AppSettings,
  addHistorySubscription,
  backfillHistory,
  getAppSettings,
  getHistoryCoverage,
  getHistoryJobs,
  getHistorySchedulerStatus,
  getHistorySubscriptions,
  previewHistorySource,
  runHistoryScheduler,
  setHistorySubscriptionEnabled,
  subscribeSettings,
} from '../services/api'

const PRESETS = ['AAPL', 'QQQ', 'TSLA', 'GOOGL', 'NVDA', 'AMD', '600519.SH', '300750.SZ', '00700.HK', '00883.HK']

export default function HistoryPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')
  const [startDate, setStartDate] = useState('2026-02-01T00:00:00')
  const [endDate, setEndDate] = useState('2026-03-19T00:00:00')
  const [coverage, setCoverage] = useState<any | null>(null)
  const [jobs, setJobs] = useState<any[]>([])
  const [subscriptions, setSubscriptions] = useState<any[]>([])
  const [schedulerStatus, setSchedulerStatus] = useState<any | null>(null)
  const [preview, setPreview] = useState<any | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const unsubscribe = subscribeSettings(next => setSettings(next))
    return unsubscribe
  }, [])

  useEffect(() => {
    void refreshAll(false)
  }, [])

  useEffect(() => {
    void refreshAll(true)
  }, [symbol, timeframe, settings.marketDataSource])

  async function refreshAll(silent = true) {
    if (!silent) {
      setLoading(true)
      setError('')
      setMessage('')
    }
    try {
      const [coverageData, jobsData, previewData, subsData, schedulerData] = await Promise.all([
        getHistoryCoverage(symbol, timeframe),
        getHistoryJobs(30),
        previewHistorySource(symbol, settings.marketDataSource),
        getHistorySubscriptions(false),
        getHistorySchedulerStatus(),
      ])
      setCoverage(coverageData)
      setJobs(jobsData)
      setPreview(previewData)
      setSubscriptions(subsData)
      setSchedulerStatus(schedulerData)
    } catch (e: any) {
      setError(e.message || '历史数据加载失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  async function handleBackfill() {
    setLoading(true)
    setError('')
    setMessage('')
    try {
      const result = await backfillHistory({
        symbol,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        host: settings.marketHost,
        port: settings.marketPort,
        source: preview?.source,
      })
      setMessage(`补数完成：${result.symbol} / ${result.timeframe} / ${result.source}，写入 ${result.written} 条`)
      await refreshAll(true)
    } catch (e: any) {
      setError(e.message || '补数失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSubscribe(targetSymbol: string) {
    setLoading(true)
    setError('')
    setMessage('')
    try {
      const source = (await previewHistorySource(targetSymbol, settings.marketDataSource)).source
      await addHistorySubscription({
        symbol: targetSymbol,
        name: targetSymbol,
        source_hint: source,
        enabled: true,
      })
      setMessage(`已加入订阅：${targetSymbol}（默认每天 08:00 更新 1d / 1h / 30m / 5m / 1m）`)
      await refreshAll(true)
    } catch (e: any) {
      setError(e.message || '加入订阅失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleToggleSubscription(targetSymbol: string, enabled: boolean) {
    setLoading(true)
    setError('')
    setMessage('')
    try {
      await setHistorySubscriptionEnabled(targetSymbol, enabled)
      setMessage(`${targetSymbol} 已${enabled ? '启用' : '停用'}定时更新`)
      await refreshAll(true)
    } catch (e: any) {
      setError(e.message || '更新订阅状态失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleRunScheduler() {
    setLoading(true)
    setError('')
    setMessage('')
    try {
      const result = await runHistoryScheduler(settings)
      setMessage(`定时任务已执行：${result.status}`)
      await refreshAll(true)
    } catch (e: any) {
      setError(e.message || '执行定时任务失败')
    } finally {
      setLoading(false)
    }
  }

  function applyPreset(nextSymbol: string) {
    setSymbol(nextSymbol)
    setMessage('')
    setError('')
  }

  const subscribedSet = new Set(subscriptions.filter(item => item.enabled).map(item => item.symbol))

  return (
    <div className="page active">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 12, flexWrap: 'wrap' }}>
        <h2>历史数据</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {PRESETS.map(item => (
            <button key={item} className="btn-outline" onClick={() => applyPreset(item)}>{item}</button>
          ))}
        </div>
      </div>

      {message && (
        <div className="card" style={{ border: '1px solid rgba(76,201,240,0.35)', color: '#cde9f5' }}>
          {message}
        </div>
      )}
      {error && (
        <div className="card" style={{ border: '1px solid rgba(233,69,96,0.35)', color: '#ffb0ba' }}>
          {error}
        </div>
      )}

      <div className="history-layout">
        <div className="card">
          <h3 style={{ marginBottom: 12 }}>补数控制台</h3>
          <div className="history-form-grid">
            <div className="option-field">
              <label>标的代码</label>
              <input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} />
            </div>
            <div className="option-field">
              <label>周期</label>
              <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
                <option value="1d">1d</option>
                <option value="1h">1h</option>
                <option value="30m">30m</option>
                <option value="5m">5m</option>
                <option value="1m">1m</option>
              </select>
            </div>
            <div className="option-field">
              <label>开始时间</label>
              <input value={startDate} onChange={e => setStartDate(e.target.value)} />
            </div>
            <div className="option-field">
              <label>结束时间</label>
              <input value={endDate} onChange={e => setEndDate(e.target.value)} />
            </div>
          </div>

          <div className="status-line" style={{ marginTop: 16 }}>
            <span className="tag ready">主行情源：{settings.marketDataSource}</span>
            <span className="tag positive">补数源预览：{preview?.source || '--'}</span>
            <span className="tag draft">Futu Host：{settings.marketHost}:{settings.marketPort}</span>
          </div>

          <div style={{ display: 'flex', gap: 12, marginTop: 16, flexWrap: 'wrap' }}>
            <button className="btn" onClick={handleBackfill} disabled={loading}>
              {loading ? '处理中...' : '执行补数'}
            </button>
            <button className="btn-outline" onClick={() => handleSubscribe(symbol)} disabled={loading}>
              加入订阅
            </button>
            <button className="btn-outline" onClick={handleRunScheduler} disabled={loading}>
              手动跑定时任务
            </button>
            <button className="btn-outline" onClick={() => refreshAll(false)} disabled={loading}>
              刷新状态
            </button>
          </div>
        </div>

        <div className="card">
          <h3 style={{ marginBottom: 12 }}>调度状态</h3>
          <div className="metrics-grid history-metrics-grid">
            <div className="metric-card">
              <div className="value">{schedulerStatus?.subscriptions?.length ?? subscriptions.length}</div>
              <div className="label">启用订阅数</div>
            </div>
            <div className="metric-card">
              <div className="value">08:00</div>
              <div className="label">每日执行时间</div>
            </div>
            <div className="metric-card">
              <div className="value" style={{ fontSize: 15 }}>{schedulerStatus?.last_started_at || '--'}</div>
              <div className="label">最近启动</div>
            </div>
            <div className="metric-card">
              <div className="value" style={{ fontSize: 15 }}>{schedulerStatus?.next_run_at || '--'}</div>
              <div className="label">下次执行</div>
            </div>
          </div>
          <div style={{ marginTop: 14, color: '#9fb2d0', fontSize: 13 }}>
            固定更新周期：<strong>1d / 1h / 30m / 5m / 1m</strong>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>覆盖情况</h3>
        <div className="metrics-grid history-metrics-grid">
          <div className="metric-card">
            <div className="value">{coverage?.bar_count ?? '--'}</div>
            <div className="label">本地K线条数</div>
          </div>
          <div className="metric-card">
            <div className="value" style={{ fontSize: 16 }}>{coverage?.earliest_ts || '--'}</div>
            <div className="label">最早时间</div>
          </div>
          <div className="metric-card">
            <div className="value" style={{ fontSize: 16 }}>{coverage?.latest_ts || '--'}</div>
            <div className="label">最新时间</div>
          </div>
          <div className="metric-card">
            <div className="value">{coverage?.status || 'idle'}</div>
            <div className="label">同步状态</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>订阅列表</h3>
        {subscriptions.length === 0 ? (
          <div style={{ color: '#888' }}>暂无订阅。手动补数或点击“加入订阅”后会进入每天 08:00 更新列表。</div>
        ) : (
          <table className="trade-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>市场</th>
                <th>来源偏好</th>
                <th>状态</th>
                <th>最近调度</th>
                <th>最近结果</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {subscriptions.map(item => (
                <tr key={item.symbol}>
                  <td>{item.symbol}</td>
                  <td>{item.market}</td>
                  <td>{item.source_hint || '--'}</td>
                  <td>{item.enabled ? '启用' : '停用'}</td>
                  <td>{item.last_scheduled_sync_at || '--'}</td>
                  <td>{item.last_scheduled_status || '--'}</td>
                  <td>
                    <button className="btn-outline" onClick={() => handleToggleSubscription(item.symbol, !item.enabled)}>
                      {item.enabled ? '停用' : '启用'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>补数任务</h3>
        {jobs.length === 0 ? (
          <div style={{ color: '#888' }}>暂无补数任务</div>
        ) : (
          <table className="trade-table">
            <thead>
              <tr>
                <th>任务ID</th>
                <th>标的</th>
                <th>周期</th>
                <th>来源</th>
                <th>区间</th>
                <th>状态</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => (
                <tr key={job.id}>
                  <td>{String(job.id).slice(0, 8)}</td>
                  <td>{job.symbol}</td>
                  <td>{job.timeframe}</td>
                  <td>{job.source}</td>
                  <td>{String(job.start_ts).slice(0, 10)} ~ {String(job.end_ts).slice(0, 10)}</td>
                  <td>{job.status}</td>
                  <td>{job.updated_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>快速订阅</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {PRESETS.map(item => (
            <button
              key={item}
              className={subscribedSet.has(item) ? 'btn' : 'btn-outline'}
              onClick={() => handleSubscribe(item)}
              disabled={subscribedSet.has(item) || loading}
            >
              {subscribedSet.has(item) ? `${item} 已订阅` : `订阅 ${item}`}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
