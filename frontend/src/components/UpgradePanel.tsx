import { useState } from 'react'
import { api } from '../api'
import { ApiError, type UpgradeItem } from '../client'
import { Check } from './Stepper'
import { Spark } from './Icons'

/** An optional, agent-suggested redesign of this slide. It follows the ACME guidelines,
 *  every figure is checked against the slide, and nothing changes unless the reviewer accepts it. */
export function UpgradePanel({ deckId, slide, kind, item, onChange, onShow, onToast }: {
  deckId: string
  slide: number
  kind?: 'chart' | 'text'
  item?: UpgradeItem
  onChange: (item: UpgradeItem) => void
  onShow: () => void
  onToast: (text: string, undo?: boolean) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function suggest() {
    setBusy(true)
    setError(null)
    try {
      const next = await api.generateUpgrade(deckId, slide)
      onChange(next)
      if (next.state === 'ready') onShow()
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function choose(choice: 'use' | null) {
    try {
      onChange(await api.chooseUpgrade(deckId, slide, choice))
      onToast(choice ? 'Redesign accepted: it’s made when you apply' : 'Original slide kept')
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError((e as Error).message)
    }
  }

  if (!item || busy) {
    return (
      <div className="upgrade">
        <p className="ai-line"><Spark /> Redline agent · optional</p>
        <p className="h-small">A clearer layout for this slide</p>
        <p className="fine-strong">
          The guidelines ask for headlines that state the takeaway, clear separation and clarity over density. The Redline
          agent can suggest a redesign that follows them: a takeaway headline{kind === 'text'
            ? ' and panels built from the slide’s own sentences.'
            : ', the key figures as a chart with its key beneath it, and panels built from the slide’s own sentences. The table is kept, unchanged, on an appendix slide.'}
          {' '}The agent only chooses; every sentence it shows is the slide’s own or a comparison computed from the table.
        </p>
        <button className="btn btn-primary" onClick={suggest} disabled={busy}>{busy ? 'Designing… about 20 seconds' : 'Suggest a redesign'}</button>
        {busy && <div className="bar"><span className="is-indeterminate" /></div>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <p className="fine">Not counted as an issue. Your slide stays as it is unless you accept the redesign.</p>
      </div>
    )
  }

  if (item.state !== 'ready' || !item.proposal) {
    return (
      <div className="upgrade">
        <p className="ai-line"><Spark /> Redline agent</p>
        <p className="h-small">No redesign for this slide</p>
        <p className="fine-strong">{item.note ?? 'Redline couldn’t suggest a redesign it could check against the slide, so it suggests none.'}</p>
        <button className="btn btn-secondary btn-sm" onClick={suggest}>Try again</button>
      </div>
    )
  }

  const p = item.proposal
  const accepted = item.choice === 'use'
  return (
    <div className="upgrade">
      <p className="ai-line"><Spark /> Redline agent · checked against the slide</p>
      <p className="h-small">A clearer slide {slide}</p>
      <div className="upgrade-headline">
        <p className="eyebrow">Headline</p>
        <p><del>{p.original_headline}</del></p>
        <p><ins className="is-strong">{p.headline}</ins></p>
      </div>
      <ol className="basis">
        {(p.basis ?? []).map((b, i) => (
          <li key={b.change}>
            <span className="basis-num" aria-hidden="true">{i + 1}</span>
            <span><b>{b.change}</b><span className="fine">{b.guideline}</span></span>
          </li>
        ))}
      </ol>
      {p.issues_before !== undefined && p.issues_after !== undefined && (
        <p className="checked"><Check /> Guideline issues on this slide: {p.issues_before} before, {p.issues_after} after.</p>
      )}
      <p className="checked"><Check /> {p.headline_source === 'template'
        ? 'The headline is computed from the table; every bullet is a sentence from the slide, unchanged.'
        : 'The headline and every bullet are sentences from the slide, unchanged.'}</p>
      <p className="fine">Quoting the slide keeps its wording, figures and units, but a new selection and layout can shift emphasis or context. Check it reads right before you accept.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="upgrade-actions">
        {accepted ? (
          <>
            <span className="state state-willfix">Will be made when you apply</span>
            <button className="btn btn-ghost btn-sm" onClick={() => choose(null)}>Undo</button>
          </>
        ) : (
          <>
            <button className="btn btn-primary" onClick={() => choose('use')}>Accept redesign</button>
            <button className="btn btn-secondary" onClick={() => choose(null)}>Keep original</button>
          </>
        )}
      </div>
    </div>
  )
}
