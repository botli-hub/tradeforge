import type { ReactNode } from 'react'

type Props = {
  title: string
  description?: string
  children?: ReactNode
}

export default function EmptyState({ title, description, children }: Props) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {children && <div className="empty-actions">{children}</div>}
    </div>
  )
}
