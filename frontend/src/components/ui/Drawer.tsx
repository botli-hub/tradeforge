import { useEffect, type ReactNode } from 'react'

type Props = {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  /** sheet = bottom sheet on all sizes; auto uses sheet on narrow */
  mode?: 'drawer' | 'sheet' | 'auto'
}

export default function Drawer({ open, onClose, title, subtitle, children, mode = 'auto' }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  if (!open) return null

  const isNarrow = typeof window !== 'undefined' && window.innerWidth < 900
  const asSheet = mode === 'sheet' || (mode === 'auto' && isNarrow)

  return (
    <>
      <div className="drawer-mask" onClick={onClose} aria-hidden />
      <aside
        className={`drawer-panel${asSheet ? ' sheet' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="drawer-header">
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{title}</div>
            {subtitle && <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{subtitle}</div>}
          </div>
          <button type="button" className="btn-icon" aria-label="关闭" onClick={onClose}>✕</button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  )
}
