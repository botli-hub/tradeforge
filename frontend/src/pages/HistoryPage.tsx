import { useEffect, useState } from 'react'
import {
  AppSettings,
  getAppSettings,
  getHistoryJobs,
  getHistorySchedulerStatus,
  runHistoryScheduler,
  subscribeSettings,
} from '../services/api'

export default function HistoryPage() {
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [jobs, setJobs] = useState<any[]>([])
  const [schedulerStatus, setSchedulerStatus] = useState<any | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const unsubscribe = subscribeSettings(next => setSettings(next))
    return unsubscribe
  }, [])

  useEffect(() => {
    void refresh(false)
  }, [])

  async function refresh(silent = true) {
    if (!silent) {
      setLoading(true)
      setError('')
      setMessage('')
    }
    try {
      const [jobsData, schedulerData] = await Promise.all([
        getHistoryJobs(50),
        getHistorySchedulerStatus(),
      ])
      setJobs(jobsData)
      setSchedulerStatus(schedulerData)
    } catch (e: any) {
      setError(e.message || '加载失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  async function handleRunScheduler() {
    setLoading(true)
    setError('')
    setMessage('')
    try {
      const result = await runHistoryScheduler(settings)
      setMessage(`定时任务已触发：${result.status}`)
      await refresh(true)
    } catch (e: any) {
      setError(e.message || '执行失败')
    } finally {
      setLoading(false)
    }
  }

  const stockCount = schedulerStatus?.subscriptions?.length ?? '--'

  return (
    <div className="page active">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 12, flexWrap: 'wrap' }}>
        <h2>历史数据</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-outline" onClick={() => refresh(false)} disabled={loading}>刷新</button>
          <button className="btn" onClick={handleRunScheduler} disabled={loading}>
            {loading ? '执行中...' : '手动触发补数'}
          </button>
        </div>
      </div>

      {message && (
        <div className="card" style={{ border: '1px solid rgba(76,201,240,0.35)', color: 'var(--text-secondary)', marginBottom: 16 }}>
          {message}
        </div>
      )}
      {error && (
        <div className="card" style={{ border: '1px solid rgba(233,69,96,0.35)', color: '#ffb0ba', marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginBottom: 12 }}>调度状态</h3>
        <div className="metrics-grid history-metrics-grid">
          <div className="metric-card">
            <div className="value">{stockCount}</div>
            <div className="label">股票池股票数</div>
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
        <div style={{ marginTop: 14, color: 'var(--text-secondary)', fontSize: 13 }}>
          固定更新周期：<strong>1d / 1h / 30m / 5m / 1m</strong>
          　·　启动时自动检测缺失数据并补充
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>补数任务</h3>
        {jobs.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)' }}>暂无补数任务</div>
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
    </div>
  )
}
