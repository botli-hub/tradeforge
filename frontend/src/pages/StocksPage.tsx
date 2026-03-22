import { useEffect, useState } from 'react'
import {
  StockItem,
  getStocks,
  addStock,
  deleteStock,
  setStockEnabled,
  setStockSubscribed,
} from '../services/api'

const MARKET_LABEL: Record<string, string> = { US: '美股', HK: '港股', CN: 'A股' }
const MARKET_OPTS = ['ALL', 'US', 'HK', 'CN']

const EMPTY_FORM = { symbol: '', name: '', market: 'US' }

export default function StocksPage() {
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [filter, setFilter] = useState<string>('ALL')
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [adding, setAdding] = useState(false)

  async function load() {
    try {
      const data = await getStocks()
      setStocks(data)
    } catch (e: any) {
      setError(e.message ?? '加载失败')
    }
  }

  useEffect(() => { load() }, [])

  function setItemBusy(symbol: string, val: boolean) {
    setBusy(b => ({ ...b, [symbol]: val }))
  }

  async function toggleEnabled(s: StockItem) {
    setItemBusy(s.symbol, true)
    try {
      await setStockEnabled(s.symbol, !s.enabled)
      setStocks(prev => prev.map(x => x.symbol === s.symbol ? { ...x, enabled: !s.enabled } : x))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setItemBusy(s.symbol, false)
    }
  }

  async function toggleSubscribed(s: StockItem) {
    setItemBusy(s.symbol, true)
    try {
      await setStockSubscribed(s.symbol, !s.subscribed)
      setStocks(prev => prev.map(x => x.symbol === s.symbol ? { ...x, subscribed: !s.subscribed } : x))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setItemBusy(s.symbol, false)
    }
  }

  async function handleDelete(s: StockItem) {
    if (!confirm(`确认删除 ${s.symbol}？`)) return
    setItemBusy(s.symbol, true)
    try {
      await deleteStock(s.symbol)
      setStocks(prev => prev.filter(x => x.symbol !== s.symbol))
    } catch (e: any) {
      setError(e.message)
    } finally {
      setItemBusy(s.symbol, false)
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    if (!form.symbol.trim() || !form.name.trim()) return
    setAdding(true)
    try {
      const item = await addStock({ symbol: form.symbol.trim().toUpperCase(), name: form.name.trim(), market: form.market })
      setStocks(prev => [...prev, item])
      setForm(EMPTY_FORM)
      setShowAdd(false)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setAdding(false)
    }
  }

  const displayed = filter === 'ALL' ? stocks : stocks.filter(s => s.market === filter)

  const grouped = displayed.reduce<Record<string, StockItem[]>>((acc, s) => {
    ;(acc[s.market] = acc[s.market] ?? []).push(s)
    return acc
  }, {})

  const marketOrder = ['US', 'HK', 'CN']

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
        <h2>股票池管理</h2>
        <button className="btn" onClick={() => setShowAdd(v => !v)}>
          {showAdd ? '取消' : '+ 新增股票'}
        </button>
      </div>

      {error && (
        <div className="card" style={{ border: '1px solid rgba(233,69,96,0.35)', color: '#ffb0ba', marginBottom: 16 }}>
          {error}
          <button className="btn-outline" style={{ marginLeft: 12 }} onClick={() => setError('')}>关闭</button>
        </div>
      )}

      {showAdd && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginBottom: 12 }}>新增股票</h3>
          <form onSubmit={handleAdd} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div className="option-field">
              <label>代码</label>
              <input
                placeholder="AAPL / 00700.HK / 600519.SH"
                value={form.symbol}
                onChange={e => setForm(f => ({ ...f, symbol: e.target.value }))}
                style={{ width: 160 }}
              />
            </div>
            <div className="option-field">
              <label>名称</label>
              <input
                placeholder="公司名称"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                style={{ width: 140 }}
              />
            </div>
            <div className="option-field">
              <label>市场</label>
              <select value={form.market} onChange={e => setForm(f => ({ ...f, market: e.target.value }))}>
                <option value="US">美股</option>
                <option value="HK">港股</option>
                <option value="CN">A股</option>
              </select>
            </div>
            <button className="btn" type="submit" disabled={adding}>{adding ? '添加中...' : '添加'}</button>
          </form>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>市场筛选：</span>
          {MARKET_OPTS.map(m => (
            <button
              key={m}
              className={filter === m ? 'btn' : 'btn-outline'}
              style={{ padding: '4px 14px', fontSize: 13 }}
              onClick={() => setFilter(m)}
            >
              {m === 'ALL' ? '全部' : MARKET_LABEL[m]}
            </button>
          ))}
          <span style={{ marginLeft: 'auto', color: 'var(--text-secondary)', fontSize: 13 }}>
            共 {displayed.length} 只 · 已启用 {displayed.filter(s => s.enabled).length} · 已订阅 {displayed.filter(s => s.subscribed).length}
          </span>
        </div>
      </div>

      {marketOrder.map(market => {
        const rows = grouped[market]
        if (!rows?.length) return null
        return (
          <div key={market} className="card" style={{ marginBottom: 16 }}>
            <h3 style={{ marginBottom: 12 }}>{MARKET_LABEL[market]}</h3>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <thead>
                <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>
                  <th style={{ textAlign: 'left', padding: '6px 8px' }}>代码</th>
                  {market !== 'US' && <th style={{ textAlign: 'left', padding: '6px 8px' }}>名称</th>}
                  <th style={{ textAlign: 'center', padding: '6px 8px' }}>状态</th>
                  <th style={{ textAlign: 'center', padding: '6px 8px' }}>订阅</th>
                  <th style={{ textAlign: 'center', padding: '6px 8px' }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(s => (
                  <tr key={s.symbol} style={{ borderBottom: '1px solid var(--border)', opacity: s.enabled ? 1 : 0.45 }}>
                    <td style={{ padding: '8px 8px', fontFamily: 'monospace' }}>{s.symbol}</td>
                    {market !== 'US' && <td style={{ padding: '8px 8px' }}>{s.name}</td>}
                    <td style={{ textAlign: 'center', padding: '8px 8px' }}>
                      <span className={`tag ${s.enabled ? 'ready' : 'draft'}`}>
                        {s.enabled ? '启用' : '禁用'}
                      </span>
                    </td>
                    <td style={{ textAlign: 'center', padding: '8px 8px' }}>
                      <span className={`tag ${s.subscribed ? 'positive' : 'draft'}`}>
                        {s.subscribed ? '已订阅' : '未订阅'}
                      </span>
                    </td>
                    <td style={{ textAlign: 'center', padding: '8px 8px' }}>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'center', flexWrap: 'wrap' }}>
                        <button
                          className="btn-outline"
                          style={{ fontSize: 12, padding: '3px 10px' }}
                          disabled={busy[s.symbol]}
                          onClick={() => toggleEnabled(s)}
                        >
                          {s.enabled ? '禁用' : '启用'}
                        </button>
                        <button
                          className="btn-outline"
                          style={{ fontSize: 12, padding: '3px 10px' }}
                          disabled={busy[s.symbol]}
                          onClick={() => toggleSubscribed(s)}
                        >
                          {s.subscribed ? '取消订阅' : '订阅'}
                        </button>
                        <button
                          className="btn-outline"
                          style={{ fontSize: 12, padding: '3px 10px', color: '#e94560', borderColor: '#e94560' }}
                          disabled={busy[s.symbol]}
                          onClick={() => handleDelete(s)}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}
    </div>
  )
}
