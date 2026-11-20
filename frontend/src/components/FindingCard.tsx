import { forwardRef, useState } from 'react'
import type { Decision, FixRow } from '../client'
import { SEVERITY_LABEL, SEVERITY_NAME } from '../severity'
import type { Finding } from '../types'
import { Spark, Undo } from './Icons'

export interface GroupInfo {
  slides: number[]
  total: number
}

interface Props {
  finding: Finding
  number: number
  decision: Decision | undefined
  selected: boolean
  group: GroupInfo | null
  onSelect: () => void
  onHover: (hovering: boolean) => void
  onDecide: (value: Decision | null, applyToGroup: boolean) => void
  onGoToSlide: (n: number) => void
  /** Redline can make this change itself. */
  fixable: boolean
  fixRow?: FixRow
}

const HEX = /#([0-9A-F]{6})\b/i

function Swatch({ text }: { text: string }) {
  const hex = text.match(HEX)
  return hex ? <span className="swatch" style={{ background: `#${hex[1]}` }} aria-hidden="true" /> : null
}

export type Tone = 'willfix' | 'fixed' | 'todo' | 'manual' | 'dismissed'

/** What a decision does, or did, to the deck, in the reviewer's words. */
export function outcome(decision: Decision | undefined, fixable: boolean, row?: FixRow): { label: string; tone: Tone } | null {
  if (decision === 'rejected') return { label: 'Rejected', tone: 'dismissed' }
  if (decision === 'todo') return { label: 'On your to-do list', tone: 'todo' }
  if (decision === 'accepted' && row) {
    if (row.status === 'applied') return { label: row.verified === null ? 'Changed' : 'Fixed', tone: 'fixed' }
    return { label: 'Needs a hand edit', tone: 'manual' }
  }
  if (decision === 'accepted') return fixable ? { label: 'Will be fixed', tone: 'willfix' } : { label: 'On your to-do list', tone: 'todo' }
  return null
}

/** The change itself, the way a tracked change reads: old wording struck through, new wording beside it. */
export function Change({ f }: { f: Finding }) {
  const comparison = f.rule_id === 'KEY_ORDER_INCONSISTENT'
  if (f.origin === 'ai' && f.rule_id === 'BULLET_PARALLEL' && f.quote) {
    return <blockquote className="change-quote">“{f.quote}”</blockquote>
  }
  if (f.origin === 'ai' && f.quote && f.suggested_fix) {
    return (
      <p className="change">
        <del>{f.quote}</del> <span className="change-arrow" aria-hidden="true">→</span>
        <span className="sr-only"> suggested: </span><ins>{f.suggested_fix}</ins>
      </p>
    )
  }
  if (!f.actual) return null
  return (
    <p className="change">
      <del className={comparison ? 'is-compare' : ''}><Swatch text={f.actual} />{f.actual}</del>
      {f.expected && (
        <>
          <span className="change-arrow" aria-hidden="true">{comparison ? '↔' : '→'}</span>
          <span className="sr-only">{comparison ? ' compared with ' : ' should be '}</span>
          <ins><Swatch text={f.expected} />{f.expected}</ins>
        </>
      )}
    </p>
  )
}

export function summary(f: Finding): string {
  if (f.origin === 'ai' && f.quote && f.suggested_fix) return `${f.quote} → ${f.suggested_fix}`
  if (f.actual && f.expected && f.rule_id !== 'KEY_ORDER_INCONSISTENT') return `${f.actual} → ${f.expected}`
  return f.title
}

export const FindingCard = forwardRef<HTMLElement, Props>(function FindingCard(
  { finding: f, number, decision, selected, group, onSelect, onHover, onDecide, onGoToSlide, fixable, fixRow },
  ref,
) {
  const [groupPage, setGroupPage] = useState(0)
  const pageSize = 24
  const pages = Math.max(1, Math.ceil((group?.slides.length ?? 0) / pageSize))
  const page = Math.min(groupPage, pages - 1)
  const grouped = group && group.total > 1
  const isAi = f.origin === 'ai'
  const result = outcome(decision, fixable, fixRow)
  const accept: Decision = fixable ? 'accepted' : 'todo'
  const category = f.rule_ref.split('·')[0].split('›').pop()?.trim()

  if (decision && !selected) {
    return (
      <article ref={ref} className={`decided is-${result!.tone}`} data-finding-id={f.id} onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}>
        <span className={`state state-${result!.tone}`}>{result!.label}</span>
        <h3 className="decided-title">
          <button type="button" className="card-select" onClick={onSelect} aria-label={`${f.title}: ${result!.label}. Show details`}>
            {summary(f)}
          </button>
        </h3>
        {!fixRow && (
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => onDecide(null, false)} aria-label={`Undo decision on ${f.title}`}>
            <Undo /> Undo
          </button>
        )}
      </article>
    )
  }

  return (
    <article
      ref={ref}
      className={`card sev-${f.severity}${selected ? ' is-selected' : ''}`}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      aria-current={selected ? 'true' : undefined}
      data-finding-id={f.id}
    >
      <div className="card-head">
        <span className={`pill pill-${SEVERITY_NAME[f.severity]}`}><span className="dot" />{SEVERITY_LABEL[f.severity]}</span>
        {category && <span className="card-cat">{category}</span>}
        {isAi && <span className="ai-tag" title="Suggested by the Redline agent: it quotes the slide, and nothing changes unless you accept it"><Spark /> Agent suggestion</span>}
        <span className="card-num" aria-hidden="true">{number}</span>
      </div>
      <h3 className="card-title">
        <button
          type="button"
          className="card-select"
          aria-controls={selected ? `finding-detail-${f.id}` : undefined}
          aria-label={`${SEVERITY_LABEL[f.severity]} ${number}: ${f.title}${selected ? '' : '. Show details'}`}
          onClick={(event) => { event.stopPropagation(); onSelect() }}
        >
          {f.title}
        </button>
      </h3>
      {selected && (
        <div className="card-detail" id={`finding-detail-${f.id}`}>
          <Change f={f} />
          <p className="card-why">{f.message}</p>
          {isAi && f.quote && !f.suggested_fix && f.rule_id !== 'BULLET_PARALLEL' && <blockquote className="change-quote">“{f.quote}”</blockquote>}
          {result && <p className={`state state-${result.tone}`}>{result.label}{fixRow?.note ? ` · ${fixRow.note}` : ''}</p>}
          {grouped && (
            <div className="group" onClick={(e) => e.stopPropagation()}>
              <p className="group-title">Same issue in {group.total} places{group.slides.length > 1 ? ` on ${group.slides.length} slides` : ' on this slide'}</p>
              <div className="chips">
                {group.slides.slice(page * pageSize, (page + 1) * pageSize).map((n) => (
                  <button
                    key={n}
                    className={`chip${n === f.slide ? ' is-current' : ''}`}
                    onClick={() => n !== f.slide && onGoToSlide(n)}
                    aria-current={n === f.slide ? 'true' : undefined}
                    aria-label={n === f.slide ? `Slide ${n} (this slide)` : `Go to slide ${n}`}
                  >
                    {n}
                  </button>
                ))}
              </div>
              {pages > 1 && (
                <div className="row">
                  <button type="button" className="btn btn-ghost btn-xs" disabled={page === 0} onClick={() => setGroupPage(page - 1)}>Previous</button>
                  <span className="fine">Page {page + 1} of {pages}</span>
                  <button type="button" className="btn btn-ghost btn-xs" disabled={page === pages - 1} onClick={() => setGroupPage(page + 1)}>More</button>
                </div>
              )}
            </div>
          )}
          <div className="card-actions" onClick={(e) => e.stopPropagation()}>
            {/* Pressing a decision again never reverses it; Undo is the one way back. */}
            <button className="btn btn-primary btn-sm" aria-pressed={decision === accept} onClick={() => decision !== accept && onDecide(accept, false)}>
              {decision === accept ? 'Accepted' : `Accept${grouped ? ' this one' : ''}`}
            </button>
            <button className="btn btn-secondary btn-sm" aria-pressed={decision === 'rejected'} onClick={() => decision !== 'rejected' && onDecide('rejected', false)}>
              {decision === 'rejected' ? 'Rejected' : 'Reject'}
            </button>
            {decision && <button className="btn btn-ghost btn-sm" onClick={() => onDecide(null, false)}>Undo</button>}
            {grouped && (
              <>
                <button className="btn btn-ghost btn-sm" onClick={() => onDecide(accept, true)}>Accept all {group.total}</button>
                <button className="btn btn-ghost btn-sm" onClick={() => onDecide('rejected', true)}>Reject all</button>
              </>
            )}
          </div>
          <p className="card-consequence">
            {fixable ? 'Accept and Redline makes this change in a copy when you apply.' : 'Accept to put it on your to-do list; this change needs a person.'}
            {fixable && decision !== 'todo' && (
              <> <button type="button" className="link-btn" onClick={(e) => { e.stopPropagation(); onDecide('todo', false) }}>I’ll change it myself</button></>
            )}
          </p>
        </div>
      )}
    </article>
  )
})
