import { useEffect, useState } from 'react'
import {
  AppSettings,
  BackendConfig,
  connectTrading,
  disconnectTrading,
  getAppSettings,
  getBackendConfig,
  getMarketStatus,
  saveAppSettings,
  saveBackendConfig,
} from '../services/api'

export default function SettingsPage() {
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
    setMessage('设置已保存')
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

  return (
    <div className="page">
      <h2>设置</h2>

      {message && (
        <div className="card" style={{ border: '1px solid #2d6cdf', color: '#cde4ff' }}>
          {message}
        </div>
      )}

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
          <h4>行情数据源</h4>
          <div style={{ marginBottom: 8, color: 'var(--text-secondary)', fontSize: 12 }}>
            行情页/策略实时信号默认按资产自动路由：美股 quote→Finnhub，A股/港股 quote→Futu，美股 K 线→Yahoo，A股/港股 K 线→Futu。下面这个选项主要作为调试/兼容入口保留。
          </div>
          <div className="settings-row">
            <label>默认入口（调试）</label>
            <select value={settings.marketDataSource} onChange={e => updateField('marketDataSource', e.target.value as AppSettings['marketDataSource'])}>
              <option value="futu">Futu OpenD</option>
              <option value="finnhub">Finnhub</option>
            </select>
          </div>
          <div className="settings-row">
            <label>OpenD Host</label>
            <input value={settings.marketHost} onChange={e => updateField('marketHost', e.target.value)} disabled={settings.marketDataSource === 'finnhub'} />
          </div>
          <div className="settings-row">
            <label>OpenD Port</label>
            <input type="number" value={settings.marketPort} onChange={e => updateField('marketPort', Number(e.target.value))} disabled={settings.marketDataSource === 'finnhub'} />
          </div>
          {settings.marketDataSource === 'finnhub' && (
            <div style={{ marginTop: 8, color: 'var(--text-secondary)', fontSize: 12 }}>
              Finnhub 模式下，后端会使用下方「数据源」卡片中保存到本地数据库的 API Key。
            </div>
          )}
          <div className="status-row">
            <span>当前状态</span>
            <span className={`tag ${marketStatus?.connected ? 'ready' : 'draft'}`}>
              {marketStatus?.connected ? `已连接 (${marketStatus.adapter})` : '未测试'}
            </span>
          </div>
          <div style={{ marginTop: 12 }}>
            <button className="btn-outline" onClick={handleTestMarket} disabled={testingMarket}>
              {testingMarket ? '测试中...' : '测试行情源'}
            </button>
          </div>
        </div>

        <div className="editor-section">
          <h4>交易连接（富途前端集成）</h4>
          <div className="settings-row">
            <label>交易适配器</label>
            <select value={settings.tradingAdapter} onChange={e => updateField('tradingAdapter', e.target.value as AppSettings['tradingAdapter'])}>
              <option value="futu">Futu</option>
            </select>
          </div>
          <div className="settings-row">
            <label>交易环境</label>
            <select value={settings.tradingEnv} onChange={e => updateField('tradingEnv', e.target.value as AppSettings['tradingEnv'])}>
              <option value="SIM">模拟盘</option>
              <option value="REAL">实盘</option>
            </select>
          </div>
          <div className="settings-row">
            <label>交易 Host</label>
            <input value={settings.tradingHost} onChange={e => updateField('tradingHost', e.target.value)} />
          </div>
          <div className="settings-row">
            <label>交易 Port</label>
            <input type="number" value={settings.tradingPort} onChange={e => updateField('tradingPort', Number(e.target.value))} />
          </div>
          <div className="settings-row">
            <label>默认下单数量</label>
            <input type="number" value={settings.defaultOrderQuantity} onChange={e => updateField('defaultOrderQuantity', Number(e.target.value))} />
          </div>
          <div className="status-row">
            <span>交易状态</span>
            <span className={`tag ${tradingConnected ? 'ready' : 'draft'}`}>
              {tradingConnected ? `已连接 (${settings.tradingAdapter})` : '未连接'}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
            <button className="btn" onClick={handleConnectTrading} disabled={connectingTrading}>
              {connectingTrading ? '连接中...' : '连接交易'}
            </button>
            <button className="btn-outline" onClick={handleDisconnectTrading} disabled={connectingTrading}>
              断开交易
            </button>
          </div>
        </div>

        <div className="editor-section">
          <h4>下单确认</h4>
          <div className="settings-row">
            <label>信号触发后确认弹窗</label>
            <select value={settings.confirmSignals ? 'yes' : 'no'} onChange={e => updateField('confirmSignals', e.target.value === 'yes')}>
              <option value="yes">开启</option>
              <option value="no">关闭</option>
            </select>
          </div>
        </div>

        <div className="editor-section">
          <h4>显示</h4>
          <div className="settings-row">
            <label>主题</label>
            <select value={settings.theme} onChange={e => updateField('theme', e.target.value)}>
              <option value="dark">深色</option>
              <option value="light">浅色</option>
            </select>
          </div>
          <div className="settings-row">
            <label>语言</label>
            <select value={settings.language} onChange={e => updateField('language', e.target.value)}>
              <option value="zh">简体中文</option>
              <option value="en">English</option>
            </select>
          </div>
        </div>

        <div style={{ textAlign: 'right' }}>
          <button className="btn" onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存设置'}
          </button>
        </div>
      </div>

      <BackendConfigCard />
    </div>
  )
}

// ── 后端服务配置(存本地数据库,不进代码仓库)────────────────────────────────────
function BackendConfigCard() {
  const [cfg, setCfg] = useState<BackendConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    getBackendConfig().then(setCfg).catch(e => setMsg('加载后端配置失败:' + e.message))
  }, [])

  function up<K extends keyof BackendConfig>(section: K, key: string, value: any) {
    setCfg(prev => prev ? {
      ...prev,
      [section]: typeof prev[section] === 'object'
        ? { ...(prev[section] as any), [key]: value }
        : value,
    } : prev)
  }

  async function save() {
    if (!cfg) return
    setSaving(true)
    setMsg('')
    try {
      setCfg(await saveBackendConfig(cfg))
      setMsg('后端配置已保存并立即生效(存本地数据库)')
      setTimeout(() => setMsg(''), 3000)
    } catch (e: any) {
      setMsg('保存失败:' + e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!cfg) return <div className="card">{msg || '正在加载后端配置...'}</div>

  return (
    <div className="card">
      {msg && <div style={{ marginBottom: 10, color: '#cde4ff', fontSize: 13 }}>{msg}</div>}

      <div className="editor-section">
        <h4>通知与数据源(敏感信息,仅存本地数据库)</h4>
        <div className="settings-row">
          <label>Telegram Bot Token</label>
          <input type="password" value={cfg.telegram.bot_token} placeholder="123456:ABC-DEF..."
            onChange={e => up('telegram', 'bot_token', e.target.value)} />
        </div>
        <div className="settings-row">
          <label>Telegram Chat ID</label>
          <input value={cfg.telegram.chat_id} placeholder="-100xxxx 或用户ID"
            onChange={e => up('telegram', 'chat_id', e.target.value)} />
        </div>
        <div className="settings-row">
          <label>Finnhub API Key(报价/财报日历)</label>
          <input type="password" value={cfg.finnhub_api_key}
            onChange={e => setCfg(p => p ? { ...p, finnhub_api_key: e.target.value } : p)} />
        </div>
        <div className="settings-row">
          <label>Finnhub Base URL</label>
          <input value={cfg.finnhub_base_url} placeholder="https://finnhub.io/api/v1"
            onChange={e => setCfg(p => p ? { ...p, finnhub_base_url: e.target.value } : p)} />
        </div>
        <div className="settings-row">
          <label>Yahoo Finance Base URL(美股K线)</label>
          <input value={cfg.yahoo_base_url} placeholder="https://query1.finance.yahoo.com/v8/finance/chart"
            onChange={e => setCfg(p => p ? { ...p, yahoo_base_url: e.target.value } : p)} />
        </div>
        <div className="settings-row">
          <label>后台任务 OpenD Host</label>
          <input value={cfg.futu.host} onChange={e => up('futu', 'host', e.target.value)} />
        </div>
        <div className="settings-row">
          <label>后台任务 OpenD Port</label>
          <input type="number" value={cfg.futu.port} onChange={e => up('futu', 'port', Number(e.target.value))} />
        </div>
      </div>

      <div className="editor-section">
        <h4>Wheel 时机扫描</h4>
        <div className="settings-row">
          <label>合约 DTE 范围(天)</label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="number" style={{ width: 90 }} value={cfg.wheel_timing.dte_min}
              onChange={e => up('wheel_timing', 'dte_min', Number(e.target.value))} />
            ~
            <input type="number" style={{ width: 90 }} value={cfg.wheel_timing.dte_max}
              onChange={e => up('wheel_timing', 'dte_max', Number(e.target.value))} />
          </div>
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
          <label>自动扫描间隔(分钟,0=关闭)</label>
          <input type="number" value={cfg.wheel_timing.auto_scan_minutes}
            onChange={e => up('wheel_timing', 'auto_scan_minutes', Number(e.target.value))} />
        </div>
      </div>

      <div className="editor-section">
        <h4>Wheel 持仓管理</h4>
        <div className="settings-row">
          <label>平仓利润目标(%)</label>
          <input type="number" value={cfg.wheel_position.profit_target_pct}
            onChange={e => up('wheel_position', 'profit_target_pct', Number(e.target.value))} />
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
          <label>每周一 Telegram 周报</label>
          <select value={cfg.wheel_position.weekly_report ? '1' : '0'}
            onChange={e => up('wheel_position', 'weekly_report', e.target.value === '1')}>
            <option value="1">开启</option>
            <option value="0">关闭</option>
          </select>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          保存后立即生效,无需重启;所有配置均存本地数据库,不再读取任何配置文件
        </span>
        <button className="btn" onClick={save} disabled={saving}>
          {saving ? '保存中...' : '保存后端配置'}
        </button>
      </div>
    </div>
  )
}
