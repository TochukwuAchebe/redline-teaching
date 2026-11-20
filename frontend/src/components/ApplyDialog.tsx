import { useEffect, useRef } from 'react'
import type { Decision } from '../client'
import type { Finding } from '../types'
import { isAuto } from '../brandScore'

/** Confirm what will change before anything is written, grouped the way a reviewer thinks. */
export function ApplyDialog({ findings, decisions, upgrades, busy, onCancel, onApply }: {
  findings: Finding[]
  decisions: Record<string, Decision>
  upgrades: number[]
  busy: boolean
  onCancel: () => void
  onApply: () => void
}) {
  const dialog = useRef<HTMLDivElement>(null)
  useEffect(() => {
    dialog.current?.querySelector<HTMLButtonElement>('.btn-primary')?.focus()
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onCancel])
  const fixes = findings.filter((f) => decisions[f.id] === 'accepted' && isAuto(f))
  const groups = new Map<string, Finding[]>()
  for (const f of fixes) groups.set(f.title, [...(groups.get(f.title) ?? []), f])
  const todos = findings.filter((f) => decisions[f.id] === 'todo' || (decisions[f.id] === 'accepted' && !isAuto(f))).length
  const total = fixes.length + upgrades.length
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div ref={dialog} className="dialog" role="dialog" aria-modal="true" aria-labelledby="apply-title">
        <p className="eyebrow">Step 3 of 4</p>
        <h2 id="apply-title" className="h-dialog">Apply {total} change{total === 1 ? '' : 's'}?</h2>
        <p className="dialog-text">Redline edits a copy of your deck, then checks it again to confirm every change landed. Your original stays exactly as you uploaded it.</p>
        <ul className="apply-list">
          {[...groups.entries()].map(([title, items]) => (
            <li key={title}>
              <span className="count">{items.length}</span>
              <span><b>{title}</b><span className="fine">Slide{new Set(items.map((i) => i.slide)).size === 1 ? '' : 's'} {[...new Set(items.map((i) => i.slide))].sort((a, b) => a - b).join(', ')}</span></span>
            </li>
          ))}
          {upgrades.map((n) => (
            <li key={`u${n}`}>
              <span className="count">1</span>
              <span><b>Redesign of slide {n}</b><span className="fine">The suggested layout; any table is kept on an appendix slide</span></span>
            </li>
          ))}
        </ul>
        {todos > 0 && <p className="dialog-note"><b>{todos}</b> {todos === 1 ? 'item stays on your to-do list. It needs a person, so Redline leaves it alone.' : 'items stay on your to-do list. They need a person, so Redline leaves them alone.'}</p>}
        <div className="dialog-actions">
          <button className="btn btn-ghost" onClick={onCancel}>Keep reviewing</button>
          <button className="btn btn-primary btn-lg" onClick={onApply} disabled={busy || !total}>{busy ? 'Starting…' : `Apply ${total} change${total === 1 ? '' : 's'}`}</button>
        </div>
      </div>
    </div>
  )
}
