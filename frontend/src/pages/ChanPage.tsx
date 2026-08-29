import { useEffect, useRef, useState } from 'react'
import { createChart, IChartApi, ISeriesApi, CandlestickData, LineData, SeriesMarker, Time } from 'lightweight-charts'
import StockSelect from '../components/StockSelect'
import {
  ChanAnalyze,
  getAppSettings,
  getChanAnalyze,
  subscribeSettings,
} from '../services/api'

const LEVELS: { value: string; label: string }[] = [
  { value: '1d', label: '日线' },
  { value: '1w', label: '周线' },
  { value: '60m', label: '60分钟' },
  { value: '30m', label: '30分钟' },
  { value: '15m', label: '15分钟' },
  { value: '5m', label: '5分钟' },
]

function toChartTime(value?: string): Time | null {
  if (!value) return null
  const n = Number(value)
  if (Number.isFinite(n) && n > 1e9) {
    return (n > 1e12 ? Math.floor(n / 1000) : Math.floor(n)) as Time
  }
  const ms = new Date(value).getTime()
  if (Number.isNaN(ms)) return null
  return Math.floor(ms / 1000) as Time
}

function trendColor(type: string) {
  if (type === 'up_trend') return 'var(--green)'
  if (type === 'down_trend') return 'var(--red)'
  return 'var(--text-secondary)'
}

export default function ChanPage() {
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')
  const [data, setData] = useState<ChanAnalyze | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [chartWarning, setChartWarning] = useState('')
  const [settings, setSettings] = useState(getAppSettings())

  const wrapRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const biRef = useRef<ISeriesApi<'Line'> | null>(null)
  const segRef = useRef<ISeriesApi<'Line'> | null>(null)
  const hubSeriesRef = useRef<ISeriesApi<'Line'>[]>([])

  useEffect(() => subscribeSettings(setSettings), [])

  useEffect(() => {
    if (!wrapRef.current) return
    try {
      const chart = createChart(wrapRef.current, {
        layout: { background: { color: '#000000' }, textColor: '#888888' },
        grid: { vertLines: { color: '#111111' }, horzLines: { color: '#111111' } },
        width: wrapRef.current.clientWidth,
        height: Math.max(320, Math.min(480, window.innerHeight * 0.42)),
        rightPriceScale: { borderColor: '#222' },
        timeScale: { borderColor: '#222', timeVisible: timeframe !== '1d' && timeframe !== '1w' },
      })
      candleRef.current = chart.addCandlestickSeries({
        upColor: '#00C805', downColor: '#FF5000',
        borderUpColor: '#00C805', borderDownColor: '#FF5000',
        wickUpColor: '#00C805', wickDownColor: '#FF5000',
      })
      biRef.current = chart.addLineSeries({ color: '#5ac8fa', lineWidth: 1, lastValueVisible: false, priceLineVisible: false })
      segRef.current = chart.addLineSeries({ color: '#ffd60a', lineWidth: 2, lastValueVisible: false, priceLineVisible: false })
      chartRef.current = chart
      setChartWarning('')
      const onResize = () => {
        if (!wrapRef.current || !chartRef.current) return
        chartRef.current.applyOptions({ width: wrapRef.current.clientWidth })
      }
      window.addEventListener('resize', onResize)
      return () => {
        window.removeEventListener('resize', onResize)
        hubSeriesRef.current.forEach(s => {
          try { chart.removeSeries(s) } catch { /* */ }
        })
        hubSeriesRef.current = []
        try { chart.remove() } catch { /* */ }
        chartRef.current = null
      }
    } catch (e: any) {
      setChartWarning(e?.message || '图表初始化失败')
      return
    }
  }, [timeframe])

  useEffect(() => {
    let dead = false
    setLoading(true)
    setError('')
    getChanAnalyze(symbol, timeframe, 500, settings)
      .then(d => { if (!dead) setData(d) })
      .catch(e => { if (!dead) { setError(e.message || '分析失败'); setData(null) } })
      .finally(() => { if (!dead) setLoading(false) })
    return () => { dead = true }
  }, [symbol, timeframe, settings.marketDataSource, settings.marketHost, settings.marketPort])

  useEffect(() => {
    if (!candleRef.current || !data) return
    try {
      const deduped = new Map<Time, CandlestickData>()
      data.klines.forEach(k => {
        const t = toChartTime(k.timestamp)
        if (t == null) return
        deduped.set(t, {
          time: t,
          open: Number(k.open),
          high: Number(k.high),
          low: Number(k.low),
          close: Number(k.close),
        })
      })
      const candles = [...deduped.entries()].sort((a, b) => Number(a[0]) - Number(b[0])).map(([, v]) => v)
      if (!candles.length) {
        setChartWarning('没有可画的 K 线')
        return
      }
      candleRef.current.setData(candles)

      const strokeLine = (items: ChanAnalyze['bis']): LineData[] => {
        const pts: LineData[] = []
        items.forEach((b, i) => {
          const t0 = toChartTime(b.start_ts)
          const t1 = toChartTime(b.end_ts)
          if (t0 == null || t1 == null) return
          if (i === 0) pts.push({ time: t0, value: b.start_price })
          pts.push({ time: t1, value: b.end_price })
        })
        return pts
      }
      biRef.current?.setData(strokeLine(data.bis))
      segRef.current?.setData(strokeLine(data.segments))

      const chart = chartRef.current
      if (chart) {
        hubSeriesRef.current.forEach(s => {
          try { chart.removeSeries(s) } catch { /* */ }
        })
        hubSeriesRef.current = []
        data.zhongshu.slice(-6).forEach((h, idx) => {
          const t0 = toChartTime(h.start_ts)
          const t1 = toChartTime(h.end_ts)
          if (t0 == null || t1 == null) return
          const col = idx === data.zhongshu.slice(-6).length - 1 ? '#bf5af2' : '#636366'
          const zg = chart.addLineSeries({ color: col, lineWidth: 1, lastValueVisible: false, priceLineVisible: false, lineStyle: 2 })
          const zd = chart.addLineSeries({ color: col, lineWidth: 1, lastValueVisible: false, priceLineVisible: false, lineStyle: 2 })
          zg.setData([{ time: t0, value: h.zg }, { time: t1, value: h.zg }])
          zd.setData([{ time: t0, value: h.zd }, { time: t1, value: h.zd }])
          hubSeriesRef.current.push(zg, zd)
        })
      }

      const markers: SeriesMarker<Time>[] = []
      data.signals.forEach(s => {
        const t = toChartTime(s.ts)
        if (t == null) return
        const buy = s.kind.startsWith('B')
        markers.push({
          time: t,
          position: buy ? 'belowBar' : 'aboveBar',
          color: buy ? '#00C805' : '#FF5000',
          shape: buy ? 'arrowUp' : 'arrowDown',
          text: s.label,
        })
      })
      candleRef.current.setMarkers(markers)
      chartRef.current?.timeScale().fitContent()
      setChartWarning('')
    } catch (e: any) {
      setChartWarning(e?.message || '叠加层绘制失败')
    }
  }, [data])

  const trend = data?.trend

  return (
    <div className="page active">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>缠论</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <StockSelect value={symbol} onChange={setSymbol} />
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {LEVELS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </div>
      </div>
      <div className="home-todo-label" style={{ marginTop: 0, marginBottom: 12 }}>
        选定标的和级别后分解走势。青线是笔，黄线是线段，紫/灰虚线是中枢上下沿。买卖点标在图上。
      </div>

      {error && <div className="card" style={{ color: 'var(--red)', marginBottom: 12 }}>{error}</div>}
      {loading && <div className="home-todo-label">正在分解 {symbol} {LEVELS.find(l => l.value === timeframe)?.label}…</div>}

      <div ref={wrapRef} style={{ width: '100%', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 12 }} />
      {chartWarning && <div className="home-todo-label">{chartWarning}</div>}

      {trend && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 4 }}>
            {data?.symbol} · {data?.level_label} · {data?.bar_count} 根K · {data?.bi_count} 笔 · {data?.segment_count} 段
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: trendColor(trend.type) }}>{trend.label}</div>
          <div style={{ marginTop: 6, color: 'var(--text-secondary)' }}>{trend.summary}</div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }} className="chan-two-col">
        <div className="card">
          <div className="paths-col-title">中枢</div>
          {!data?.zhongshu.length && <div className="home-todo-empty">尚未形成笔中枢</div>}
          {(data?.zhongshu || []).slice().reverse().map((h, i) => (
            <div key={`${h.start_ts}-${i}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
              <div style={{ fontWeight: 650 }}>ZG {h.zg} · ZD {h.zd}</div>
              <div style={{ color: 'var(--text-secondary)' }}>{h.bi_count} 笔 · {h.direction === 'up' ? '上' : h.direction === 'down' ? '下' : '震荡'}</div>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="paths-col-title">买卖点</div>
          {!data?.signals.length && <div className="home-todo-empty">当前级别没有已确认的三类买卖点</div>}
          {(data?.signals || []).map(s => (
            <div key={s.kind} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
              <div style={{ fontWeight: 700, color: s.kind.startsWith('B') ? 'var(--green)' : 'var(--red)' }}>
                {s.label} · {s.price}
              </div>
              <div style={{ color: 'var(--text-secondary)' }}>{s.note}</div>
            </div>
          ))}
        </div>
      </div>
      <style>{`
        @media (max-width: 640px) {
          .chan-two-col { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  )
}
