import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export type ToastKind = 'info' | 'success' | 'error'

type ToastItem = { id: number; message: string; kind: ToastKind }

type ToastApi = {
  toast: (message: string, kind?: ToastKind) => void
}

const ToastContext = createContext<ToastApi>({ toast: () => {} })

let _id = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const toast = useCallback((message: string, kind: ToastKind = 'info') => {
    const id = _id++
    setItems(prev => [...prev.slice(-4), { id, message, kind }])
    setTimeout(() => setItems(prev => prev.filter(x => x.id !== id)), 2800)
  }, [])

  const api = useMemo(() => ({ toast }), [toast])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-root" aria-live="polite">
        {items.map(t => (
          <div key={t.id} className={`toast-item ${t.kind}`}>{t.message}</div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}
