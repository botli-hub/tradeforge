import { useEffect, useRef, useState, type ReactNode } from 'react'

export type MenuAction = {
  label: string
  onClick: () => void
  disabled?: boolean
  title?: string
}

export default function KebabMenu({ items, ariaLabel = '更多操作' }: { items: MenuAction[]; ariaLabel?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="menu-wrap" ref={ref}>
      <button
        type="button"
        className="btn-icon"
        aria-label={ariaLabel}
        aria-expanded={open}
        title={ariaLabel}
        onClick={() => setOpen(v => !v)}
      >
        ⋯
      </button>
      {open && (
        <div className="menu-pop" role="menu">
          {items.map(it => (
            <button
              key={it.label}
              type="button"
              className="menu-item"
              role="menuitem"
              disabled={it.disabled}
              title={it.title || it.label}
              onClick={() => {
                if (it.disabled) return
                setOpen(false)
                it.onClick()
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function MenuSlot({ children }: { children: ReactNode }) {
  return <div className="menu-wrap">{children}</div>
}
