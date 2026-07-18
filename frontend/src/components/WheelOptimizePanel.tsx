import { Fragment, useCallback, useEffect, useState } from 'react'
import {
  getAppSettings,
  getWheelPortfolio,
  getWheelStress,
  getWheelCorrelation,
  getWheelAdmission,
  getWheelHealth,
  getWheelReconcile,
  applyWheelReconcileDraft,
  runWheelBacktest,
  getWheelProfiles,
  activateWheelProfile,
  pushWheelPositionAlerts,
  getWheelFloorSuggest,
  getWheelFloorLog,
  updateWheelTarget,
} from '../services/api'

const C = { green: '#4ade80', orange: '#fb923c', red: '#f87171', blue: '#38bdf8', purple: '#a78bfa' }

/** 策略档位：按钮直接中文 + 分档语义色 */
const PROFILE_META: Record<string, { label: string; color: string; hint: string }> = {
  conservative: { label: '稳健', color: C.blue, hint: '更严 delta/年化，少接货' },
  balanced: { label: '均衡', color: C.green, hint: '默认盘中档' },
  aggressive: { label: '激进', color: C.orange, hint: '更宽参数，追求权利金' },
}

function profileMeta(name: string) {
  return PROFILE_META[name] || {
    label: name,
    color: C.purple,
    hint: '',
  }
}

function fmt(v: number | null | undefined, d = 1) {
  if (v == null || Number.isNaN(v)) return '--'
  return v.toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 })
}

export default function WheelOptimizePanel() {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [portfolio, setPortfolio] = useState<any>(null)
  const [stress, setStress] = useState<any>(null)
  const [corr, setCorr] = useState<any>(null)
  const [admission, setAdmission] = useState<any>(null)
  const [admissionExpand, setAdmissionExpand] = useState<string | null>(null)
  const [floorLog, setFloorLog] = useState<any[]>([])
  const [health, setHealth] = useState<any>(null)
  const [reconcile, setReconcile] = useState<any>(null)
  const [profiles, setProfiles] = useState<any>(null)
  const [btSymbol, setBtSymbol] = useState('AAPL')
  const [btResult, setBtResult] = useState<any>(null)
  const [btLoading, setBtLoading] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const [p, s, c, a, h, pr, fl] = await Promise.all([
        getWheelPortfolio().catch(() => null),
        getWheelStress().catch(() => null),
        getWheelCorrelation().catch(() => null),
        getWheelAdmission().catch(() => null),
        getWheelHealth().catch(() => null),
        getWheelProfiles().catch(() => null),
        getWheelFloorLog(undefined, 20).catch(() => ({ items: [] })),
      ])
      setPortfolio(p)
      setStress(s)
      setCorr(c)
      setAdmission(a)
      setHealth(h)
      setProfiles(pr)
      setFloorLog(fl?.items || [])
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function doReconcile() {
    setMsg('')
    const st = getAppSettings()
    try {
      const r = await getWheelReconcile(st.marketHost, st.marketPort, st.tradingEnv === 'REAL' ? 'REAL' : 'SIMULATE')
      setReconcile(r)
      setMsg(r.ok ? `对账完成: ${r.summary?.diff_count || 0} 处差异` : (r.error || '对账失败'))
    } catch (e: any) {
      setMsg('对账失败: ' + e.message)
    }
  }

  async function applyDraft(d: any) {
    try {
      await applyWheelReconcileDraft(d)
      setMsg('草稿已登记')
      await doReconcile()
      load()
    } catch (e: any) {
      setMsg('登记失败: ' + e.message)
    }
  }

  async function runBt() {
    setBtLoading(true)
    setBtResult(null)
    try {
      setBtResult(await runWheelBacktest(btSymbol.trim().toUpperCase(), {
        delta: 0.25, dte: 30, profit_take: 0.5, floor_pct: 0.9, min_annualized: 15,
      }))
    } catch (e: any) {
      setBtResult({ ok: false, error: e.message })
    } finally {
      setBtLoading(false)
    }
  }

  async function activate(name: string) {
    try {
      await activateWheelProfile(name)
      setMsg(`已切换为「${profileMeta(name).label}」`)
      const pr = await getWheelProfiles()
      setProfiles(pr)
    } catch (e: any) {
      setMsg(e.message)
    }
  }

  async function applyFloor(symbol: string) {
    try {
      const f = await getWheelFloorSuggest(symbol)
      if (!f.suggested_floor) {
        setMsg(`${symbol}: 无市场结构参考愿接价`)
        return
      }
      const cur = f.current_floor != null ? `当前 $${f.current_floor}` : '当前未设'
      const ok = window.confirm(
        `${symbol} 市场结构参考愿接价 $${f.suggested_floor}\n`
        + `${cur}${f.spot != null ? ` · 现价 $${f.spot}` : ''}\n\n`
        + `${f.rationale || '仅供参考,非「正确floor」'}\n\n`
        + `确认写入愿接最高价? (Put strike 必须 ≤ 此价)`,
      )
      if (!ok) return
      await updateWheelTarget(symbol, {
        floor_price: f.suggested_floor,
        floor_change_source: 'smart',
      })
      setMsg(`${symbol} 愿接价已更新为 $${f.suggested_floor}(参考应用)`)
      load()
    } catch (e: any) {
      setMsg(e.message)
    }
  }

  async function pushAlerts() {
    const st = getAppSettings()
    try {
      const r = await pushWheelPositionAlerts(st.marketHost, st.marketPort)
      setMsg(r.sent ? `已推送 ${r.count} 条` : `未推送(count=${r.count}, 检查 Telegram)`)
    } catch (e: any) {
      setMsg(e.message)
    }
  }

  const card: Record<string, string | number> = {
    padding: '14px 16px', borderRadius: 8, border: '1px solid var(--border)',
    background: 'var(--bg-secondary)', marginBottom: 12,
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="btn" onClick={load} disabled={loading} style={{ fontSize: 12 }}>
          {loading ? '加载中…' : '刷新优化数据'}
        </button>
        <button className="btn" onClick={doReconcile} style={{ fontSize: 12 }}>富途对账</button>
        <button className="btn" onClick={pushAlerts} style={{ fontSize: 12 }}>推送持仓告警</button>
        {msg && <span style={{ fontSize: 12, color: C.blue }}>{msg}</span>}
      </div>
      {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}

      {/* 策略档位 */}
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 13 }}>策略档位</div>
          {profiles?.active && (
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              {profileMeta(profiles.active).hint}
            </span>
          )}
        </div>
        <div
          role="group"
          aria-label="策略档位"
          style={{
            display: 'inline-flex',
            padding: 3,
            borderRadius: 10,
            background: 'var(--bg-primary, #0a0a0a)',
            border: '1px solid var(--border)',
            gap: 2,
            flexWrap: 'wrap',
          }}
        >
          {(profiles?.presets?.length
            ? profiles.presets as string[]
            : ['conservative', 'balanced', 'aggressive']
          ).map((n: string) => {
            const meta = profileMeta(n)
            const active = profiles?.active === n
            return (
              <button
                key={n}
                type="button"
                onClick={() => { if (!active) activate(n) }}
                title={meta.hint}
                style={{
                  minWidth: 72,
                  padding: '8px 16px',
                  borderRadius: 8,
                  border: 'none',
                  cursor: active ? 'default' : 'pointer',
                  fontSize: 13,
                  fontWeight: active ? 700 : 500,
                  background: active ? meta.color : 'transparent',
                  color: active ? '#0a0a0a' : 'var(--text-secondary)',
                  transition: 'background 0.15s, color 0.15s',
                }}
              >
                {meta.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* 组合资金 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>组合资金</div>
        {portfolio ? (
          <>
            {(portfolio.notes || []).length > 0 && (
              <div style={{
                marginBottom: 10, padding: '8px 10px', borderRadius: 6, fontSize: 12,
                border: `1px solid ${C.orange}55`, background: C.orange + '14', color: C.orange,
              }}>
                {(portfolio.notes as string[]).map((n, i) => <div key={i}>⚠ {n}</div>)}
                <div style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
                  去 <b style={{ color: C.blue }}>设置页 → 后端配置 → 组合风控</b> 填「组合净值」；
                  单标的上限在 <b style={{ color: C.blue }}>Wheel → 标的设置 / 看板✎编辑</b> 填「资金上限」。
                </div>
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 10, fontSize: 12 }}>
              <div>
                净值参考{' '}
                <b>{portfolio.equity != null ? fmt(portfolio.equity, 0) : '未设置'}</b>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>
                  {portfolio.equity_source === 'config' ? '来自设置·组合净值'
                    : portfolio.equity_source === 'max_capital_sum' ? '各标的 max_capital 之和'
                    : '请设置净值或 max_capital'}
                </div>
              </div>
              <div>
                已占用 <b>{fmt(portfolio.total_committed, 0)}</b>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>
                  CSP {fmt(portfolio.csp_collateral, 0)} + 持股 {fmt(portfolio.holding_cost, 0)}
                </div>
              </div>
              <div>
                利用率{' '}
                <b style={{ color: portfolio.over_portfolio ? C.red : C.green }}>
                  {portfolio.utilization_pct != null
                    ? `${fmt(portfolio.utilization_pct)}% / ${fmt(portfolio.max_portfolio_pct)}%`
                    : `— / ${fmt(portfolio.max_portfolio_pct)}%`}
                </b>
              </div>
              <div>
                闲置现金{' '}
                <b style={{ color: C.orange }}>
                  {portfolio.idle_cash != null
                    ? `${fmt(portfolio.idle_cash, 0)}${portfolio.idle_pct != null ? ` (${fmt(portfolio.idle_pct)}%)` : ''}`
                    : '—'}
                </b>
              </div>
              <div>全 assign 压力 <b>{fmt(portfolio.assignment_stress, 0)}</b></div>
            </div>
            {(portfolio.violations || []).length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: C.red }}>
                ⚠ 超限: {(portfolio.violations as any[]).map(v => v.symbol).join(', ')}
              </div>
            )}
            <div style={{ marginTop: 10, maxHeight: 200, overflow: 'auto' }}>
              <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: 'var(--text-secondary)', textAlign: 'left' }}>
                    <th>标的</th><th>占用</th><th>CSP</th><th>持股</th><th>上限</th><th>余量</th><th>净值%</th>
                  </tr>
                </thead>
                <tbody>
                  {(portfolio.per_symbol || []).slice(0, 20).map((r: any) => (
                    <tr key={r.symbol} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 0' }}>{r.symbol}</td>
                      <td>{fmt(r.committed, 0)}</td>
                      <td>{fmt(r.csp_collateral, 0)}</td>
                      <td>{fmt(r.holding_cost, 0)}</td>
                      <td style={{ color: r.cap_unset ? 'var(--text-secondary)' : undefined }}>
                        {r.cap_unset ? '未设' : fmt(r.max_capital, 0)}
                      </td>
                      <td style={{ color: (r.headroom ?? 0) < 0 ? C.red : undefined }}>
                        {r.headroom != null ? fmt(r.headroom, 0) : '—'}
                      </td>
                      <td style={{ color: r.over_symbol_pct ? C.orange : undefined }}>
                        {r.pct_of_equity != null ? `${fmt(r.pct_of_equity)}%` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>暂无数据</div>}
      </div>

      {/* 压力测试 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>下跌压力测试</div>
        {stress?.scenarios ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 10 }}>
            {stress.scenarios.map((sc: any) => (
              <div key={sc.shock_pct} style={{
                padding: 10, borderRadius: 6, border: `1px solid ${C.orange}44`, background: C.orange + '11', fontSize: 12,
              }}>
                <div style={{ fontWeight: 700, color: C.orange }}>标的 {sc.shock_pct}%</div>
                <div>CSP 变 ITM: <b>{sc.csp_itm_count}</b></div>
                <div>接货资金: <b>{fmt(sc.assign_capital_needed, 0)}</b></div>
                <div>总占用估: <b>{fmt(sc.total_capital_if_assigned, 0)}</b></div>
                {(sc.itm_positions || []).slice(0, 4).map((p: any) => (
                  <div key={p.cycle_id || p.symbol} style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                    {p.symbol} K{p.strike} → {fmt(p.assign_cost, 0)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>暂无在场 CSP 或无日K</div>}
        {stress?.note && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>{stress.note}</div>}
      </div>

      {/* 相关性 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>高相关标的(≥0.7 慎同时卖 Put)</div>
        {(corr?.high_corr || []).length ? (
          <div style={{ fontSize: 12 }}>
            {corr.high_corr.map((p: any) => (
              <span key={p.a + p.b} style={{
                display: 'inline-block', margin: '0 8px 6px 0', padding: '2px 8px',
                borderRadius: 10, background: C.red + '22', color: C.red, fontSize: 11,
              }}>{p.a}↔{p.b} {p.corr}</span>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            无高相关对(需足够本地日K)
          </div>
        )}
      </div>

      {/* 准入评分 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>标的准入评分</div>
        {admission?.floor_glossary && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.45 }}>
            {admission.floor_glossary}
          </div>
        )}
        <div style={{ maxHeight: 280, overflow: 'auto' }}>
          <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-secondary)', textAlign: 'left' }}>
                <th>标的</th><th>分</th><th>建议</th><th>激进度</th>
                <th title="市场结构参考愿接价">参考愿接</th>
                <th>标签</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(admission?.items || []).map((r: any) => (
                <Fragment key={r.symbol}>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 0' }}>
                      <button
                        type="button"
                        className="btn"
                        style={{ fontSize: 11, padding: '0 4px', fontWeight: 600 }}
                        title="展开分项"
                        onClick={() => setAdmissionExpand(e => e === r.symbol ? null : r.symbol)}
                      >
                        {admissionExpand === r.symbol ? '▾' : '▸'} {r.symbol}
                      </button>
                    </td>
                    <td style={{
                      fontWeight: 700,
                      color: r.score >= 70 ? C.green : r.score < 35 ? C.red : C.orange,
                    }}>{r.score}</td>
                    <td>{r.action}</td>
                    <td style={{
                      color: r.aggressiveness === '激进' ? C.orange
                        : r.aggressiveness === '保守' || r.aggressiveness === '偏保守' ? C.blue
                          : 'var(--text-secondary)',
                    }}>{r.aggressiveness || '--'}</td>
                    <td style={{ color: 'var(--text-secondary)', maxWidth: 220 }}>{(r.tags || []).join(' · ')}</td>
                    <td>
                      <button className="btn" style={{ fontSize: 10, padding: '1px 6px' }}
                        title="市场结构参考,需确认后写入"
                        onClick={() => applyFloor(r.symbol)}>参考愿接价</button>
                    </td>
                  </tr>
                  {admissionExpand === r.symbol && (
                    <tr key={`${r.symbol}-detail`}>
                      <td colSpan={6} style={{ padding: '6px 8px 10px', background: 'var(--bg-secondary)', fontSize: 11 }}>
                        <div style={{ marginBottom: 4, color: 'var(--text-secondary)' }}>
                          主分=趋势/波动/IV/历史/数据 · floor 仅轻提示 · 点行展开
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                          {(r.factor_detail?.length
                            ? r.factor_detail
                            : Object.entries(r.factors || {}).map(([k, v]) => ({ key: k, delta: v, label: k, note: '' }))
                          ).map((row: any) => {
                            const d = Number(row.delta) || 0
                            return (
                              <span key={row.key || row.label} style={{
                                padding: '2px 6px', borderRadius: 4, border: '1px solid var(--border)',
                                color: d > 0 ? C.green : d < 0 ? C.red : 'var(--text-secondary)',
                              }}>
                                {row.label || row.key} {d > 0 ? '+' : ''}{d}
                                {row.note ? ` · ${row.note}` : ''}
                              </span>
                            )
                          })}
                        </div>
                        {r.metrics?.floor_price != null && (
                          <div style={{ marginTop: 6, opacity: 0.9 }}>
                            愿接价 ${r.metrics.floor_price}
                            {r.metrics.spot != null && ` · 现价 $${Number(r.metrics.spot).toFixed(2)}`}
                            {r.metrics.floor_spot_ratio != null && ` · floor/spot=${r.metrics.floor_spot_ratio}`}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 愿接价变更日志 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>愿接价变更记录</div>
        {floorLog.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>暂无记录(修改/应用参考愿接价后出现)</div>
        ) : (
          <div style={{ maxHeight: 140, overflow: 'auto', fontSize: 11 }}>
            {floorLog.map((x: any) => (
              <div key={x.id} style={{ display: 'flex', gap: 10, padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                <span style={{ fontWeight: 600, width: 56 }}>{x.symbol}</span>
                <span style={{ color: 'var(--text-secondary)' }}>
                  ${x.old_floor ?? '--'} → <b style={{ color: 'var(--text)' }}>${x.new_floor}</b>
                </span>
                <span style={{ color: C.blue }}>{x.source === 'smart' ? '参考应用' : '手改'}</span>
                <span style={{ opacity: 0.7, marginLeft: 'auto' }}>{(x.created_at || '').slice(0, 16)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 策略体检 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>策略体检(归因)</div>
        {health ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(110px,1fr))', gap: 8, fontSize: 12 }}>
            <div>已结束轮 <b>{health.closed_cycles}</b></div>
            <div>胜率 <b style={{ color: C.green }}>{fmt(health.win_rate)}%</b></div>
            <div>净权利金 <b>{fmt(health.premium_net_total, 0)}</b></div>
            <div>已实现盈亏 <b style={{ color: (health.realized_pnl_total || 0) >= 0 ? C.green : C.red }}>
              {fmt(health.realized_pnl_total, 0)}</b></div>
            <div>均持仓天 <b>{fmt(health.avg_duration_days)}</b></div>
            <div>Assign率 <b>{health.assign_rate != null ? (health.assign_rate * 100).toFixed(1) + '%' : '--'}</b></div>
            <div>Call走率 <b>{health.called_away_rate != null ? (health.called_away_rate * 100).toFixed(1) + '%' : '--'}</b></div>
          </div>
        ) : null}
        {health?.tip && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>{health.tip}</div>}
        {(health?.symbol_heat || []).length > 0 && (
          <div style={{ marginTop: 8, fontSize: 11 }}>
            标的热力(差→好): {(health.symbol_heat as any[]).slice(0, 8).map(h => (
              <span key={h.symbol} style={{
                marginRight: 8, color: h.realized_pnl < 0 ? C.red : C.green,
              }}>{h.symbol} {fmt(h.realized_pnl, 0)}</span>
            ))}
          </div>
        )}
      </div>

      {/* 对账结果 */}
      {reconcile && (
        <div style={card}>
          <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>
            富途对账结果
            {reconcile.summary && (
              <span style={{ fontWeight: 400, color: 'var(--text-secondary)', marginLeft: 8 }}>
                差异 {reconcile.summary.diff_count} · 草稿 {reconcile.summary.draft_count} · 警告 {reconcile.summary.warnings}
              </span>
            )}
          </div>
          {(reconcile.diffs || []).map((d: any, i: number) => (
            <div key={i} style={{
              fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--border)',
              color: d.severity === 'warning' ? C.orange : 'var(--text)',
            }}>
              [{d.type}] {d.message}
            </div>
          ))}
          {(reconcile.drafts || []).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 12, marginBottom: 6 }}>登记草稿(请核对价格后应用):</div>
              {(reconcile.drafts as any[]).map((d, i) => (
                <div key={i} style={{
                  display: 'flex', gap: 8, alignItems: 'center', fontSize: 11, marginBottom: 4,
                }}>
                  <code style={{ flex: 1 }}>{d.trade_type} {d.symbol} {d.contract_code || ''} @{d.price}</code>
                  <button className="btn" style={{ fontSize: 10, padding: '2px 8px' }}
                    onClick={() => applyDraft(d)}>登记</button>
                </div>
              ))}
            </div>
          )}
          {(reconcile.futu?.errors || []).length > 0 && (
            <div style={{ fontSize: 11, color: C.orange, marginTop: 6 }}>
              {reconcile.futu.errors.join('; ')}
            </div>
          )}
        </div>
      )}

      {/* 回测 */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>Wheel 规则回测(合成权利金近似)</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <input value={btSymbol} onChange={e => setBtSymbol(e.target.value)}
            style={{ width: 100, padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)' }}
            placeholder="AAPL" />
          <button className="btn" style={{ fontSize: 12 }} onClick={runBt} disabled={btLoading}>
            {btLoading ? '回测中…' : '运行回测'}
          </button>
        </div>
        {btResult && (
          btResult.ok ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(100px,1fr))', gap: 8, fontSize: 12 }}>
              <div>CAGR <b style={{ color: C.green }}>{fmt(btResult.cagr_pct)}%</b></div>
              <div>总收益 <b>{fmt(btResult.total_return_pct)}%</b></div>
              <div>最大回撤 <b style={{ color: C.red }}>{fmt(btResult.max_drawdown_pct)}%</b></div>
              <div>终值 <b>{fmt(btResult.final_equity, 0)}</b></div>
              <div>Assign <b>{btResult.assign_count}</b></div>
              <div>交易数 <b>{btResult.trade_count}</b></div>
              <div style={{ gridColumn: '1/-1', fontSize: 11, color: 'var(--text-secondary)' }}>{btResult.note}</div>
            </div>
          ) : (
            <div style={{ fontSize: 12, color: C.red }}>{btResult.error || '失败'}</div>
          )
        )}
      </div>
    </div>
  )
}
