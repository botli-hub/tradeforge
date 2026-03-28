import { useEffect, useMemo, useState } from 'react'
import FormulaEditor from './FormulaEditor'
import { StrategySummary, createStrategy, deleteStrategy, getStrategy, getStrategies, updateStrategy } from '../services/api'
import StockSelect from '../components/StockSelect'

type StrategyMode = 'visual' | 'formula'
type ViewMode = 'list' | 'create' | 'edit'

type StrategyItem = StrategySummary

type VisualForm = {
  name: string
  symbol: string
  timeframe: string
  fastPeriod: number
  slowPeriod: number
  volRatioThreshold: number
}

type FormulaForm = {
  name: string
  symbol: string
  timeframe: string
  sourceCode: string
}

const DEFAULT_VISUAL_FORM: VisualForm = {
  name: '',
  symbol: 'AAPL',
  timeframe: '1d',
  fastPeriod: 5,
  slowPeriod: 20,
  volRatioThreshold: 1.5,
}

const DEFAULT_FORMULA_FORM: FormulaForm = {
  name: '',
  symbol: 'AAPL',
  timeframe: '1d',
  sourceCode: '',
}

function buildVisualConfig(form: VisualForm) {
  return {
    version: '1.0',
    strategy_id: '',
    mode: 'visual',
    name: form.name,
    symbols: [form.symbol.trim().toUpperCase()],
    timeframe: form.timeframe,
    indicators: [
      { name: 'ma_fast', type: 'MA', period: Number(form.fastPeriod), source: 'close' },
      { name: 'ma_slow', type: 'MA', period: Number(form.slowPeriod), source: 'close' },
      { name: 'vol_ma', type: 'MA', period: 20, source: 'volume' },
    ],
    variables: {
      vol_ratio: {
        op: '/',
        left: 'volume',
        right: 'vol_ma',
      },
    },
    conditions: {
      entry: {
        type: 'AND',
        rules: [
          { id: 'entry_1', type: 'crossover', op: 'cross_above', left: 'ma_fast', right: 'ma_slow' },
          { id: 'entry_2', type: 'binary', op: '>', left: 'vol_ratio', right: Number(form.volRatioThreshold) },
        ],
      },
      exit: {
        type: 'OR',
        rules: [
          { id: 'exit_1', type: 'crossover', op: 'cross_below', left: 'ma_fast', right: 'ma_slow' },
        ],
      },
    },
    position_sizing: { type: 'fixed_amount', value: 10000 },
    risk_rules: { initial_capital: 100000, fee_rate: 0.0003, slippage: 0.001, max_position_pct: 0.5 },
  }
}

function strategyToVisualForm(strategy: any): VisualForm {
  const config = strategy?.config || {}
  const entryRules = config?.conditions?.entry?.rules || []
  const thresholdRule = entryRules.find((item: any) => item?.id === 'entry_2' || item?.left === 'vol_ratio')
  const fast = (config?.indicators || []).find((item: any) => item?.name === 'ma_fast')
  const slow = (config?.indicators || []).find((item: any) => item?.name === 'ma_slow')
  return {
    name: strategy?.name || config?.name || '',
    symbol: config?.symbols?.[0] || 'AAPL',
    timeframe: config?.timeframe || '1d',
    fastPeriod: Number(fast?.period || 5),
    slowPeriod: Number(slow?.period || 20),
    volRatioThreshold: Number(thresholdRule?.right || 1.5),
  }
}

function strategyToFormulaForm(strategy: any): FormulaForm {
  const config = strategy?.config || {}
  return {
    name: strategy?.name || config?.name || '',
    symbol: config?.symbols?.[0] || 'AAPL',
    timeframe: config?.timeframe || '1d',
    sourceCode: config?.source_code || '',
  }
}

export default function StrategyPage() {
  const [strategies, setStrategies] = useState<StrategyItem[]>([])
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editorMode, setEditorMode] = useState<StrategyMode>('visual')
  const [visualForm, setVisualForm] = useState<VisualForm>(DEFAULT_VISUAL_FORM)
  const [formulaForm, setFormulaForm] = useState<FormulaForm>(DEFAULT_FORMULA_FORM)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string>('')
  const [error, setError] = useState<string>('')

  const pageTitle = useMemo(() => {
    if (viewMode === 'create') return '新建策略'
    if (viewMode === 'edit') return '编辑策略'
    return '我的策略'
  }, [viewMode])

  useEffect(() => {
    loadStrategies()
  }, [])

  async function loadStrategies() {
    setLoading(true)
    try {
      const data = await getStrategies()
      setStrategies(data)
    } catch (e: any) {
      setError(e?.message || '策略列表加载失败')
    } finally {
      setLoading(false)
    }
  }

  function resetForms(nextMode: StrategyMode = 'visual', clearNotice: boolean = true) {
    setEditorMode(nextMode)
    setEditingId(null)
    setVisualForm(DEFAULT_VISUAL_FORM)
    setFormulaForm(DEFAULT_FORMULA_FORM)
    setError('')
    if (clearNotice) {
      setMessage('')
    }
  }

  function startCreate(mode: StrategyMode = 'visual') {
    resetForms(mode)
    setViewMode('create')
  }

  async function startEdit(strategyId: string) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const detail = await getStrategy(strategyId)
      const mode = (detail?.mode || detail?.config?.mode || 'visual') as StrategyMode
      setEditingId(strategyId)
      setEditorMode(mode)
      if (mode === 'formula') {
        setFormulaForm(strategyToFormulaForm(detail))
      } else {
        setVisualForm(strategyToVisualForm(detail))
      }
      setViewMode('edit')
    } catch (e: any) {
      setError(e?.message || '加载策略详情失败')
    } finally {
      setBusy(false)
    }
  }

  function cancelEditing(clearNotice: boolean = false) {
    resetForms('visual', clearNotice)
    setViewMode('list')
  }

  function validateVisualForm() {
    if (!visualForm.name.trim()) return '请输入策略名称'
    if (!visualForm.symbol.trim()) return '请输入标的 symbol'
    if (visualForm.fastPeriod <= 0 || visualForm.slowPeriod <= 0) return '均线周期必须大于 0'
    if (visualForm.fastPeriod >= visualForm.slowPeriod) return 'fast period 需小于 slow period'
    return ''
  }

  function validateFormulaMeta() {
    if (!formulaForm.name.trim()) return '请输入策略名称'
    if (!formulaForm.symbol.trim()) return '请输入标的 symbol'
    return ''
  }

  async function submitVisualStrategy() {
    const validationError = validateVisualForm()
    if (validationError) {
      setError(validationError)
      return
    }

    const payload = {
      name: visualForm.name.trim(),
      config: buildVisualConfig(visualForm),
    }

    setBusy(true)
    setError('')
    setMessage('')
    try {
      if (viewMode === 'edit' && editingId) {
        await updateStrategy(editingId, payload)
        setMessage('visual 策略已更新')
      } else {
        await createStrategy(payload)
        setMessage('visual 策略已创建')
      }
      await loadStrategies()
      cancelEditing(false)
    } catch (e: any) {
      setError(e?.message || '保存 visual 策略失败')
    } finally {
      setBusy(false)
    }
  }

  async function submitFormulaStrategy(ir: any) {
    const validationError = validateFormulaMeta()
    if (validationError) {
      setError(validationError)
      return
    }

    const payload = {
      name: formulaForm.name.trim(),
      config: {
        ...ir,
        mode: 'formula',
        name: formulaForm.name.trim(),
        symbols: [formulaForm.symbol.trim().toUpperCase()],
        timeframe: formulaForm.timeframe,
        source_code: formulaForm.sourceCode || ir?.source_code || '',
      },
    }

    setBusy(true)
    setError('')
    setMessage('')
    try {
      if (viewMode === 'edit' && editingId) {
        await updateStrategy(editingId, payload)
        setMessage('formula 策略已更新')
      } else {
        await createStrategy(payload)
        setMessage('formula 策略已创建')
      }
      await loadStrategies()
      cancelEditing(false)
    } catch (e: any) {
      setError(e?.message || '保存 formula 策略失败')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm('确定删除这个策略吗？')) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await deleteStrategy(id)
      await loadStrategies()
      setMessage('策略已删除')
      if (editingId === id) {
        cancelEditing()
      }
    } catch (e: any) {
      setError(e?.message || '删除策略失败')
    } finally {
      setBusy(false)
    }
  }

  function renderVisualEditor() {
    return (
      <div className="strategy-editor-card card">
        <div className="strategy-form-grid">
          <label className="strategy-field">
            <span>策略名称</span>
            <input value={visualForm.name} onChange={(e) => setVisualForm({ ...visualForm, name: e.target.value })} placeholder="例如：AAPL 放量均线策略" />
          </label>
          <label className="strategy-field">
            <span>标的 Symbol</span>
            <StockSelect value={visualForm.symbol} onChange={v => setVisualForm({ ...visualForm, symbol: v })} />
          </label>
          <label className="strategy-field">
            <span>Timeframe</span>
            <select value={visualForm.timeframe} onChange={(e) => setVisualForm({ ...visualForm, timeframe: e.target.value })}>
              <option value="1d">1d</option>
              <option value="1h">1h</option>
              <option value="30m">30m</option>
              <option value="5m">5m</option>
              <option value="1m">1m</option>
            </select>
          </label>
          <label className="strategy-field">
            <span>Fast Period</span>
            <input type="number" min={1} value={visualForm.fastPeriod} onChange={(e) => setVisualForm({ ...visualForm, fastPeriod: Number(e.target.value) })} />
          </label>
          <label className="strategy-field">
            <span>Slow Period</span>
            <input type="number" min={1} value={visualForm.slowPeriod} onChange={(e) => setVisualForm({ ...visualForm, slowPeriod: Number(e.target.value) })} />
          </label>
          <label className="strategy-field">
            <span>Vol Ratio Threshold</span>
            <input type="number" step="0.1" min={0} value={visualForm.volRatioThreshold} onChange={(e) => setVisualForm({ ...visualForm, volRatioThreshold: Number(e.target.value) })} />
          </label>
        </div>

        <div className="strategy-preview">
          <div className="strategy-preview-title">策略说明</div>
          <p>当快线上穿慢线，且成交量放大比例大于阈值时触发买入；快线下穿慢线时触发卖出。</p>
        </div>

        <div className="strategy-editor-actions">
          <button className="btn-outline" onClick={() => cancelEditing()} disabled={busy}>取消</button>
          <button className="btn" onClick={submitVisualStrategy} disabled={busy}>{busy ? '保存中...' : viewMode === 'edit' ? '保存策略' : '创建策略'}</button>
        </div>
      </div>
    )
  }

  function renderFormulaEditor() {
    return (
      <div className="strategy-editor-card card">
        <div className="strategy-form-grid strategy-form-grid-compact">
          <label className="strategy-field">
            <span>策略名称</span>
            <input value={formulaForm.name} onChange={(e) => setFormulaForm({ ...formulaForm, name: e.target.value })} placeholder="例如：MA Cross Formula" />
          </label>
          <label className="strategy-field">
            <span>标的 Symbol</span>
            <StockSelect value={formulaForm.symbol} onChange={v => setFormulaForm({ ...formulaForm, symbol: v })} />
          </label>
          <label className="strategy-field">
            <span>Timeframe</span>
            <select value={formulaForm.timeframe} onChange={(e) => setFormulaForm({ ...formulaForm, timeframe: e.target.value })}>
              <option value="1d">1d</option>
              <option value="1h">1h</option>
              <option value="30m">30m</option>
              <option value="5m">5m</option>
              <option value="1m">1m</option>
            </select>
          </label>
        </div>

        <div className="strategy-preview">
          <div className="strategy-preview-title">操作说明</div>
          <p>先填写基础信息，再在 Formula 编辑器内验证/转译，按钮会直接创建或保存当前策略。</p>
        </div>

        <FormulaEditor
          initialCode={formulaForm.sourceCode}
          saveLabel={viewMode === 'edit' ? '保存策略并转译' : '创建策略并转译'}
          onCodeChange={(code) => setFormulaForm((prev) => ({ ...prev, sourceCode: code }))}
          onSave={submitFormulaStrategy}
        />

        <div className="strategy-editor-actions">
          <button className="btn-outline" onClick={() => cancelEditing()} disabled={busy}>返回列表</button>
        </div>
      </div>
    )
  }

  return (
    <div className="page" style={{ display: 'block' }}>
      <div className="strategy-page-shell">
        <div className="strategy-page-header">
          <div>
            <h2>{pageTitle}</h2>
            <p className="strategy-page-subtitle">支持 visual / formula 两种创建方式，demo 数据会在空库时自动注入。</p>
          </div>
          {viewMode === 'list' ? (
            <div className="strategy-toolbar">
              <button className="btn-outline" onClick={() => startCreate('visual')}>+ 新建 Visual 策略</button>
              <button className="btn" onClick={() => startCreate('formula')}>+ 新建 Formula 策略</button>
            </div>
          ) : (
            <div className="strategy-toolbar">
              <button className="btn-outline" onClick={() => cancelEditing()}>返回列表</button>
            </div>
          )}
        </div>

        {(error || message) && (
          <div className={`strategy-notice ${error ? 'error' : 'success'}`}>
            {error || message}
          </div>
        )}

        {viewMode !== 'list' && (
          <div className="strategy-mode-switch">
            <button className={`mode-pill ${editorMode === 'visual' ? 'active' : ''}`} onClick={() => resetForms('visual')} disabled={viewMode === 'edit' && editorMode === 'formula'}>
              Visual
            </button>
            <button className={`mode-pill ${editorMode === 'formula' ? 'active' : ''}`} onClick={() => resetForms('formula')} disabled={viewMode === 'edit' && editorMode === 'visual'}>
              Formula
            </button>
          </div>
        )}

        {viewMode === 'create' || viewMode === 'edit' ? (
          editorMode === 'formula' ? renderFormulaEditor() : renderVisualEditor()
        ) : loading ? (
          <div className="card">策略加载中...</div>
        ) : strategies.length === 0 ? (
          <div className="empty-state">
            <h3>暂无策略</h3>
            <p>点击右上角按钮创建第一条策略。</p>
          </div>
        ) : (
          <div className="strategy-list">
            {strategies.map((s) => (
              <div key={s.id} className="strategy-list-card card">
                <div className="strategy-list-main">
                  <div className="strategy-title-row">
                    <h3>{s.name}</h3>
                    <span className={`tag ${s.status === 'ready' ? 'ready' : 'draft'}`}>{s.status || 'draft'}</span>
                  </div>
                  <div className="strategy-meta-grid">
                    <div><span className="meta-label">Mode</span><strong>{s.mode || (s.config?.mode as StrategyMode) || 'visual'}</strong></div>
                    <div><span className="meta-label">Symbol(s)</span><strong>{(s.symbols || []).join(', ') || '-'}</strong></div>
                    <div><span className="meta-label">Timeframe</span><strong>{s.timeframe || '-'}</strong></div>
                    <div><span className="meta-label">Version</span><strong>v{s.config?.version || s.version || 1}</strong></div>
                  </div>
                </div>
                <div className="strategy-list-actions">
                  <button className="btn-outline" onClick={() => startEdit(s.id)} disabled={busy}>编辑</button>
                  <button className="btn-outline danger" onClick={() => handleDelete(s.id)} disabled={busy}>删除</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
