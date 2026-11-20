import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent } from 'react'
import type { Decision } from '../client'
import type { Finding } from '../types'
import { highlightHits } from '../highlightHits'
import { isAuto } from '../brandScore'

interface Props {
  src: string
  slide: number
  slideWidth: number
  slideHeight: number
  findings: Finding[]
  numbers: Map<string, number>
  decisions: Record<string, Decision>
  selectedId: string | null
  hoveredId: string | null
  onSelect: (f: Finding) => void
}

const pct = (v: number, of: number) => `${(v / of) * 100}%`
const QUIET_ABOVE = 5

/** The box marks where an absent element should be, rather than an element that exists. */
export const isMissingElement = (f: Finding) => f.rule_id === 'CONF_MISSING' || (f.element_id === null && f.bbox !== null)

export function SlideStage({ src, slide, slideWidth, slideHeight, findings, numbers, decisions, selectedId, hoveredId, onSelect }: Props) {
  const [failed, setFailed] = useState<string | null>(null)
  const [choices, setChoices] = useState<{ items: Finding[]; x: number; y: number } | null>(null)
  const choicePanel = useRef<HTMLDivElement>(null)
  const choiceTrigger = useRef<HTMLButtonElement | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const [available, setAvailable] = useState({ width: 0, height: 0 })
  const [zoom, setZoom] = useState(1)
  const ratio = slideWidth / slideHeight

  useEffect(() => {
    const node = viewport.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) => {
      setAvailable({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  // Measure the remaining space after all toolbars, including Before/After.
  // A viewport-height estimate clips the footer when another control appears.
  const fittedWidth = Math.max(1, Math.min(available.width - 16, (available.height - 16) * ratio))

  useEffect(() => setChoices(null), [slide, src, findings])
  useEffect(() => {
    if (choices) choicePanel.current?.querySelector<HTMLButtonElement>('button')?.focus()
  }, [choices])

  function chooseAt(event: MouseEvent<HTMLButtonElement>, finding: Finding) {
    // Keyboard activation identifies the intended control without a pointer hit.
    if (event.detail === 0) { setChoices(null); onSelect(finding); return }
    const stage = event.currentTarget.parentElement!.getBoundingClientRect()
    const x = event.clientX - stage.left, y = event.clientY - stage.top
    const items = highlightHits(findings, x / stage.width * slideWidth, y / stage.height * slideHeight,
      3 / stage.width * slideWidth)
    if (items.length < 2) { setChoices(null); onSelect(finding); return }
    choiceTrigger.current = event.currentTarget
    setChoices({ items, x: Math.max(8, Math.min(x, stage.width - Math.min(320, stage.width - 16) - 8)),
      y: Math.max(8, Math.min(y, stage.height - 220)) })
  }

  // Larger boxes first so smaller ones stay clickable on top.
  const boxes = findings
    .filter((f) => f.bbox)
    .sort((a, b) => b.bbox!.w * b.bbox!.h - a.bbox!.w * a.bbox!.h)

  return (
    <div className="slide-inspector" style={{ '--slide-ratio': ratio } as CSSProperties}>
      <div className="slide-inspector-tools">
        <label>Zoom{' '}
          <select aria-label="Slide size" value={zoom} onChange={(event) => { setZoom(Number(event.target.value)); setChoices(null) }}>
            <option value={1}>Fit slide</option>
            <option value={1.5}>Enlarge 150%</option>
            <option value={2}>Enlarge 200%</option>
            <option value={3}>Enlarge 300%</option>
          </select>
        </label>
        {zoom > 1 && <span id="slide-pan-hint">Scroll to inspect the enlarged slide.</span>}
      </div>
      <div ref={viewport} className="stage-viewport" tabIndex={0}
        role="region" aria-label="Slide preview" aria-describedby={zoom > 1 ? 'slide-pan-hint' : undefined}>
        <div className="stage-surface">
          <div
            className="stage"
            style={{ aspectRatio: `${slideWidth} / ${slideHeight}`, width: fittedWidth * zoom }}
            onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setChoices(null) } }}
            onClick={(event) => { if (event.target === event.currentTarget || (event.target as HTMLElement).tagName === 'IMG') setChoices(null) }}
          >
            {failed === src ? (
              <div className="stage-missing">Slide image unavailable</div>
            ) : (
              <img key={src} className="stage-img" src={src} alt={`Slide ${slide}`} onError={() => setFailed(src)} draggable={false} />
            )}
            {boxes.map((f) => {
              const bb = f.bbox!
              const n = numbers.get(f.id)
              const missing = isMissingElement(f)
              // On a busy slide only the finding in focus is drawn at full strength.
              const d = decisions[f.id]
              const fixing = d === 'accepted' && isAuto(f)
              const tone = fixing ? 'fix' : d === 'todo' || d === 'accepted' ? 'todo' : d === 'rejected' ? 'rejected' : null
              const quiet = boxes.length > QUIET_ABOVE && f.id !== selectedId && f.id !== hoveredId && !fixing
              // The new value appears on the slide the moment the reviewer accepts, so the decision is visible.
              const preview = fixing && f.expected && f.expected.length <= 28 && f.rule_id !== 'KEY_ORDER_INCONSISTENT' ? f.expected : null
              const cls = [
                'hl',
                `sev-${f.severity}`,
                tone && `is-${tone}`,
                missing && 'is-missing',
                f.id === selectedId && 'is-selected',
                f.id === hoveredId && 'is-hovered',
                d && 'is-decided',
                quiet && 'is-quiet',
              ]
                .filter(Boolean)
                .join(' ')
              return (
                <button
                  key={f.id}
                  className={cls}
                  style={{
                    left: `calc(${pct(bb.x, slideWidth)} - 3px)`,
                    top: `calc(${pct(bb.y, slideHeight)} - 3px)`,
                    width: `calc(${pct(bb.w, slideWidth)} + 6px)`,
                    height: `calc(${pct(bb.h, slideHeight)} + 6px)`,
                  }}
                  title={f.title}
                  aria-label={`Finding ${n}: ${f.title}`}
                  tabIndex={-1}
                  onClick={(event) => chooseAt(event, f)}
                >
                  <span className="hl-tag">
                    {fixing ? '✓' : n}
                    {missing && !d && ' · Expected here'}
                  </span>
                  {preview && <span className="hl-after">→ {preview}</span>}
                </button>
              )
            })}
            {choices && (
              <div ref={choicePanel} className="hl-choices" role="group" aria-label="Choose an overlapping finding"
                style={{ left: choices.x, top: choices.y }}
                onKeyDown={(event) => {
                  event.stopPropagation()
                  if (event.key === 'Escape') { setChoices(null); choiceTrigger.current?.focus() }
                }}
                onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setChoices(null) }}>
                <strong>Several findings here</strong>
                <span>Choose the issue you want to review.</span>
                {choices.items.map((finding) => (
                  <button key={finding.id} type="button" onClick={() => { setChoices(null); onSelect(finding) }}>
                    {numbers.get(finding.id)} · {finding.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
