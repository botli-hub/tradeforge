import { useEffect, useState, useCallback } from 'react'
import {
  getLeapsWatchlist, getLeapsSignals, getLeapsCooldowns, getLeapsStatus,
  triggerLeapsScan, updateLeapsWatchlistItem, resendLeapsSignal,
  LeapsWatchlistItem, LeapsSignal, LeapsCooldown, LeapsStatus,
} from '../services/api'

function Badge({ level }: { level: string }) {
  const isSecondary = level === 'SECONDARY'
  return (
    <span style={{
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 700,
      background: isSecondary ? '#7c3aed22' : '#0ea5e922',
      color: isSecondary ? '#a78bfa' : '#38bdf8',
      border: `1px solid ${isSecondary ? '#7c3aed55' : '#0ea5e955'}`,
    }}>
      {isSecondary ? '二级 EMA200' : '一级 EMA50'}
    </span>
  )
}

function formatDate(iso: string) {
  return iso ? iso.replace('T', ' ').slice(0, 16) : '-'
}

function formatExpiry(expiry: string) {
  if (expiry.length === 6) return `20${expiry.slice(0, 2)}-${expiry.slice(2, 4)}-${expiry.slice(4, 6)}`
  return expiry
}

export default function LeapsMonitorPage() {
  const [tab, setTab] = useState<'overview' | 'watchlist' | 'signals' | 'cooldowns'>('overview')
  const [status, setStatus] = useState<LeapsStatus | null>(null)
  const [watchlist, setWatchlist] = useState<LeapsWatchlistItem[]>([])
  const [signals, setSignals] = useState<LeapsSignal[]>([])
  const [cooldowns, setCooldowns] = useState<LeapsCooldown[]>([])
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingFloor, setEditingFloor] = useState<Record<string, string>>({})

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, wl, sigs, cd] = await Promise.all([
        getLeapsStatus().catch(() => null),
        getLeapsWatchlist().catch(() => []),
        getLeapsSignals(undefined, 30).catch(() => []),
        getLeapsCooldowns().catch(() => []),
      ])
      setStatus(s)
      setWatchlist(wl)
      setSignals(sigs)
      setCooldowns(cd)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  async function handleScan(symbol?: string) {
    setScanning(true)
    try {
      await triggerLeapsScan(symbol)
      setTimeout(loadAll, 1500)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setScanning(false)
    }
  }

  async function handleToggleEnabled(symbol: string, enabled: boolean) {
    await updateLeapsWatchlistItem(symbol, { enabled: !enabled })
    await loadAll()
  }

  async function handleSaveFloor(symbol: string) {
    const val = parseFloat(editingFloor[symbol] || '')
    if (isNaN(val) || val <= 0) return
    await updateLeapsWatchlistItem(symbol, { floor_price: val })
    setEditingFloor(prev => { const n = { ...prev }; delete n[symbol]; return n })
    await loadAll()
  }

  async function handleResend(id: string) {
    try {
      const r = await resendLeapsSignal(id)
      alert(r.sent ? '已重新推送到 Telegram' : '推送失败（Telegram 未配置？）\n\n' + r.message)
    } catch (e: any) {
      alert('推送异常：' + e.message)
    }
  }

  return (
    <div style={{ padding: '20px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>LEAPS Put 信号监控</h2>
        <button
          onClick={() => handleScan()}
          disabled={scanning}
          className="btn btn-primary"
          style={{ fontSize: 13, padding: '5px 14px' }}
        >
          {scanning ? '扫描中...' : '手动扫描全部'}
        </button>
        <button onClick={loadAll} disabled={loading} className="btn" style={{ fontSize: 13, padding: '5px 12px' }}>
          刷新
        </button>
      </div>

      {error && (
        <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>
      )}

      {/* Tab 导航 */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid var(--border)' }}>
        {(['overview', 'watchlist', 'signals', 'cooldowns'] as const).map(t => (
          <div
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: '8px 16px', cursor: 'pointer', fontSize: 13,
              borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
              color: tab === t ? 'var(--accent)' : 'var(--text-secondary)',
            }}
          >
            {t === 'overview' ? '概览' : t === 'watchlist' ? `白名单（${watchlist.length}）` : t === 'signals' ? `信号历史（${signals.length}）` : `冷却中（${cooldowns.length}）`}
          </div>
        ))}
      </div>

      {/* 概览 */}
      {tab === 'overview' && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 28 }}>
            {[
              { label: '白名单标的', value: status?.watchlist_enabled ?? '-', sub: `共 ${status?.watchlist_total ?? 0} 个` },
              { label: '30天信号', value: signals.length, sub: '条记录' },
              { label: '冷却合约', value: status?.active_cooldowns ?? '-', sub: '活跃冷却' },
              { label: '二级信号', value: signals.filter(s => s.signal_level === 'SECONDARY').length, sub: 'EMA200 触发' },
            ].map(({ label, value, sub }) => (
              <div key={label} className="card" style={{ padding: '16px 20px' }}>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>{label}</div>
                <div style={{ fontSize: 28, fontWeight: 700 }}>{value}</div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{sub}</div>
              </div>
            ))}
          </div>

          {status?.recent_signals && status.recent_signals.length > 0 && (
            <div>
              <h3 style={{ fontSize: 14, marginBottom: 12 }}>最近信号</h3>
              <SignalsTable signals={status.recent_signals} onResend={handleResend} />
            </div>
          )}
        </div>
      )}

      {/* 白名单 */}
      {tab === 'watchlist' && (
        <div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                {['标的', '名称', '接货底线价', '状态', '操作'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {watchlist.map(item => (
                <tr key={item.symbol} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 12px', fontWeight: 600 }}>{item.symbol}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>{item.name}</td>
                  <td style={{ padding: '10px 12px' }}>
                    {editingFloor[item.symbol] !== undefined ? (
                      <div style={{ display: 'flex', gap: 6 }}>
                        <input
                          type="number"
                          value={editingFloor[item.symbol]}
                          onChange={e => setEditingFloor(prev => ({ ...prev, [item.symbol]: e.target.value }))}
                          style={{ width: 90, padding: '3px 6px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)' }}
                        />
                        <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => handleSaveFloor(item.symbol)}>保存</button>
                        <button className="btn" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setEditingFloor(prev => { const n = { ...prev }; delete n[item.symbol]; return n })}>取消</button>
                      </div>
                    ) : (
                      <span style={{ cursor: 'pointer', color: 'var(--accent)' }} onClick={() => setEditingFloor(prev => ({ ...prev, [item.symbol]: String(item.floor_price) }))}>
                        ${item.floor_price}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    <span style={{ color: item.enabled ? '#4ade80' : 'var(--text-secondary)' }}>
                      {item.enabled ? '监控中' : '已停用'}
                    </span>
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        className="btn"
                        style={{ fontSize: 12, padding: '3px 10px' }}
                        onClick={() => handleToggleEnabled(item.symbol, item.enabled)}
                      >
                        {item.enabled ? '停用' : '启用'}
                      </button>
                      <button
                        className="btn btn-primary"
                        style={{ fontSize: 12, padding: '3px 10px' }}
                        disabled={scanning}
                        onClick={() => handleScan(item.symbol)}
                      >
                        扫描
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 信号历史 */}
      {tab === 'signals' && (
        <SignalsTable signals={signals} onResend={handleResend} showFull />
      )}

      {/* 冷却 */}
      {tab === 'cooldowns' && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              {['合约', '标的', '冷却到期时间'].map(h => (
                <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cooldowns.length === 0 && (
              <tr><td colSpan={3} style={{ padding: '20px 12px', color: 'var(--text-secondary)', textAlign: 'center' }}>暂无冷却中的合约</td></tr>
            )}
            {cooldowns.map(c => (
              <tr key={c.contract_code} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '10px 12px', fontFamily: 'monospace', fontSize: 12 }}>{c.contract_code}</td>
                <td style={{ padding: '10px 12px', fontWeight: 600 }}>{c.symbol}</td>
                <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>{formatDate(c.cooldown_until)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function SignalsTable({
  signals,
  onResend,
  showFull = false,
}: {
  signals: LeapsSignal[]
  onResend: (id: string) => void
  showFull?: boolean
}) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (signals.length === 0) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '20px 0' }}>暂无信号记录</div>
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
          {['信号', '标的', '合约', 'IV Rank', '触发价/均线', '标的价', '时间', ''].map(h => (
            <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 500 }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {signals.map(sig => (
          <>
            <tr
              key={sig.id}
              style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
              onClick={() => setExpanded(expanded === sig.id ? null : sig.id)}
            >
              <td style={{ padding: '10px 12px' }}><Badge level={sig.signal_level} /></td>
              <td style={{ padding: '10px 12px', fontWeight: 600 }}>{sig.symbol}</td>
              <td style={{ padding: '10px 12px', fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)' }}>
                {formatExpiry(sig.contract_code.split('.').pop()?.slice(-12, -6) || '')} {sig.signal_level === 'SECONDARY' ? '🔥' : ''}
              </td>
              <td style={{ padding: '10px 12px' }}>
                <span style={{ color: sig.iv_rank >= 80 ? '#f87171' : sig.iv_rank >= 70 ? '#fb923c' : 'var(--text)' }}>
                  {sig.iv_rank.toFixed(0)}
                </span>
              </td>
              <td style={{ padding: '10px 12px' }}>
                {sig.trigger_price.toFixed(2)} / {sig.ema_type}({sig.ema_value.toFixed(2)})
              </td>
              <td style={{ padding: '10px 12px' }}>${sig.underlying_price}</td>
              <td style={{ padding: '10px 12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                {sig.is_intraday && <span style={{ fontSize: 10, marginRight: 4, color: '#fb923c' }}>盘中</span>}
                {formatDate(sig.created_at)}
              </td>
              <td style={{ padding: '10px 12px' }}>
                <button
                  className="btn"
                  style={{ fontSize: 11, padding: '2px 8px' }}
                  onClick={e => { e.stopPropagation(); onResend(sig.id) }}
                >
                  推送
                </button>
              </td>
            </tr>
            {expanded === sig.id && sig.suggestions.length > 0 && (
              <tr key={sig.id + '_detail'} style={{ background: 'var(--bg-secondary)' }}>
                <td colSpan={8} style={{ padding: '12px 24px' }}>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>📋 建议交易（卖出虚值 put，delta 0.20~0.30）</div>
                  <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                    {sig.suggestions.map(s => (
                      <div key={s.contract_code} style={{
                        padding: '8px 14px', background: 'var(--bg)',
                        border: '1px solid var(--border)', borderRadius: 6,
                        minWidth: 180,
                      }}>
                        <div style={{ fontWeight: 700, marginBottom: 4 }}>${s.strike}P &nbsp; δ{s.delta}</div>
                        <div>权利金 <b>${s.premium}</b></div>
                        <div>年化 <b style={{ color: '#4ade80' }}>{s.annualized_yield}%</b></div>
                        <div style={{ color: 'var(--text-secondary)', fontSize: 11 }}>接货成本 ${s.cost_basis}</div>
                        <div style={{ color: 'var(--text-secondary)', fontSize: 11 }}>DTE {s.dte}天</div>
                      </div>
                    ))}
                  </div>
                </td>
              </tr>
            )}
          </>
        ))}
      </tbody>
    </table>
  )
}
