import { useEffect, useState } from 'react'
import {
  AppSettings,
  BackendConfig,
  WheelPushLogItem,
  connectTrading,
  disconnectTrading,
  getAppSettings,
  getBackendConfig,
  getMarketStatus,
  getWheelAlertLog,
  getWheelAlertPreview,
  saveAppSettings,
  saveBackendConfig,
  testWheelAlert,
} from '../services/api'

type SettingsTab = 'wheel' | 'research' | 'general'

export default function SettingsPage() {
  const [tab, setTab] = useState<SettingsTab>('wheel')
  const [settings, setSettings] = useState<AppSettings>(getAppSettings())
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [marketStatus, setMarketStatus] = useState<{ connected: boolean; adapter: string } | null>(null)
  const [testingMarket, setTestingMarket] = useState(false)
  const [connectingTrading, setConnectingTrading] = useState(false)
  const [tradingConnected, setTradingConnected] = useState(false)

  function updateField<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setSettings(prev => ({ ...prev, [key]: value }))
  }

  function handleSave() {
    setSaving(true)
    const next = saveAppSettings(settings)
    setSettings(next)
    setMessage('前端设置已保存')
    setTimeout(() => setMessage(''), 2000)
    setSaving(false)
  }

  async function handleTestMarket() {
    setTestingMarket(true)
    setMessage('')
    try {
      const latest = saveAppSettings(settings)
      setSettings(latest)
      const status = await getMarketStatus(latest)
      setMarketStatus({ connected: status.connected, adapter: status.adapter })
      setMessage(`行情源连接正常：${status.adapter}@${status.host}:${status.port}`)
    } catch (e: any) {
      setMessage(`行情源测试失败：${e.message}`)
    } finally {
      setTestingMarket(false)
    }
  }

  async function handleConnectTrading() {
    setConnectingTrading(true)
    setMessage('')
    try {
      const latest = saveAppSettings(settings)
      setSettings(latest)
      await connectTrading(latest)
      setTradingConnected(true)
      setMessage(`交易账户已连接：${latest.tradingAdapter} / ${latest.tradingEnv}`)
    } catch (e: any) {
      setTradingConnected(false)
      setMessage(`交易连接失败：${e.message}`)
    } finally {
      setConnectingTrading(false)
    }
  }

  async function handleDisconnectTrading() {
    setConnectingTrading(true)
    setMessage('')
    try {
      await disconnectTrading()
      setTradingConnected(false)
      setMessage('交易账户已断开')
    } catch (e: any) {
      setMessage(`断开失败：${e.message}`)
    } finally {
      setConnectingTrading(false)
    }
  }

  const tabs: { k: SettingsTab; label: string; hint: string }[] = [
    { k: 'wheel', label: 'Wheel', hint: '触线 / 高分 / 持仓 / 组合风控' },
    { k: 'research', label: '研究', hint: '回测 / 行情 / 交易前端' },
    { k: 'general', label: '通用', hint: 'Telegram / API / OpenD' },
  ]

  useEffect(() => {
    const onSec = (e: Event) => {
      const sec = (e as CustomEvent).detail?.section
      if (sec === 'wheel' || sec === 'research' || sec === 'general') setTab(sec)
    }
    window.addEventListener('tradeforge:settings-section', onSec)
    return () => window.removeEventListener('tradeforge:settings-section', onSec)
  }, [])

  return (
    <div className="page" style={{ maxWidth: 920 }}>
      <h2 style={{ marginBottom: 8 }}>设置</h2>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 16px' }}>
        按使用场景分区：Wheel 管盘中策略；研究管回测与前端行情；通用管密钥与后台连接。
      </p>

      {message && (
        <div className="banner info" style={{ marginBottom: 12 }}>
          <span style={{ flex: 1 }}>{message}</span>
        </div>
      )}

      <div className="page-tabs" style={{ marginBottom: 16 }}>
        {tabs.map(t => (
          <button
            key={t.k}
            type="button"
            className={`page-tab ${tab === t.k ? 'active' : ''}`}
            onClick={() => setTab(t.k)}
            title={t.hint}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'wheel' && (
        <BackendConfigCard
          mode="wheel"
          onMessage={setMessage}
        />
      )}

      {tab === 'research' && (
        <div className="card">
          <div className="editor-section">
            <h4>回测参数</h4>
            <div className="settings-row">
              <label>初始资金</label>
              <input
                type="number"
                value={settings.initialCapital}
                onChange={e => updateField('initialCapital', Number(e.target.value))}
              />
            </div>
            <div className="settings-row">
              <label>手续费率</label>
              <input
                type="number"
                step="0.0001"
                value={settings.feeRate}
                onChange={e => updateField('feeRate', Number(e.target.value))}
              />
            </div>
            <div className="settings-row">
              <label>滑点</label>
              <input
                type="number"
                step="0.001"
                value={settings.slippage}
                onChange={e => updateField('slippage', Number(e.target.value))}
              />
            </div>
          </div>

          <div className="editor-section">
            <h4>行情数据源（前端）</h4>
            <div className="settings-row">
              <label>数据源</label>
              <select
                value={settings.marketDataSource}
                onChange={e => updateField('marketDataSource', e.target.value as AppSettings['marketDataSource'])}
              >
                <option value="futu">Futu OpenD</option>
                <option value="finnhub">Finnhub</option>
              </select>
            </div>
            <div className="settings-row">
              <label>Host</label>
              <input
                value={settings.marketHost}
                onChange={e => updateField('marketHost', e.target.value)}
                disabled={settings.marketDataSource === 'finnhub'}
              />
            </div>
            <div className="settings-row">
              <label>Port</label>
              <input
                type="number"
                value={settings.marketPort}
                onChange={e => updateField('marketPort', Number(e.target.value))}
                disabled={settings.marketDataSource === 'finnhub'}
              />
            </div>
            {settings.marketDataSource === 'finnhub' && (
              <div className="settings-row">
                <label>说明</label>
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                  Finnhub Key 在「通用」Tab 配置
                </span>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button type="button" className="btn btn-secondary btn-sm" disabled={testingMarket} onClick={handleTestMarket}>
                {testingMarket ? '测试中…' : '测试行情连接'}
              </button>
              {marketStatus && (
                <span style={{ fontSize: 13, color: marketStatus.connected ? 'var(--green)' : 'var(--warning)', alignSelf: 'center' }}>
                  {marketStatus.connected ? `已连接 ${marketStatus.adapter}` : '未连接'}
                </span>
              )}
            </div>
          </div>

          <div className="editor-section">
            <h4>交易连接（富途前端）</h4>
            <div className="settings-row">
              <label>适配器</label>
              <select
                value={settings.tradingAdapter}
                onChange={e => updateField('tradingAdapter', e.target.value as AppSettings['tradingAdapter'])}
              >
                <option value="futu">Futu</option>
              </select>
            </div>
            <div className="settings-row">
              <label>环境</label>
              <select
                value={settings.tradingEnv}
                onChange={e => updateField('tradingEnv', e.target.value as AppSettings['tradingEnv'])}
              >
                <option value="SIM">模拟</option>
                <option value="REAL">实盘</option>
              </select>
            </div>
            <div className="settings-row">
              <label>Trading Host</label>
              <input value={settings.tradingHost} onChange={e => updateField('tradingHost', e.target.value)} />
            </div>
            <div className="settings-row">
              <label>Trading Port</label>
              <input
                type="number"
                value={settings.tradingPort}
                onChange={e => updateField('tradingPort', Number(e.target.value))}
              />
            </div>
            <div className="settings-row">
              <label>默认下单数量</label>
              <input
                type="number"
                value={settings.defaultOrderQuantity}
                onChange={e => updateField('defaultOrderQuantity', Number(e.target.value))}
              />
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button type="button" className="btn btn-primary btn-sm" disabled={connectingTrading} onClick={handleConnectTrading}>
                {connectingTrading ? '连接中…' : '连接交易'}
              </button>
              <button type="button" className="btn btn-secondary btn-sm" disabled={connectingTrading} onClick={handleDisconnectTrading}>
                断开
              </button>
              <span style={{ fontSize: 13, color: tradingConnected ? 'var(--green)' : 'var(--text-secondary)', alignSelf: 'center' }}>
                {tradingConnected ? '已连接' : '未连接'}
              </span>
            </div>
          </div>

          <div className="editor-section">
            <h4>下单确认</h4>
            <div className="settings-row">
              <label>信号触发后确认弹窗</label>
              <select
                value={settings.confirmSignals ? '1' : '0'}
                onChange={e => updateField('confirmSignals', e.target.value === '1')}
              >
                <option value="1">开启</option>
                <option value="0">关闭</option>
              </select>
            </div>
          </div>

          <div className="editor-section">
            <h4>显示</h4>
            <div className="settings-row">
              <label>主题</label>
              <select
                value={settings.theme}
                onChange={e => updateField('theme', e.target.value)}
              >
                <option value="dark">深色</option>
                <option value="light">浅色</option>
              </select>
            </div>
            <div className="settings-row">
              <label>语言</label>
              <select
                value={settings.language}
                onChange={e => updateField('language', e.target.value)}
              >
                <option value="zh">简体中文</option>
                <option value="en">English</option>
              </select>
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? '保存中…' : '保存研究/前端设置'}
            </button>
          </div>
        </div>
      )}

      {tab === 'general' && (
        <BackendConfigCard
          mode="general"
          onMessage={setMessage}
        />
      )}
    </div>
  )
}

// ── 后端服务配置(存本地数据库) ───────────────────────────────────────────────
function BackendConfigCard({
  mode,
  onMessage,
}: {
  mode: 'wheel' | 'general'
  onMessage: (m: string) => void
}) {
  const [cfg, setCfg] = useState<BackendConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    getBackendConfig().then(setCfg).catch(e => setMsg('加载后端配置失败:' + e.message))
  }, [])

  function up(section: keyof BackendConfig, key: string, value: any) {
    setCfg(prev => prev ? {
      ...prev,
      [section]: typeof prev[section] === 'object' && prev[section] !== null
        ? { ...(prev[section] as any), [key]: value }
        : value,
    } : prev)
  }

  function upScan(key: string, value: any) {
    setCfg(p => p ? {
      ...p,
      wheel_scan: { ...(p.wheel_scan || {} as any), [key]: value },
    } : p)
  }

  function upAlerts(key: string, value: any) {
    setCfg(p => p ? {
      ...p,
      wheel_alerts: { ...(p.wheel_alerts || {}), [key]: value },
    } : p)
  }

  async function save() {
    if (!cfg) return
    setSaving(true)
    setMsg('')
    try {
      setCfg(await saveBackendConfig(cfg))
      const text = mode === 'wheel'
        ? 'Wheel 配置已保存并立即生效'
        : '通用配置已保存并立即生效'
      setMsg(text)
      onMessage(text)
      setTimeout(() => setMsg(''), 3000)
    } catch (e: any) {
      setMsg('保存失败:' + e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!cfg) return <div className="card">{msg || '正在加载配置…'}</div>

  const downPct = Math.round((cfg.wheel_timing.strike_range_down ?? 0.2) * 100)
  const upPct = Math.round((cfg.wheel_timing.strike_range_up ?? 0.1) * 100)

  return (
    <div className="card">
      {msg && <div className="banner info" style={{ marginBottom: 12 }}>{msg}</div>}

      {mode === 'general' && (
        <>
          <div className="editor-section">
            <h4>Telegram 通知</h4>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              敏感信息仅存本地数据库，不会进代码仓库。
            </p>
            <div className="settings-row">
              <label>Bot Token</label>
              <input type="password" value={cfg.telegram.bot_token} placeholder="123456:ABC-DEF..."
                onChange={e => up('telegram', 'bot_token', e.target.value)} />
            </div>
            <div className="settings-row">
              <label>Chat ID</label>
              <input value={cfg.telegram.chat_id} placeholder="-100xxxx 或用户ID"
                onChange={e => up('telegram', 'chat_id', e.target.value)} />
            </div>
            <div className="settings-row">
              <label>代理(可选)</label>
              <input value={cfg.telegram.proxy || ''} placeholder="如 http://127.0.0.1:7890"
                onChange={e => up('telegram', 'proxy', e.target.value)} />
            </div>
          </div>

          <div className="editor-section">
            <h4>外部 API</h4>
            <div className="settings-row">
              <label>Finnhub API Key</label>
              <input type="password" value={cfg.finnhub_api_key}
                onChange={e => setCfg(p => p ? { ...p, finnhub_api_key: e.target.value } : p)} />
            </div>
            <div className="settings-row">
              <label>Finnhub Base URL</label>
              <input value={cfg.finnhub_base_url} placeholder="https://finnhub.io/api/v1"
                onChange={e => setCfg(p => p ? { ...p, finnhub_base_url: e.target.value } : p)} />
            </div>
            <div className="settings-row">
              <label>Yahoo Finance Base URL</label>
              <input value={cfg.yahoo_base_url} placeholder="https://query1.finance.yahoo.com/..."
                onChange={e => setCfg(p => p ? { ...p, yahoo_base_url: e.target.value } : p)} />
            </div>
          </div>

          <div className="editor-section">
            <h4>后台 OpenD（扫描/任务用）</h4>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              与「研究」Tab 里前端行情 Host/Port 可分开配置；后台任务（触线/高分）读这里。
            </p>
            <div className="settings-row">
              <label>OpenD Host</label>
              <input value={cfg.futu.host} onChange={e => up('futu', 'host', e.target.value)} />
            </div>
            <div className="settings-row">
              <label>OpenD Port</label>
              <input type="number" value={cfg.futu.port}
                onChange={e => up('futu', 'port', Number(e.target.value))} />
            </div>
          </div>
        </>
      )}

      {mode === 'wheel' && (
        <>
          <div className="editor-section">
            <h4>触线扫描（EMA 时机）</h4>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              对应机会里的「触线」来源：合约价摸到自身 EMA50/200。标的级 delta/年化在 Wheel→标的设置。
            </p>

            <div style={{
              marginBottom: 14, padding: '12px 14px', borderRadius: 10,
              border: '1px solid rgba(56,189,248,0.35)', background: 'rgba(56,189,248,0.08)',
            }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Strike 扫描区间（相对现价）</div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 10 }}>
                只扫现价附近合约：区间 = 现价 × [1 − 下方%, 1 + 上方%]。
                例：现价 100、下 20% / 上 10% → 扫 strike <b style={{ color: 'var(--text)' }}>80 ~ 110</b>。
                CALL 另有 strike ≥ cost_basis。
              </div>
              <div className="settings-row">
                <label>下方幅度 %（OTM Put 侧）</label>
                <input
                  type="number" min={1} max={80} step={1}
                  value={downPct}
                  onChange={e => up('wheel_timing', 'strike_range_down', Math.max(0, Number(e.target.value) || 0) / 100)}
                />
              </div>
              <div className="settings-row">
                <label>上方幅度 %（OTM Call 侧）</label>
                <input
                  type="number" min={1} max={80} step={1}
                  value={upPct}
                  onChange={e => up('wheel_timing', 'strike_range_up', Math.max(0, Number(e.target.value) || 0) / 100)}
                />
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
                当前：现价 × [1−{downPct}%, 1+{upPct}%] · 默认 20% / 10%
              </div>
            </div>

            <div className="settings-row">
              <label>全局 DTE 兜底(天)</label>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input type="number" style={{ width: 90 }} value={cfg.wheel_timing.dte_min}
                  onChange={e => up('wheel_timing', 'dte_min', Number(e.target.value))} />
                ~
                <input type="number" style={{ width: 90 }} value={cfg.wheel_timing.dte_max}
                  onChange={e => up('wheel_timing', 'dte_max', Number(e.target.value))} />
              </div>
            </div>
            <div className="settings-row">
              <label>对齐标的 DTE</label>
              <select value={cfg.wheel_timing.align_target_dte !== false ? '1' : '0'}
                onChange={e => up('wheel_timing', 'align_target_dte', e.target.value === '1')}>
                <option value="1">开启（标的设置 ± pad）</option>
                <option value="0">关闭（仅用全局范围）</option>
              </select>
            </div>
            <div className="settings-row">
              <label>DTE 外扩 pad(天)</label>
              <input type="number" value={cfg.wheel_timing.dte_pad_days ?? 7}
                onChange={e => up('wheel_timing', 'dte_pad_days', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>每标的到期日数(默认6)</label>
              <input type="number" min={1} max={12} value={cfg.wheel_timing.max_expiries ?? 6}
                onChange={e => up('wheel_timing', 'max_expiries', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>优先核心 DTE 到期日</label>
              <select value={cfg.wheel_timing.prefer_core_dte !== false ? '1' : '0'}
                onChange={e => up('wheel_timing', 'prefer_core_dte', e.target.value === '1')}>
                <option value="1">开启(先 21–45 再 pad)</option>
                <option value="0">关闭(按近月截断)</option>
              </select>
            </div>
            <div className="settings-row">
              <label>EMA50 最少 K 线</label>
              <input type="number" value={cfg.wheel_timing.ema50_min_bars ?? 45}
                onChange={e => up('wheel_timing', 'ema50_min_bars', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>允许不足周期近似 EMA</label>
              <select value={cfg.wheel_timing.allow_partial_ema !== false ? '1' : '0'}
                onChange={e => up('wheel_timing', 'allow_partial_ema', e.target.value === '1')}>
                <option value="1">开启</option>
                <option value="0">关闭</option>
              </select>
            </div>
            <div className="settings-row">
              <label>每标的合约上限(0=不限)</label>
              <input type="number" value={cfg.wheel_timing.contract_max_per_symbol}
                onChange={e => up('wheel_timing', 'contract_max_per_symbol', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>IV 分位硬条件(0=仅记录)</label>
              <input type="number" value={cfg.wheel_timing.iv_percentile_threshold}
                onChange={e => up('wheel_timing', 'iv_percentile_threshold', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>合约冷却(交易日)</label>
              <input type="number" value={cfg.wheel_timing.cooldown_trading_days}
                onChange={e => up('wheel_timing', 'cooldown_trading_days', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>自动扫描间隔(分钟,0=关)</label>
              <input type="number" value={cfg.wheel_timing.auto_scan_minutes}
                onChange={e => up('wheel_timing', 'auto_scan_minutes', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>TG 仅推可做/强信号</label>
              <select value={cfg.wheel_timing.push_strong_only !== false ? '1' : '0'}
                onChange={e => up('wheel_timing', 'push_strong_only', e.target.value === '1')}>
                <option value="1">开启</option>
                <option value="0">关闭(全部推)</option>
              </select>
            </div>
            <div className="settings-row">
              <label>强信号 IVR 阈值</label>
              <input type="number" value={cfg.wheel_timing.push_min_iv_rank ?? 50}
                onChange={e => up('wheel_timing', 'push_min_iv_rank', Number(e.target.value))} />
            </div>
          </div>

          <div className="editor-section">
            <h4>高分扫描（截面打分）</h4>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              机会里的「高分 / 优先」来源。按规则筛合约再综合打分。
            </p>
            <div className="settings-row">
              <label>跨标的展示条数</label>
              <input type="number" value={cfg.wheel_scan?.top_overall ?? 15}
                onChange={e => upScan('top_overall', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>TG 推送 Top N</label>
              <input type="number" value={cfg.wheel_scan?.telegram_top_n ?? 3}
                onChange={e => upScan('telegram_top_n', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>高分自动推送(分钟,0=关)</label>
              <input type="number" value={cfg.wheel_scan?.auto_push_minutes ?? 0}
                onChange={e => upScan('auto_push_minutes', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>点差上限%(硬过滤)</label>
              <input type="number" value={cfg.wheel_scan?.max_spread_pct ?? 10}
                onChange={e => upScan('max_spread_pct', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>排序模式</label>
              <select value={cfg.wheel_scan?.sort_mode || 'score'}
                onChange={e => upScan('sort_mode', e.target.value)}>
                <option value="score">综合分 score</option>
                <option value="robust">稳健分 robust</option>
              </select>
            </div>
          </div>

          <div className="editor-section">
            <h4>持仓管理</h4>
            <div className="settings-row">
              <label>平仓利润目标(%)</label>
              <input type="number" value={cfg.wheel_position.profit_target_pct}
                onChange={e => up('wheel_position', 'profit_target_pct', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>软止盈阈值(%)</label>
              <input type="number" value={cfg.wheel_position.soft_profit_pct ?? 30}
                onChange={e => up('wheel_position', 'soft_profit_pct', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>硬 Roll DTE</label>
              <input type="number" value={cfg.wheel_position.hard_roll_dte ?? 21}
                onChange={e => up('wheel_position', 'hard_roll_dte', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>保证金估算比例(0~1)</label>
              <input type="number" step="0.05" value={cfg.wheel_position.margin_ratio}
                onChange={e => up('wheel_position', 'margin_ratio', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>财报警示提前天数</label>
              <input type="number" value={cfg.wheel_position.earnings_warn_days}
                onChange={e => up('wheel_position', 'earnings_warn_days', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>每周一 TG 周报</label>
              <select value={cfg.wheel_position.weekly_report ? '1' : '0'}
                onChange={e => up('wheel_position', 'weekly_report', e.target.value === '1')}>
                <option value="1">开启</option>
                <option value="0">关闭</option>
              </select>
            </div>
          </div>

          <NotificationCenter
            cfg={cfg}
            up={up}
            upScan={upScan}
            upAlerts={upAlerts}
            onMessage={(t) => { setMsg(t); onMessage(t) }}
          />

          <div className="editor-section">
            <h4>组合风控</h4>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              <b>组合净值</b>是账户总预算的<b>唯一入口</b>：Wheel 首页可用资金/建议张数、体检资金紧、
              优化页利用率均读此值。请勿在标的页另设「组合预算」。
              单标的上限在 <b>Wheel → 标的</b> 的 max_capital。
            </p>
            <div className="settings-row">
              <label>组合净值 / 预算(USD,0=未设)</label>
              <input type="number" step="1000" value={cfg.wheel_portfolio?.total_equity ?? 0}
                placeholder="例如 300000"
                title="唯一组合预算;保存后同步到 Wheel 首页"
                onChange={e => up('wheel_portfolio', 'total_equity', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>组合占用上限(0~1)</label>
              <input type="number" step="0.05" min={0} max={1}
                value={cfg.wheel_portfolio?.max_portfolio_pct ?? 0.8}
                onChange={e => up('wheel_portfolio', 'max_portfolio_pct', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>单标的占净值上限(0~1)</label>
              <input type="number" step="0.05" min={0} max={1}
                value={cfg.wheel_portfolio?.max_symbol_pct ?? 0.25}
                onChange={e => up('wheel_portfolio', 'max_symbol_pct', Number(e.target.value))} />
            </div>
            <div className="settings-row">
              <label>高相关阈值</label>
              <input type="number" step="0.05" min={0} max={1}
                value={cfg.wheel_portfolio?.high_corr_threshold ?? 0.7}
                onChange={e => up('wheel_portfolio', 'high_corr_threshold', Number(e.target.value))} />
            </div>
          </div>
        </>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
        <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          保存后立即生效 · 存本地数据库
        </span>
        <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? '保存中…' : mode === 'wheel' ? '保存 Wheel 配置' : '保存通用配置'}
        </button>
      </div>
    </div>
  )
}

// ── 通知中心(N1–N5 聚合设置 + 样例 + 测试 + 日志) ────────────────────────────
function NotificationCenter({
  cfg,
  up,
  upScan,
  upAlerts,
  onMessage,
}: {
  cfg: BackendConfig
  up: (section: keyof BackendConfig, key: string, value: any) => void
  upScan: (key: string, value: any) => void
  upAlerts: (key: string, value: any) => void
  onMessage: (m: string) => void
}) {
  const alerts = cfg.wheel_alerts || {}
  const [logs, setLogs] = useState<WheelPushLogItem[]>([])
  const [preview, setPreview] = useState<{ position: string; scan: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [showPreview, setShowPreview] = useState(false)

  function refreshLogs() {
    getWheelAlertLog({ limit: 30 })
      .then(r => setLogs(r.items || []))
      .catch(() => setLogs([]))
  }

  useEffect(() => {
    refreshLogs()
    getWheelAlertPreview()
      .then(r => setPreview({ position: r.position, scan: r.scan }))
      .catch(() => setPreview(null))
  }, [])

  async function handleTest(kind: 'position' | 'scan' | 'ping') {
    setBusy(true)
    try {
      const r = await testWheelAlert(kind)
      if (r.sent || r.ok) {
        onMessage(`测试推送已发送(${kind})`)
      } else {
        onMessage(`测试未发出: ${r.reason || '未知'}`)
      }
      refreshLogs()
    } catch (e: any) {
      onMessage(`测试失败: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const statusColor = (s: string) => {
    if (s === 'sent') return 'var(--green, #3d9)'
    if (s === 'failed') return 'var(--red, #e55)'
    return 'var(--text-secondary)'
  }

  return (
    <div className="editor-section">
      <h4>通知中心</h4>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
        管仓告警=事件变化才推(去重/冷却)；机会推送=TopN+门槛+合约去重。
        组合闸门停 Put 时不会推无效 Put 机会。静默时段内仅紧急(深ITM/接货)可破。
      </p>

      <div className="settings-row">
        <label>管仓轮询(分钟,0=关)</label>
        <input type="number" min={0}
          value={cfg.wheel_position.alert_push_minutes ?? 0}
          onChange={e => up('wheel_position', 'alert_push_minutes', Number(e.target.value))}
          title="多久检查一次持仓;同状态不会重复刷屏" />
      </div>
      <div className="settings-row">
        <label>持仓通知模式</label>
        <select value={cfg.wheel_position.notify_mode || 'realtime'}
          onChange={e => up('wheel_position', 'notify_mode', e.target.value)}>
          <option value="realtime">即时(状态变化推)</option>
          <option value="digest">每日汇总(紧急仍即时)</option>
        </select>
      </div>
      <div className="settings-row">
        <label>普通冷却(小时)</label>
        <input type="number" step="0.5" min={0}
          value={alerts.position_cooldown_hours ?? 6}
          onChange={e => upAlerts('position_cooldown_hours', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>紧急冷却(小时)</label>
        <input type="number" step="0.5" min={0}
          value={alerts.position_urgent_cooldown_hours ?? 2}
          onChange={e => upAlerts('position_urgent_cooldown_hours', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>优先级上限(≤推)</label>
        <input type="number" min={1} max={9}
          value={alerts.position_priority_max ?? 3}
          onChange={e => upAlerts('position_priority_max', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>静默开始时(0-23)</label>
        <input type="number" min={0} max={23}
          value={alerts.quiet_hours_start ?? 22}
          onChange={e => upAlerts('quiet_hours_start', Number(e.target.value))}
          title="与结束相同=关闭静默" />
      </div>
      <div className="settings-row">
        <label>静默结束时(0-23)</label>
        <input type="number" min={0} max={23}
          value={alerts.quiet_hours_end ?? 7}
          onChange={e => upAlerts('quiet_hours_end', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>静默仍推紧急</label>
        <select value={alerts.quiet_hours_allow_urgent === false ? '0' : '1'}
          onChange={e => upAlerts('quiet_hours_allow_urgent', e.target.value === '1')}>
          <option value="1">是(推荐)</option>
          <option value="0">否</option>
        </select>
      </div>
      <div className="settings-row">
        <label>Digest 发送时</label>
        <input type="number" min={0} max={23}
          value={alerts.digest_hour ?? 9}
          onChange={e => upAlerts('digest_hour', Number(e.target.value))} />
      </div>

      <div style={{ height: 8 }} />
      <div className="settings-row">
        <label>高分自动推送(分钟,0=关)</label>
        <input type="number" value={cfg.wheel_scan?.auto_push_minutes ?? 0}
          onChange={e => upScan('auto_push_minutes', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>TG 推送 Top N</label>
        <input type="number" min={1} max={20}
          value={cfg.wheel_scan?.telegram_top_n ?? 5}
          onChange={e => upScan('telegram_top_n', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>机会最低分</label>
        <input type="number" step="0.1" min={0}
          value={alerts.scan_min_score ?? 0}
          onChange={e => upAlerts('scan_min_score', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>机会最低年化%</label>
        <input type="number" step="1" min={0}
          value={alerts.scan_min_annualized ?? 0}
          onChange={e => upAlerts('scan_min_annualized', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>机会去重(小时)</label>
        <input type="number" step="0.5" min={0}
          value={alerts.scan_dedupe_hours ?? 12}
          onChange={e => upAlerts('scan_dedupe_hours', Number(e.target.value))} />
      </div>
      <div className="settings-row">
        <label>闸门停 Put 时</label>
        <select value={alerts.scan_skip_blocked_puts === false ? '0' : '1'}
          onChange={e => upAlerts('scan_skip_blocked_puts', e.target.value === '1')}>
          <option value="1">不推 Put(推荐)</option>
          <option value="0">仍推(仅标注)</option>
        </select>
      </div>
      <div className="settings-row">
        <label>仅推新机会</label>
        <select value={alerts.scan_only_new === false ? '0' : '1'}
          onChange={e => upAlerts('scan_only_new', e.target.value === '1')}>
          <option value="1">是(去重)</option>
          <option value="0">否(每轮全推 Top)</option>
        </select>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '12px 0 8px' }}>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
          onClick={() => handleTest('ping')}>测试连通</button>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
          onClick={() => handleTest('position')}>测管仓样例</button>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
          onClick={() => handleTest('scan')}>测机会样例</button>
        <button type="button" className="btn btn-secondary btn-sm"
          onClick={() => setShowPreview(v => !v)}>
          {showPreview ? '收起样例' : '预览短模板'}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={refreshLogs}>
          刷新日志
        </button>
      </div>

      {showPreview && preview && (
        <div style={{
          display: 'grid', gap: 8, marginBottom: 12,
          fontSize: 12, fontFamily: 'ui-monospace, monospace',
          whiteSpace: 'pre-wrap',
        }}>
          <div style={{
            padding: 10, borderRadius: 8,
            background: 'var(--bg-secondary, #1a1a1a)', border: '1px solid var(--border, #333)',
          }}>
            <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>管仓样例</div>
            {preview.position}
          </div>
          <div style={{
            padding: 10, borderRadius: 8,
            background: 'var(--bg-secondary, #1a1a1a)', border: '1px solid var(--border, #333)',
          }}>
            <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>机会样例</div>
            {preview.scan}
          </div>
        </div>
      )}

      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
        最近推送
      </div>
      {logs.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>暂无记录</div>
      ) : (
        <div style={{
          maxHeight: 220, overflow: 'auto', fontSize: 12,
          border: '1px solid var(--border, #333)', borderRadius: 8,
        }}>
          {logs.map(row => (
            <div key={row.id} style={{
              padding: '8px 10px',
              borderBottom: '1px solid var(--border, #333)',
              display: 'grid',
              gridTemplateColumns: '72px 70px 1fr',
              gap: 8,
              alignItems: 'start',
            }}>
              <span style={{ color: statusColor(row.status) }}>{row.status}</span>
              <span style={{ color: 'var(--text-secondary)' }}>{row.category}</span>
              <span>
                <div style={{ color: 'var(--text-secondary)' }}>
                  {(row.created_at || '').replace('T', ' ').slice(0, 19)}
                  {row.reason ? ` · ${row.reason}` : ''}
                </div>
                <div style={{
                  whiteSpace: 'pre-wrap', marginTop: 2,
                  maxHeight: 60, overflow: 'hidden',
                }}>
                  {(row.title || row.body || '').slice(0, 160)}
                </div>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
