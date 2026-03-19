import { useEffect, useState } from 'react'
import { API_BASE } from '../services/api'

const DEFAULT_CODE = `strategy("MA Cross", capital=100000, fee=0.0003)

fast = param("快线周期", 5, 2, 50)
slow = param("慢线周期", 20, 5, 200)

ma_fast = MA(close, fast)
ma_slow = MA(close, slow)
vol_ratio = volume / MA(volume, 20)

entry = cross_above(ma_fast, ma_slow) and vol_ratio > 1.5
exit = cross_below(ma_fast, ma_slow)

if entry:
    buy(100)

if exit:
    sell_all()
`

type ParamItem = {
  name: string
  default: number
  min: number
  max: number
}

type FormulaEditorProps = {
  onSave?: (ir: any) => void
  onCodeChange?: (code: string) => void
  initialCode?: string
  saveLabel?: string
}

export default function FormulaEditor({
  onSave,
  onCodeChange,
  initialCode = '',
  saveLabel = '保存并转译',
}: FormulaEditorProps) {
  const [code, setCode] = useState(initialCode || DEFAULT_CODE)
  const [params, setParams] = useState<ParamItem[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [validating, setValidating] = useState(false)
  const [transpiling, setTranspiling] = useState(false)

  useEffect(() => {
    const nextCode = initialCode || DEFAULT_CODE
    setCode(nextCode)
    onCodeChange?.(nextCode)
    setParams([])
    setErrors([])
  }, [initialCode])

  function updateCode(nextCode: string) {
    setCode(nextCode)
    onCodeChange?.(nextCode)
  }

  async function validate() {
    setValidating(true)
    setErrors([])

    try {
      const res = await fetch(`${API_BASE}/api/formula/validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      })
      const data = await res.json()

      if (!data.valid) {
        setErrors([data.message || '语法错误'])
      } else {
        await parseParams()
      }
    } catch {
      setErrors(['连接失败，请确保后端运行中'])
    } finally {
      setValidating(false)
    }
  }

  async function parseParams() {
    try {
      const res = await fetch(`${API_BASE}/api/formula/parse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      })
      const data = await res.json()
      if (data.success) {
        setParams(data.params || [])
      }
    } catch {
      setErrors(['参数解析失败'])
    }
  }

  async function transpile() {
    setTranspiling(true)
    setErrors([])
    try {
      const res = await fetch(`${API_BASE}/api/formula/transpile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      })
      const data = await res.json()
      if (data.success) {
        onSave?.({ ...data.ir, source_code: code })
      } else {
        setErrors([data.detail || '转译失败'])
      }
    } catch {
      setErrors(['转译失败'])
    } finally {
      setTranspiling(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%' }}>
      <div style={{ flex: 2 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={{ color: '#4cc9f0', fontWeight: 'bold' }}>Formula 编辑器</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-outline" onClick={validate} disabled={validating || transpiling}>
              {validating ? '验证中...' : '验证'}
            </button>
            <button className="btn" onClick={transpile} disabled={transpiling || validating}>
              {transpiling ? '转译中...' : saveLabel}
            </button>
          </div>
        </div>

        {errors.length > 0 && (
          <div
            style={{
              background: 'rgba(233, 69, 96, 0.15)',
              border: '1px solid rgba(233, 69, 96, 0.4)',
              padding: 8,
              borderRadius: 6,
              marginBottom: 8,
              color: '#e94560'
            }}
          >
            {errors.map((e, i) => <div key={i}>{e}</div>)}
          </div>
        )}

        <textarea
          value={code}
          onChange={(e) => updateCode(e.target.value)}
          style={{
            width: '100%',
            minHeight: 420,
            background: '#0f172a',
            color: '#e5e7eb',
            border: '1px solid #334155',
            borderRadius: 8,
            padding: 12,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: 13,
            lineHeight: 1.6,
            resize: 'vertical'
          }}
        />
      </div>

      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ color: '#4cc9f0', fontWeight: 'bold', marginBottom: 12 }}>参数面板</div>
        {params.length === 0 ? (
          <div style={{ color: '#666' }}>点击“验证”解析参数</div>
        ) : (
          params.map((p, i) => (
            <div key={i} style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span>{p.name}</span>
                <span>{p.default}</span>
              </div>
              <input type="range" min={p.min} max={p.max} defaultValue={p.default} style={{ width: '100%' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#666' }}>
                <span>{p.min}</span>
                <span>{p.max}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
