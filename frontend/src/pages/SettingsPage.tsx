import { useState } from 'react'
import {
  AppSettings,
  connectTrading,
  disconnectTrading,
  getAppSettings,
  getMarketStatus,
  saveAppSettings,
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
              Finnhub 模式下，后端会读取环境变量 <code>FINNHUB_API_KEY</code>。
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
    </div>
  )
}
