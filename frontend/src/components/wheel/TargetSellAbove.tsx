/** 标的愿卖价:CC strike 锚,与 Put 愿接价 floor_price 分开。0/空=未设。 */
import { useEffect, useState } from 'react'
import { API_BASE } from '../../services/api'

export async function patchTargetSellAbove(
  symbol: string,
  sellAbove: number | null,
): Promise<number | null> {
  const res = await fetch(
    `${API_BASE}/api/wheel/targets/${encodeURIComponent(symbol)}/sell-above`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sell_above: sellAbove }),
    },
  )
  const text = await res.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { /* */ }
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || text || `愿卖价更新失败: ${res.status}`)
  }
  const v = data?.sell_above
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : null
}

type Props = {
  symbol: string
  value?: number | null
  disabled?: boolean
  onSaved?: (v: number | null) => void
}

export default function TargetSellAbove({ symbol, value, disabled, onSaved }: Props) {
  const toStr = (v?: number | null) => (v != null && Number(v) > 0 ? String(v) : '')
  const [draft, setDraft] = useState(toStr(value))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { setDraft(toStr(value)) }, [value, symbol])

  const commit = async () => {
    const trimmed = draft.trim()
    const next = trimmed === '' ? null : Number(trimmed)
    if (next != null && (!Number.isFinite(next) || next < 0)) {
      setErr('请填有效数字')
      return
    }
    const normalized = next != null && next > 0 ? next : null
    const prev = toStr(value)
    if ((normalized == null && prev === '') || (normalized != null && prev === String(normalized))) {
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const saved = await patchTargetSellAbove(symbol, normalized)
      setDraft(toStr(saved))
      onSaved?.(saved)
    } catch (e: any) {
      setDraft(prev)
      setErr(e?.message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <span title="愿卖价:CC strike 下限锚 max(成本基础, 愿卖价);空=只用成本;与 Put 愿接价分开">
      <input
        type="number"
        step="any"
        min={0}
        placeholder="—"
        value={draft}
        disabled={disabled || busy}
        style={{
          width: 72, padding: '3px 6px',
          background: 'var(--bg-secondary)', border: '1px solid var(--border)',
          borderRadius: 4, color: 'var(--text)', fontSize: 13,
        }}
        onChange={e => setDraft(e.target.value)}
        onBlur={() => { void commit() }}
        onKeyDown={e => { if (e.key === 'Enter') { e.currentTarget.blur() } }}
      />
      {err && <span style={{ color: 'var(--red)', fontSize: 11, display: 'block' }}>{err}</span>}
    </span>
  )
}
