import { api } from '../api'
import type { DeckState, FixState } from '../client'
import type { Finding } from '../types'
import { summary } from './FindingCard'
import { DownloadIcon } from './Icons'
import { ScoreRing } from './ScoreChip'
import { Check } from './Stepper'

interface Change { slide: number; title: string; detail: string; finding?: Finding; redesign?: boolean }

const pct = (v: number, of: number) => `${(v / of) * 100}%`

/** Step 3: what actually changed, side by side, outlined and written down. */
export function ChangesView({ deck, fix, findings, slide, onSlide, ratio, startScore, score, onBack, onFinish }: {
  deck: DeckState
  fix: FixState
  findings: Finding[]
  slide: number
  onSlide: (n: number) => void
  ratio: number
  startScore: number
  score: number
  onBack: () => void
  onFinish: () => void
}) {
  const id = deck.deck_id
  const { slide_width: W, slide_height: H } = deck.result!
  const byId = new Map(findings.map((f) => [f.id, f]))
  const changes: Change[] = []
  const manual: Change[] = []
  for (const [fid, row] of Object.entries(fix.report ?? {})) {
    const f = byId.get(fid)
    if (!f) continue
    const item = { slide: f.slide, title: f.title, detail: summary(f), finding: f }
    if (row.status === 'applied') changes.push(item)
    else manual.push({ ...item, detail: row.note })
  }
  const appendix: number[] = []
  for (const [n, u] of Object.entries(fix.upgrades ?? {})) {
    if (u.status === 'applied') {
      changes.push({ slide: Number(n), title: 'Slide redesigned', detail: u.appendix_slide ? `New layout; full table on slide ${u.appendix_slide}` : 'New layout', redesign: true })
      if (u.appendix_slide) appendix.push(u.appendix_slide)
    } else manual.push({ slide: Number(n), title: 'Redesign not applied', detail: u.note ?? '' })
  }
  changes.sort((a, b) => a.slide - b.slide)
  const slides = [...new Set(changes.map((c) => c.slide))]
  const here = changes.filter((c) => c.slide === slide)
  const redesigned = here.some((c) => c.redesign)
  const boxes = redesigned ? [] : here.filter((c) => c.finding?.bbox)
  const version = fix.version ?? 0

  const outline = (tone: 'before' | 'after') => boxes.map((c, i) => {
    const b = c.finding!.bbox!
    return (
      <span key={c.finding!.id} className={`change-box is-${tone}`} style={{ left: pct(b.x, W), top: pct(b.y, H), width: pct(b.w, W), height: pct(b.h, H) }}>
        <i>{i + 1}</i>
      </span>
    )
  })

  return (
    <div className="review-body">
      <nav className="rail" aria-label="Changed slides">
        {[...slides, ...appendix].map((n) => (
          <button key={n} className={`thumb${n === slide ? ' is-current' : ''}`} onClick={() => onSlide(n)} aria-current={n === slide ? 'true' : undefined}
            aria-label={appendix.includes(n) ? `Slide ${n}, new appendix slide` : `Slide ${n}`}>
            <span className="thumb-img" style={{ aspectRatio: String(ratio) }}><img src={api.fixedSlideUrl(id, n, version)} alt="" loading="lazy" /></span>
            <span className="thumb-meta"><span className="thumb-num">{n}</span>{appendix.includes(n) ? <span className="thumb-new">New</span> : <span className="thumb-done" aria-hidden="true">✓</span>}</span>
          </button>
        ))}
      </nav>
      <main className="stage-area">
        <div className="stage-head">
          <span className="stage-title">Slide {slide} <span className="faint">· {appendix.includes(slide) ? 'new appendix slide' : redesigned ? 'redesigned' : `${here.length} change${here.length === 1 ? '' : 's'}`}</span></span>
        </div>
        <div className={`compare${appendix.includes(slide) ? ' is-single' : ''}`}>
          {!appendix.includes(slide) && (
            <figure>
              <figcaption><span className="dot dot-must" />Before <span className="faint">as uploaded</span></figcaption>
              <div className="compare-slide" style={{ aspectRatio: String(ratio) }}><img src={api.slideUrl(id, slide)} alt={`Slide ${slide} before`} />{outline('before')}</div>
            </figure>
          )}
          <figure>
            <figcaption><span className="dot dot-done" />After <span className="faint">changed and checked again</span></figcaption>
            <div className="compare-slide" style={{ aspectRatio: String(ratio) }}><img src={api.fixedSlideUrl(id, slide, version)} alt={`Slide ${slide} after`} />{outline('after')}</div>
          </figure>
        </div>
        {here.length > 0 && (
          <ol className="here-list">
            {here.map((c, i) => (
              <li key={i}><span className="count is-done">{redesigned ? '✓' : i + 1}</span><span><b>{c.title}</b><span className="fine">{c.detail}</span></span></li>
            ))}
          </ol>
        )}
      </main>
      <aside className="panel" aria-label="Every change">
        <div className="panel-head">
          <div className="done-head">
            <span className="done-icon"><Check size={14} /></span>
            <div>
              <h2 className="h-small">{changes.length} change{changes.length === 1 ? '' : 's'} applied</h2>
              <p className="fine">Checked again{manual.length ? ` · ${manual.length} ${manual.length === 1 ? 'needs' : 'need'} a hand edit` : ' · every change confirmed'}</p>
            </div>
          </div>
          <div className="score-band">
            <ScoreRing score={score} size={36} />
            <div><p className="fine">Brand score</p><p className="score-big">{startScore !== score && <s>{startScore}</s>} {score}</p></div>
            <p className="fine score-note">Re-checked, averaged across slides</p>
          </div>
        </div>
        <div className="change-list">
          <p className="eyebrow">Every change</p>
          <ul>
            {changes.map((c, i) => (
              <li key={i}>
                <button className={`change-row${c.slide === slide ? ' is-current' : ''}`} onClick={() => onSlide(c.slide)}>
                  <span className="faint">Slide {c.slide}</span>
                  <span className="change-row-text"><b>{c.title}</b><span>{c.detail}</span></span>
                  <span className="tick" aria-hidden="true"><Check /></span>
                </button>
              </li>
            ))}
          </ul>
          {manual.length > 0 && (
            <>
              <p className="eyebrow">Need a hand edit</p>
              <ul>{manual.map((c, i) => <li key={i}><button className="change-row" onClick={() => onSlide(c.slide)}><span className="faint">Slide {c.slide}</span><span className="change-row-text"><b>{c.title}</b><span>{c.detail}</span></span></button></li>)}</ul>
            </>
          )}
        </div>
        <div className="panel-foot row">
          <a className="btn btn-primary grow" href={api.correctedDeckUrl(id)} download onClick={() => setTimeout(onFinish, 300)}><DownloadIcon /> Download deck</a>
          <button className="btn btn-secondary" onClick={onBack}>Keep reviewing</button>
        </div>
      </aside>
    </div>
  )
}
