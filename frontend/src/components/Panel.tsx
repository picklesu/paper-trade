import type { ReactNode } from 'react'

interface Props {
  title: string
  className?: string
  children?: ReactNode
}

export function Panel({ title, className = '', children }: Props) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <h2 className="panel-title">{title}</h2>
      <div className="panel-body">{children}</div>
    </section>
  )
}

export function Placeholder({ text }: { text: string }) {
  return <div className="placeholder">{text}</div>
}
