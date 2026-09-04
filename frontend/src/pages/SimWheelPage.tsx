/**
 * 完整轮子纸面账 — 薄 UI。明确标注「纸面/非实盘」,不登记实盘台账。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  getSimCycles,
  getSimStats,
  postSimTick,
  type SimCycle,
  type SimStatsRow,
} from '../services/api'

const STATUS_ZH: Record<string, string> = {
  IDLE: '空仓',
  CSP_OPEN: '卖Put中',
  HOLDING: '持股',
  CC_OPEN: '卖Call中',
  CLOSED: '已结束',
}

const BADGE: Record<string, { bg: string; color: string }> = {
  Cold: { bg: '#64748b33', color: '#94a3b8' },
  Warm: { bg: '#f59e0b33', color: '#fbbf24' },
  Hot: { bg: '#ef444433', color: '#f87171' },
}

export default function SimWheelPage() {
  const [cycles, setCycles] = useState<SimCycle[]>([])
  const [stats, setStats] = useState<SimStatsRow[]>([])
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setErr('')
    try {
      const [c, s] = await Promise.all([
        getSimCycles(statusFilter || undefined),
        getSimStats(),
      ])
      setCycles(c.items || [])
      setStats(s.items || [])
    } catch (e: any) {
      setErr(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    load()
  }, [load])

  async function onTick() {
    setMsg('')
    try {
      const spots: Record<string, number> = {}
      for (const c of cycles) {
        if (c.status !== 'CLOSED' && c.symbol && spots[c.symbol] == null) {
          // 无行情时用 strike 占位;真实部署由后端/监控喂价
          spots[c.symbol] = Number(c.open_strike || c.cost_basis || c.share_cost || 0)
        }
      }
      const out = await postSimTick({ spots })
      setMsg(`tick 完成 · 动作 ${out.actions?.length ?? 0} 条`)
      await load()
    } catch (e: any) {
      setErr(e?.message || String(e))
    }
  }

  return (
    <div className="page" style={{ padding: 16, maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>SIM 纸面轮子</h2>
        <span
          style={{
            fontSize: 12,
            padding: '2px 8px',
            borderRadius: 999,
            background: '#0ea5e933',
            color: '#38bdf8',
            border: '1px solid #38bdf855',
          }}
        >
          纸面 / 非实盘
        </span>
        <button type="button" className="btn" onClick={load} disabled={loading}>
          刷新
        </button>
        <button type="button" className="btn" onClick={onTick} disabled={loading}>
          推进 tick
        </button>
      </div>
      <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 0 }}>
        独立纸面账:Put/Call 触线与缠论信号同指纹入账。不碰 FirstTrade、不自动实盘下单。
      </p>
      {err && <div style={{ color: '#f87171', marginBottom: 8 }}>{err}</div>}
      {msg && <div style={{ color: '#34d399', marginBottom: 8 }}>{msg}</div>}

      <h3 style={{ marginTop: 20 }}>熟悉度</h3>
      {stats.length === 0 ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>暂无闭环样本</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 20 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-secondary)' }}>
              <th style={{ padding: 6 }}>策略</th>
              <th>标的</th>
              <th>熟悉度</th>
              <th>闭环</th>
              <th>胜率</th>
              <th>期望</th>
              <th>接货率</th>
              <th>被Call率</th>
            </tr>
          </thead>
          <tbody>
            {stats.map(s => {
              const b = BADGE[s.familiarity] || BADGE.Cold
              return (
                <tr key={`${s.strategy}-${s.symbol}`} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: 6 }}>{s.strategy}</td>
                  <td>{s.symbol}</td>
                  <td>
                    <span style={{ background: b.bg, color: b.color, padding: '1px 8px', borderRadius: 999, fontSize: 12 }}>
                      {s.familiarity}
                    </span>
                  </td>
                  <td>{s.closed_cycles}</td>
                  <td>{((s.win_rate || 0) * 100).toFixed(0)}%</td>
                  <td>{(s.expectancy || 0).toFixed(0)}</td>
                  <td>{((s.assign_rate || 0) * 100).toFixed(0)}%</td>
                  <td>{((s.called_away_rate || 0) * 100).toFixed(0)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>纸面周期</h3>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ fontSize: 13 }}>
          <option value="">全部</option>
          {['CSP_OPEN', 'HOLDING', 'CC_OPEN', 'IDLE', 'CLOSED'].map(s => (
            <option key={s} value={s}>{STATUS_ZH[s] || s}</option>
          ))}
        </select>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--text-secondary)' }}>
            <th style={{ padding: 6 }}>标的</th>
            <th>策略</th>
            <th>状态</th>
            <th>级别</th>
            <th>权利金</th>
            <th>成本基础</th>
            <th>实现盈亏</th>
            <th>开始</th>
          </tr>
        </thead>
        <tbody>
          {cycles.map(c => (
            <tr key={c.id} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: 6 }}>{c.symbol}</td>
              <td>{c.strategy}</td>
              <td>{STATUS_ZH[c.status] || c.status}</td>
              <td>{c.level || '—'}</td>
              <td>{c.total_premium != null ? `$${Number(c.total_premium).toFixed(0)}` : '—'}</td>
              <td>{c.cost_basis != null ? `$${Number(c.cost_basis).toFixed(2)}` : '—'}</td>
              <td>{c.realized_pnl != null ? `$${Number(c.realized_pnl).toFixed(0)}` : '—'}</td>
              <td style={{ fontSize: 12 }}>{(c.started_at || '').slice(0, 16)}</td>
            </tr>
          ))}
          {cycles.length === 0 && (
            <tr>
              <td colSpan={8} style={{ padding: 12, color: 'var(--text-secondary)' }}>
                暂无纸面持仓 — 触线/缠论推送后自动入账
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
