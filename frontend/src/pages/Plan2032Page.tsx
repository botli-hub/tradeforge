import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getPlan2032Holdings,
  getQuotes,
  getStocks,
  savePlan2032Holdings,
  type Plan2032Holding,
  type StockItem,
} from '../services/api'

type PlanTab = 'overview' | 'holdings' | 'advisor'
type AdvisorTab = 'valuation' | 'events' | 'macro' | 'discipline'
type CategoryKey = 'defensive' | 'growth' | 'aggressive' | 'satellite'
type Currency = 'USD' | 'HKD' | 'CNY'

type HoldingDraft = Omit<Plan2032Holding, 'id' | 'category'> & { id: string; category: CategoryKey }

type QuoteState = {
  price: number | null
  changePct?: number
  loading?: boolean
  error?: string
  adapter?: string
}

type CategoryMeta = {
  name: string
  color: string
  light: string
  targetRange: [number, number]
}

const USD_CNY = 7.25
const HKD_CNY = 0.925
const YEARS = 6.75
const QUOTE_REFRESH_MS = 3600000
const QUOTE_CACHE_TTL_MS = 3600000

const CATEGORY_META: Record<CategoryKey, CategoryMeta> = {
  defensive: { name: '防御压舱石', color: '#00C805', light: 'rgba(0, 200, 5, 0.10)', targetRange: [0.3, 0.35] },
  growth: { name: '成长主轴', color: '#3b82f6', light: 'rgba(59, 130, 246, 0.10)', targetRange: [0.4, 0.45] },
  aggressive: { name: '进攻性成长', color: '#f97316', light: 'rgba(249, 115, 22, 0.10)', targetRange: [0.15, 0.2] },
  satellite: { name: '卫星期权', color: '#8b5cf6', light: 'rgba(139, 92, 246, 0.10)', targetRange: [0.03, 0.05] },
}

const DEFAULT_HOLDINGS: HoldingDraft[] = [
  { id: 'NVDA', symbol: 'NVDA', name: '英伟达', shares: 88, target2032: 525, dividend_yield: 0.001, category: 'growth', pe: 22, moat: 'CUDA软件生态', risk: 2, note: 'AI算力基础设施', currency: 'USD' },
  { id: 'AAPL', symbol: 'AAPL', name: '苹果', shares: 100, target2032: 522, dividend_yield: 0.005, category: 'growth', pe: 29, moat: '生态系统+品牌', risk: 2, note: '22亿台活跃设备数字生态', currency: 'USD' },
  { id: 'TSLA', symbol: 'TSLA', name: '特斯拉', shares: 100, target2032: 940, dividend_yield: 0, category: 'aggressive', pe: 290, moat: '数据+垂直整合', risk: 4, note: 'EV/AI/机器人三重期权', currency: 'USD' },
  { id: 'CNOOC', symbol: '00883.HK', name: '中国海油', shares: 10000, target2032: 33, dividend_yield: 0.06, category: 'defensive', pe: 6, moat: '低成本+国家战略', risk: 2, note: '桶油成本低，高股息', currency: 'HKD' },
  { id: '600900.SH', symbol: '600900.SH', name: '长江电力', shares: 6000, target2032: 38.7, dividend_yield: 0.04, category: 'defensive', pe: 17, moat: '国家垄断水电', risk: 1, note: '全球最大水电上市公司', currency: 'CNY' },
]

function inferCurrency(symbol: string): Currency {
  const upper = symbol.toUpperCase()
  if (upper.endsWith('.HK')) return 'HKD'
  if (upper.endsWith('.SH') || upper.endsWith('.SZ')) return 'CNY'
  return 'USD'
}

function normalizeSymbol(symbol: string) {
  const upper = symbol.trim().toUpperCase()
  if (/^\d{6}$/.test(upper)) {
    return upper.startsWith('6') ? `${upper}.SH` : `${upper}.SZ`
  }
  return upper
}

function toCny(price: number, currency: Currency) {
  if (currency === 'USD') return price * USD_CNY
  if (currency === 'HKD') return price * HKD_CNY
  return price
}

function formatPct(value: number, digits = 1) {
  return `${(value * 100).toFixed(digits)}%`
}

function formatCurrency(value: number) {
  return `¥${Math.round(value).toLocaleString('zh-CN')}`
}

function formatCompact(value: number) {
  if (Math.abs(value) >= 1_000_000) return `¥${(value / 1_000_000).toFixed(2)}M`
  if (Math.abs(value) >= 10_000) return `¥${(value / 10_000).toFixed(1)}万`
  return formatCurrency(value)
}

function calcCagr(current: number, target: number) {
  if (!current || !target || current <= 0 || target <= 0) return 0
  return Math.pow(target / current, 1 / YEARS) - 1
}

function toDraft(holding: Plan2032Holding, fallbackId?: string): HoldingDraft {
  const symbol = normalizeSymbol(holding.symbol)
  return {
    ...holding,
    id: String(holding.id ?? fallbackId ?? symbol),
    symbol,
    currency: holding.currency || inferCurrency(symbol),
    dividend_yield: holding.dividend_yield ?? 0,
    category: (holding.category as CategoryKey) || 'growth',
    moat: holding.moat || '',
    note: holding.note || '',
    risk: holding.risk ?? 3,
  }
}

function Plan2032Page() {
  const [tab, setTab] = useState<PlanTab>('overview')
  const [advisorTab, setAdvisorTab] = useState<AdvisorTab>('valuation')
  const [holdings, setHoldings] = useState<HoldingDraft[]>(DEFAULT_HOLDINGS)
  const [quotes, setQuotes] = useState<Record<string, QuoteState>>({})
  const quoteCacheRef = useRef<Record<string, { at: number; data: QuoteState }>>({})
  const quoteBatchRunningRef = useRef(false)
  const [watchlist, setWatchlist] = useState<StockItem[]>([])
  const [selectedStock, setSelectedStock] = useState('')
  const [holdingSort, setHoldingSort] = useState<'symbol' | 'position' | 'cagr'>('cagr')
  const [sp500Pe, setSp500Pe] = useState(22)
  const [vix, setVix] = useState(20)
  const [eventSymbol, setEventSymbol] = useState('')
  const [eventType, setEventType] = useState('')
  const [macroCycle, setMacroCycle] = useState('')
  const [saveStatus, setSaveStatus] = useState('')
  const refreshQuotesRef = useRef<((force?: boolean) => Promise<void>) | null>(null)
  const [quoteRefreshAt, setQuoteRefreshAt] = useState('')

  useEffect(() => {
    getStocks()
      .then(setWatchlist)
      .catch(() => setWatchlist([]))

    getPlan2032Holdings()
      .then((rows) => {
        if (rows.length > 0) {
          setHoldings(rows.map((row, index) => toDraft(row, `db-${index}`)))
        }
      })
      .catch(() => {
        setHoldings(DEFAULT_HOLDINGS)
      })
  }, [])

  useEffect(() => {
    let cancelled = false
    const symbols = [...new Set(holdings.map((item) => normalizeSymbol(item.symbol)).filter(Boolean))]
    if (symbols.length === 0) return

    async function refreshBatch(force = false) {
      if (quoteBatchRunningRef.current) return
      quoteBatchRunningRef.current = true

      const now = Date.now()
      const symbolsToFetch: string[] = []
      const cachedQuotes: Record<string, QuoteState> = {}

      for (const symbol of symbols) {
        const cached = quoteCacheRef.current[symbol]
        if (!force && cached && now - cached.at < QUOTE_CACHE_TTL_MS) {
          cachedQuotes[symbol] = cached.data
        } else {
          symbolsToFetch.push(symbol)
        }
      }

      setQuotes((prev) => {
        const next = { ...prev, ...cachedQuotes }
        for (const symbol of symbolsToFetch) {
          next[symbol] = { ...(prev[symbol] || {}), loading: true, error: undefined }
        }
        return next
      })

      try {
        if (symbolsToFetch.length === 0) return
        const response = await getQuotes(symbolsToFetch)
        if (cancelled) return

        const mapped: Record<string, QuoteState> = {}
        const returnedSymbols = new Set<string>()
        for (const quote of response.items || []) {
          const symbol = normalizeSymbol(quote.symbol)
          returnedSymbols.add(symbol)
          const nextState: QuoteState = {
            price: quote.price,
            changePct: quote.change_pct,
            loading: false,
            adapter: quote.adapter,
          }
          quoteCacheRef.current[symbol] = { at: Date.now(), data: nextState }
          mapped[symbol] = nextState
        }

        for (const symbol of symbolsToFetch) {
          if (!returnedSymbols.has(symbol)) {
            mapped[symbol] = {
              price: null,
              loading: false,
              error: '价格获取失败',
            }
          }
        }

        setQuotes((prev) => ({ ...prev, ...mapped }))
      } catch (error) {
        if (cancelled) return
        setQuotes((prev) => {
          const fallback = { ...prev }
          for (const symbol of symbolsToFetch) {
            fallback[symbol] = {
              ...(prev[symbol] || {}),
              loading: false,
              error: error instanceof Error ? error.message : '价格获取失败',
            }
          }
          return fallback
        })
      } finally {
        quoteBatchRunningRef.current = false
        if (!cancelled) {
          setQuoteRefreshAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
        }
      }
    }

    refreshQuotesRef.current = refreshBatch
    void refreshBatch(false)
    const timer = window.setInterval(() => {
      void refreshBatch(false)
    }, QUOTE_REFRESH_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [holdings])

  const summary = useMemo(() => {
    const enriched = holdings.map((holding) => {
      const symbol = normalizeSymbol(holding.symbol)
      const currency = inferCurrency(symbol)
      const liveQuote = quotes[symbol]
      const livePrice = liveQuote?.price ?? 0
      const currentPriceCny = toCny(livePrice, currency)
      const targetPriceCny = toCny(holding.target2032, currency)
      const marketValue = currentPriceCny * holding.shares
      const targetValue = targetPriceCny * holding.shares
      const capitalCagr = calcCagr(currentPriceCny, targetPriceCny)
      const totalCagr = capitalCagr + holding.dividend_yield
      return { ...holding, symbol, currency, livePrice, currentPriceCny, targetPriceCny, marketValue, targetValue, capitalCagr, totalCagr, quote: liveQuote }
    })

    const totalValue = enriched.reduce((sum, item) => sum + item.marketValue, 0)
    const targetValue = enriched.reduce((sum, item) => sum + item.targetValue, 0)
    const dividendIncome = enriched.reduce((sum, item) => sum + item.marketValue * item.dividend_yield, 0)
    const weightedCagr = totalValue > 0 ? enriched.reduce((sum, item) => sum + item.totalCagr * (item.marketValue / totalValue), 0) : 0

    const categories = Object.entries(CATEGORY_META).map(([key, meta]) => {
      const items = enriched.filter((item) => item.category === key)
      const value = items.reduce((sum, item) => sum + item.marketValue, 0)
      const weight = totalValue > 0 ? value / totalValue : 0
      const projected = items.reduce((sum, item) => sum + item.targetValue, 0)
      const categoryCagr = value > 0 && projected > 0 ? Math.pow(projected / value, 1 / YEARS) - 1 : 0
      const dividendYield = value > 0 ? items.reduce((sum, item) => sum + item.marketValue * item.dividend_yield, 0) / value : 0
      return { key: key as CategoryKey, ...meta, items, value, weight, projected, totalCagr: categoryCagr + dividendYield }
    })

    return { enriched, totalValue, targetValue, dividendIncome, weightedCagr, categories }
  }, [holdings, quotes])

  const valuationAdvice = useMemo(() => {
    if (sp500Pe >= 30) {
      return [
        ['减仓进攻仓位', '高估值环境下优先降低高弹性资产占比。'],
        ['增持防御资产', '新增资金优先流向防御压舱石。'],
        ['留出现金', '留足再平衡空间，别把仓位打满。'],
      ]
    }
    if (vix >= 30) {
      return [
        ['分批加仓主轴', '恐慌期适合逐步提高成长主轴仓位。'],
        ['先看基本面', '优先买逻辑没坏的优质资产。'],
        ['保持纪律', '按目标区间补仓。'],
      ]
    }
    return [
      ['维持均衡结构', '当前更适合慢慢微调，不是大幅切仓。'],
      ['优先补低配', '新增资金优先补低于目标区间的板块。'],
      ['等待触发器', '大动作留给极端估值或基本面拐点。'],
    ]
  }, [sp500Pe, vix])

  const eventAdvice = useMemo(() => {
    const holding = holdings.find((item) => normalizeSymbol(item.symbol) === eventSymbol)
    if (!holding || !eventType) return null
    const mapping: Record<string, { tag: string; text: string }> = {
      beat: { tag: '加仓', text: '业绩超预期且逻辑仍在，可以小步加仓。' },
      miss: { tag: '观察', text: '先区分一次性失误还是趋势性恶化。' },
      ceo: { tag: '观察', text: '管理层变化先降低信心系数。' },
      md: { tag: '加仓', text: '护城河加深属于长期利好。' },
      me: { tag: '减仓', text: '竞争格局恶化会下调长期收益率。' },
      ov: { tag: '减仓', text: '估值透支时优先再平衡。' },
      uv: { tag: '加仓', text: '若基本面未变，可作为计划内加仓窗口。' },
      pr: { tag: '观察', text: '先判断政策是否伤及商业模式。' },
      pg: { tag: '持有', text: '政策利好先增强长期信心，不必追涨。' },
    }
    return { holding, ...mapping[eventType] }
  }, [eventSymbol, eventType, holdings])

  const macroAdvice = useMemo(() => {
    const mapping: Record<string, string[]> = {
      exp: ['经济扩张期：成长主轴保持核心。', '让盈利兑现比频繁择时更重要。'],
      hot: ['经济过热期：压低高估值仓位。', '新增资金先回流防御资产。'],
      rec: ['经济衰退期：提高防御仓和现金缓冲。', '只在错杀时出手。'],
      rev: ['经济复苏期：逐步切回成长主轴。', '保持纪律，不要上头。'],
    }
    return macroCycle ? mapping[macroCycle] : []
  }, [macroCycle])

  const projectedSeries = useMemo(() => {
    const annualRate = summary.weightedCagr
    return [2026, 2027, 2028, 2029, 2030, 2031, 2032].map((year, index) => ({
      year,
      value: summary.totalValue * Math.pow(1 + annualRate, index),
    }))
  }, [summary.totalValue, summary.weightedCagr])

  const sortedHoldings = useMemo(() => {
    const rows = [...summary.enriched]
    if (holdingSort === 'symbol') {
      rows.sort((a, b) => a.symbol.localeCompare(b.symbol, 'zh-CN'))
    } else if (holdingSort === 'cagr') {
      rows.sort((a, b) => b.totalCagr - a.totalCagr)
    } else {
      rows.sort((a, b) => b.marketValue - a.marketValue)
    }
    return rows
  }, [summary.enriched, holdingSort])

  function updateHolding(id: string, patch: Partial<HoldingDraft>) {
    setHoldings((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)))
  }

  function deleteHolding(id: string) {
    setHoldings((prev) => prev.filter((item) => item.id !== id))
  }

  function addHoldingByPool(symbol: string) {
    const stock = watchlist.find((item) => item.symbol === symbol)
    if (!stock) return
    const normalized = normalizeSymbol(stock.symbol)
    if (holdings.some((item) => normalizeSymbol(item.symbol) === normalized)) return
    setHoldings((prev) => [
      {
        id: `${normalized}-${Date.now()}`,
        symbol: normalized,
        name: stock.name,
        shares: 0,
        target2032: 0,
        dividend_yield: 0,
        category: stock.market === 'CN' ? 'defensive' : 'growth',
        currency: inferCurrency(normalized),
        pe: null,
        moat: '',
        risk: 3,
        note: '',
      },
      ...prev,
    ])
    setSelectedStock('')
  }

  async function handleSave() {
    setSaveStatus('保存中...')
    try {
      const saved = await savePlan2032Holdings(
        holdings.map((item, index) => ({
          symbol: normalizeSymbol(item.symbol),
          name: item.name,
          shares: item.shares,
          target2032: item.target2032,
          dividend_yield: item.dividend_yield,
          category: item.category,
          currency: inferCurrency(item.symbol),
          pe: item.pe ?? null,
          moat: item.moat || '',
          risk: item.risk ?? 3,
          note: item.note || '',
          sort_order: index,
        })),
      )
      setHoldings(saved.map((row, index) => toDraft(row, `saved-${index}`)))
      setSaveStatus('已保存')
      window.setTimeout(() => setSaveStatus(''), 2000)
    } catch (error) {
      setSaveStatus(error instanceof Error ? `保存失败：${error.message}` : '保存失败')
    }
  }

  return (
    <div className="page plan2032-page">
      <div className="plan2032-header">
        <div>
          <h2>2032 Plan</h2>
          <p className="plan2032-subtitle">持仓/目标/股息率可编辑，保存后写入后端；新增标的优先使用股票池作为数据源。</p>
        </div>
      </div>

      <div className="plan2032-tabs">
        <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>📊 总览</button>
        <button className={tab === 'holdings' ? 'active' : ''} onClick={() => setTab('holdings')}>📋 持仓明细</button>
        <button className={tab === 'advisor' ? 'active' : ''} onClick={() => setTab('advisor')}>🎯 调仓顾问</button>
      </div>

      {tab === 'overview' && (
        <>
          <section className="plan2032-metrics-grid">
            <MetricCard label="当前总资产" value={formatCompact(summary.totalValue)} sub={`2032 目标 ${formatCompact(summary.targetValue)}`} />
            <MetricCard label="组合总 CAGR" value={formatPct(summary.weightedCagr)} sub="随实时价格自动联动" />
            <MetricCard label="预计股息现金流" value={formatCompact(summary.dividendIncome)} sub="按当前持仓粗算" />
            <MetricCard label="七年增量空间" value={formatCompact(summary.targetValue - summary.totalValue)} sub="目标价口径" />
          </section>

          <section className="plan2032-layout-two">
            <div className="plan2032-panel">
              <div className="plan2032-section-title">持仓结构</div>
              <div className="plan2032-category-list">
                {summary.categories.map((category) => (
                  <div key={category.key} className="plan2032-category-row">
                    <div className="plan2032-category-left">
                      <span className="plan2032-color-dot" style={{ background: category.color }} />
                      <div>
                        <div>{category.name}</div>
                        <div className="plan2032-muted">目标 {formatPct(category.targetRange[0], 0)} - {formatPct(category.targetRange[1], 0)}</div>
                      </div>
                    </div>
                    <div className="plan2032-category-right">
                      <strong>{formatPct(category.weight)}</strong>
                      <span>{formatCompact(category.value)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="plan2032-panel">
              <div className="plan2032-section-title">板块 CAGR 对比</div>
              <div className="plan2032-module-cards">
                {summary.categories.map((category) => (
                  <div key={category.key} className="plan2032-module-card" style={{ background: category.light }}>
                    <div className="plan2032-module-top">
                      <span>{category.name}</span>
                      <span style={{ color: category.color }}>{formatPct(category.totalCagr)}</span>
                    </div>
                    <div className="plan2032-progress-track">
                      <div className="plan2032-progress-fill" style={{ width: `${Math.min(category.weight * 100, 100)}%`, background: category.color }} />
                    </div>
                    <div className="plan2032-muted">当前占比 {formatPct(category.weight)} · 目标估值 {formatCompact(category.projected)}</div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="plan2032-panel">
            <div className="plan2032-section-title">组合价值预测 · 2026–2032</div>
            <div className="plan2032-projection-list">
              {projectedSeries.map((item) => (
                <div key={item.year} className="plan2032-projection-row">
                  <span>{item.year}</span>
                  <div className="plan2032-projection-bar-track">
                    <div className="plan2032-projection-bar-fill" style={{ width: `${(item.value / Math.max(projectedSeries[projectedSeries.length - 1].value || 1, 1)) * 100}%` }} />
                  </div>
                  <strong>{formatCompact(item.value)}</strong>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {tab === 'holdings' && (
        <section className="plan2032-panel">
          <div className="plan2032-section-title">持仓明细</div>
          <div className="plan2032-add-wrap">
            <div className="plan2032-search-bar">
              <select value={selectedStock} onChange={(event) => setSelectedStock(event.target.value)}>
                <option value="">从股票池选择新增标的</option>
                {watchlist.map((item) => (
                  <option key={item.symbol} value={item.symbol}>
                    {item.symbol} · {item.name} · {item.market}
                  </option>
                ))}
              </select>
              <button className="btn" onClick={() => addHoldingByPool(selectedStock)} disabled={!selectedStock}>新增标的</button>
              <div className="plan2032-inline-sort">
                <button className={holdingSort === 'position' ? 'active' : ''} onClick={() => setHoldingSort('position')}>按仓位</button>
                <button className={holdingSort === 'symbol' ? 'active' : ''} onClick={() => setHoldingSort('symbol')}>按标的</button>
                <button className={holdingSort === 'cagr' ? 'active' : ''} onClick={() => setHoldingSort('cagr')}>按总CAGR</button>
              </div>
              <button className="btn-outline" onClick={() => refreshQuotesRef.current?.(true)}>刷新价格</button>
              <button className="btn-outline" onClick={handleSave}>保存</button>
            </div>
            {saveStatus && <div className="plan2032-muted">{saveStatus}</div>}
            <div className="plan2032-muted">价格缓存保留；自动刷新频率：1小时。{quoteRefreshAt ? ` 最近刷新：${quoteRefreshAt}` : ''}</div>
          </div>

          <div className="plan2032-table-wrap">
            <table className="plan2032-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>名称</th>
                  <th>持股</th>
                  <th>现价(实时)</th>
                  <th>2032目标</th>
                  <th>股息率</th>
                  <th>市值(CNY)</th>
                  <th>占比</th>
                  <th>资本CAGR</th>
                  <th>总CAGR</th>
                  <th>类别</th>
                  <th>删除</th>
                </tr>
              </thead>
              <tbody>
                {sortedHoldings.map((item) => (
                  <tr key={item.id}>
                    <td>{item.symbol}</td>
                    <td><input value={item.name} onChange={(event) => updateHolding(item.id, { name: event.target.value })} className="plan2032-text-input" /></td>
                    <td><input value={item.shares} type="number" step="0.1" onChange={(event) => updateHolding(item.id, { shares: Number(event.target.value || 0) })} className="plan2032-share-input" /></td>
                    <td>
                      <div className="plan2032-name-cell">
                        <strong>{item.livePrice ? `${item.livePrice.toFixed(2)} ${item.currency}` : '—'}</strong>
                        <span className={item.quote?.error ? 'plan2032-error-text' : 'plan2032-muted'}>
                          {item.quote?.error ? '取价失败' : item.quote?.loading ? '刷新中...' : item.quote?.adapter || '实时价'}
                        </span>
                      </div>
                    </td>
                    <td><input value={item.target2032} type="number" step="0.01" onChange={(event) => updateHolding(item.id, { target2032: Number(event.target.value || 0) })} className="plan2032-share-input" /></td>
                    <td><input value={item.dividend_yield} type="number" step="0.001" onChange={(event) => updateHolding(item.id, { dividend_yield: Number(event.target.value || 0) })} className="plan2032-share-input" /></td>
                    <td>{formatCompact(item.marketValue)}</td>
                    <td>{summary.totalValue > 0 ? formatPct(item.marketValue / summary.totalValue) : '0.0%'}</td>
                    <td>{formatPct(item.capitalCagr)}</td>
                    <td className="plan2032-green">{formatPct(item.totalCagr)}</td>
                    <td>
                      <select value={item.category} onChange={(event) => updateHolding(item.id, { category: event.target.value as CategoryKey })}>
                        {Object.entries(CATEGORY_META).map(([key, meta]) => <option key={key} value={key}>{meta.name}</option>)}
                      </select>
                    </td>
                    <td><button className="btn-outline danger plan2032-delete-btn" onClick={() => deleteHolding(item.id)}>删</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="plan2032-muted plan2032-toolbar-note">已修：A股代码统一标准化为 .SH / .SZ，避免实时价格按美股去取；新增标的来自股票池下拉源；保存会落到后端数据库。</div>
        </section>
      )}

      {tab === 'advisor' && (
        <section className="plan2032-panel">
          <div className="plan2032-advisor-tabs">
            <button className={advisorTab === 'valuation' ? 'active' : ''} onClick={() => setAdvisorTab('valuation')}>① 估值环境</button>
            <button className={advisorTab === 'events' ? 'active' : ''} onClick={() => setAdvisorTab('events')}>② 个股事件</button>
            <button className={advisorTab === 'macro' ? 'active' : ''} onClick={() => setAdvisorTab('macro')}>③ 宏观周期</button>
            <button className={advisorTab === 'discipline' ? 'active' : ''} onClick={() => setAdvisorTab('discipline')}>④ 机械纪律</button>
          </div>

          {advisorTab === 'valuation' && (
            <div className="plan2032-advisor-grid">
              <div>
                <label className="plan2032-field-label">标普500 前瞻P/E：{sp500Pe}</label>
                <input type="range" min={10} max={40} step={0.5} value={sp500Pe} onChange={(event) => setSp500Pe(Number(event.target.value))} />
              </div>
              <div>
                <label className="plan2032-field-label">VIX 恐慌指数：{vix}</label>
                <input type="range" min={10} max={60} step={1} value={vix} onChange={(event) => setVix(Number(event.target.value))} />
              </div>
              <div className="plan2032-advice-box">
                {valuationAdvice.map(([title, text]) => (
                  <div key={title} className="plan2032-advice-item"><strong>{title}</strong><p>{text}</p></div>
                ))}
              </div>
            </div>
          )}

          {advisorTab === 'events' && (
            <div className="plan2032-advisor-grid">
              <div className="plan2032-form-grid">
                <div>
                  <label className="plan2032-field-label">持仓标的</label>
                  <select value={eventSymbol} onChange={(event) => setEventSymbol(event.target.value)}>
                    <option value="">— 请选择 —</option>
                    {holdings.map((holding) => <option key={holding.id} value={normalizeSymbol(holding.symbol)}>{holding.name} ({normalizeSymbol(holding.symbol)})</option>)}
                  </select>
                </div>
                <div>
                  <label className="plan2032-field-label">事件类型</label>
                  <select value={eventType} onChange={(event) => setEventType(event.target.value)}>
                    <option value="">— 请选择 —</option>
                    <option value="beat">业绩大幅超预期</option>
                    <option value="miss">业绩大幅不及预期</option>
                    <option value="ceo">CEO/核心管理层离职</option>
                    <option value="md">护城河加深</option>
                    <option value="me">竞争格局恶化</option>
                    <option value="ov">估值严重透支</option>
                    <option value="uv">非基本面暴跌</option>
                    <option value="pr">重大政策利空</option>
                    <option value="pg">重大政策利好</option>
                  </select>
                </div>
              </div>
              <div className="plan2032-advice-box">
                {eventAdvice ? <div className="plan2032-advice-item"><span className="plan2032-pill">{eventAdvice.tag}</span><strong>{eventAdvice.holding.name} · {normalizeSymbol(eventAdvice.holding.symbol)}</strong><p>{eventAdvice.text}</p></div> : <div className="plan2032-muted">请选择标的和事件类型。</div>}
              </div>
            </div>
          )}

          {advisorTab === 'macro' && (
            <div className="plan2032-advisor-grid">
              <div className="plan2032-macro-buttons">
                <button className={macroCycle === 'exp' ? 'active' : ''} onClick={() => setMacroCycle('exp')}>经济扩张期</button>
                <button className={macroCycle === 'hot' ? 'active' : ''} onClick={() => setMacroCycle('hot')}>经济过热期</button>
                <button className={macroCycle === 'rec' ? 'active' : ''} onClick={() => setMacroCycle('rec')}>经济衰退期</button>
                <button className={macroCycle === 'rev' ? 'active' : ''} onClick={() => setMacroCycle('rev')}>经济复苏期</button>
              </div>
              <div className="plan2032-advice-box">
                {macroAdvice.length > 0 ? macroAdvice.map((item) => <div key={item} className="plan2032-advice-item"><p>{item}</p></div>) : <div className="plan2032-muted">请选择当前宏观阶段。</div>}
              </div>
            </div>
          )}

          {advisorTab === 'discipline' && (
            <div className="plan2032-discipline-grid">
              {summary.categories.map((category) => {
                const below = category.weight < category.targetRange[0]
                const above = category.weight > category.targetRange[1]
                const action = below ? '补仓' : above ? '减仓' : '持有'
                return (
                  <div key={category.key} className="plan2032-discipline-card">
                    <div className="plan2032-module-top"><strong>{category.name}</strong><span style={{ color: category.color }}>{action}</span></div>
                    <div className="plan2032-muted">当前 {formatPct(category.weight)} · 目标 {formatPct(category.targetRange[0], 0)} - {formatPct(category.targetRange[1], 0)}</div>
                    <div className="plan2032-progress-track"><div className="plan2032-progress-fill" style={{ width: `${Math.min(category.weight * 100, 100)}%`, background: category.color }} /></div>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

function MetricCard({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="plan2032-metric-card">
      <div className="plan2032-metric-label">{label}</div>
      <div className="plan2032-metric-value">{value}</div>
      <div className="plan2032-muted">{sub}</div>
    </div>
  )
}

export default Plan2032Page
