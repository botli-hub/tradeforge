/** 标的立场：只收租 / 允许接货。默认 acquire。 */
import { useState } from 'react'
import { API_BASE } from '../../services/api'

export type Stance = 'income' | 'acquire'

export function normalizeStance(raw: unknown): Stance {
  const s = String(raw || '').trim().toLowerCase()
  if (s === 'income' || s === '只收租') return 'income'
  return 'acquire'
}

export async function patchTargetStance(symbol: string, stance: Stance): Promise<Stance> {
  const res = await fetch(
    `${API_BASE}/api/wheel/targets/${encodeURIComponent(symbol)}/stance`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stance }),
    },
  )
  const text = await res.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { /* */ }
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || text || `立场更新失败: ${res.status}`)
  }
  return normalizeStance(data?.stance ?? stance)
}

type Props = {
  symbol: string
  stance?: string | null
  disabled?: boolean
  onSaved?: (stance: Stance) => void
}

/** 标的表单元格：直接改立场 */
export default function TargetStanceSelect({ symbol, stance, disabled, onSaved }: Props) {
  const [value, setValue] = useState<Stance>(normalizeStance(stance))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const onChange = async (next: Stance) => {
    const prev = value
    setValue(next)
    setErr(null)
    setBusy(true)
    try {
      const saved = await patchTargetStance(symbol, next)
      setValue(saved)
      onSaved?.(saved)
    } catch (e: any) {
      setValue(prev)
      setErr(e?.message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <span title="只收租=接货当预警、更早腾仓；允许接货=floor 是愿接股东价，临期 ITM 走准备接货">
      <select
        value={value}
        disabled={disabled || busy}
        style={{
          display: 'block', width: 110, padding: '4px 6px',
          background: 'var(--bg-secondary)', border: '1px solid var(--border)',
          borderRadius: 4, color: 'var(--text)', fontSize: 13,
        }}
        onChange={e => onChange(e.target.value === 'income' ? 'income' : 'acquire')}
      >
        <option value="acquire">允许接货</option>
        <option value="income">只收租</option>
      </select>
      {err && <span style={{ color: 'var(--red)', fontSize: 11 }}>{err}</span>}
    </span>
  )
}
