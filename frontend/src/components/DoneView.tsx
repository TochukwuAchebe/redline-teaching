import { api } from '../api'
import type { DeckState, Decision, FixState } from '../client'
import type { Finding } from '../types'
import { isAuto } from '../brandScore'
import { Arrow, DownloadIcon } from './Icons'
import { ScoreRing } from './ScoreChip'

/** Step 4: the deck is ready; what's left is the reviewer's short to-do list. */
export function DoneView({ deck, fix, findings, decisions, startScore, score, onExport, onNewDeck, onBack, onGoTo }: {
  deck: DeckState
  fix: FixState | null
  findings: Finding[]
  decisions: Record<string, Decision>
  startScore: number
  score: number
  onExport: () => void
  onNewDeck: () => void
  onBack: () => void
  onGoTo: (slide: number) => void
}) {
  const done = fix?.state === 'done'
  const fixed = Object.values(fix?.report ?? {}).filter((r) => r.status === 'applied').length
  const redesigned = Object.values(fix?.upgrades ?? {}).filter((u) => u.status === 'applied').length
  const todo = findings.filter((f) => decisions[f.id] === 'todo' || (decisions[f.id] === 'accepted' && (!isAuto(f) || fix?.report?.[f.id]?.status === 'manual')))
  const rejected = findings.filter((f) => decisions[f.id] === 'rejected').length
  const manualChecks = deck.result?.coverage?.manual_checks ?? []
  // Count decisions, as the review does: a repeated issue is one decision however many places it appears.
  const open = new Set(findings.filter((f) => !decisions[f.id]).map((f) => f.group_id ?? f.id))
  const undecided = open.size
  return (
    <main className="done">
      <div className="done-main">
        <h1 className="display display-md">{done ? <>Your deck<br />is ready.</> : <>Review<br />complete.</>}</h1>
        <p className="lede">
          {done
            ? `Every accepted change was made in a copy and checked again.${todo.length ? ` ${todo.length === 1 ? 'One item on your to-do list needs' : `${todo.length} items on your to-do list need`} you.` : ''}${undecided ? ` ${undecided} decision${undecided === 1 ? ' is' : 's are'} still open; those issues are left as they are.` : todo.length ? '' : ' Nothing is left for you to change.'}`
            : 'Download the report to share what you decided, or apply the changes you accepted.'}
        </p>
        <div className="row done-actions">
          {done && <a className="btn btn-primary btn-lg" href={api.correctedDeckUrl(deck.deck_id)} download><DownloadIcon /> Download corrected deck</a>}
          <button className="btn btn-secondary btn-lg" onClick={onExport}>Review report (CSV)</button>
        </div>
        <dl className="stats">
          <div><dt className="is-done">{fixed}</dt><dd>fixed for you</dd></div>
          {redesigned > 0 && <div><dt>{redesigned}</dt><dd>slide{redesigned === 1 ? '' : 's'} redesigned</dd></div>}
          <div><dt>{todo.length}</dt><dd>on your to-do list</dd></div>
          <div><dt className="faint">{rejected}</dt><dd>rejected</dd></div>
        </dl>
        <div className="row done-links">
          <button className="link-btn" onClick={onBack}>{undecided ? `Review the ${undecided} undecided` : 'Back to the review'}</button>
          <button className="link-btn" onClick={onNewDeck}>Check another deck <Arrow /></button>
        </div>
      </div>
      <div className="done-side">
        <div className="score-card">
          <ScoreRing score={score} size={96} />
          <div>
            <p className="fine">Brand score</p>
            <p className="score-sentence">{startScore !== score ? <>Re-checked after your fixes: up from <b>{startScore}</b> when you uploaded</> : 'As checked: open must-fix −25, should-fix −10 per slide'}</p>
          </div>
          <span className="score-value">{score}</span>
        </div>
        <section className="todo">
          <h2 className="h-small">Your to-do list</h2>
          {todo.length === 0 ? <p className="fine">Nothing on your to-do list.</p> : (
            <ul>
              {todo.map((f) => (
                <li key={f.id}>
                  <img src={api.slideUrl(deck.deck_id, f.slide)} alt="" loading="lazy" />
                  <span><b>{f.title}</b><span className="fine">Slide {f.slide} · {f.message}</span></span>
                  <button className="btn btn-ghost btn-xs" onClick={() => onGoTo(f.slide)}>View</button>
                </li>
              ))}
            </ul>
          )}
        </section>
        {manualChecks.length > 0 && (
          <details className="by-eye card-like">
            <summary>What Redline can’t check · {manualChecks.length} items to look at by eye</summary>
            <ul>{manualChecks.map((t) => <li key={t}>{t}</li>)}</ul>
          </details>
        )}
      </div>
    </main>
  )
}
