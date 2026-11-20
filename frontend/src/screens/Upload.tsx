import { useEffect, useRef, useState, type DragEvent } from 'react'
import { AppBar } from '../components/AppBar'
import { Guidelines } from '../components/Guidelines'
import { History } from '../components/History'
import { UploadIcon } from '../components/Icons'
import { Check } from '../components/Stepper'
import type { HistoryItem } from '../client'

const MAX_BYTES = 50 * 1024 * 1024

export type Stage = 'uploading' | 'queued' | 'rendering' | 'analysing'

const STEPS: { stage: Stage; label: string; detail: string }[] = [
  { stage: 'uploading', label: 'Uploading', detail: 'Sending the file securely' },
  { stage: 'queued', label: 'Waiting to start', detail: 'Another deck is being checked' },
  { stage: 'rendering', label: 'Rendering slide previews', detail: 'So every issue can be shown where it sits' },
  { stage: 'analysing', label: 'Checking against the guidelines', detail: 'Code checks the formatting; the Redline agent reviews the wording' },
]

function validate(file: File): string | null {
  if (!/\.pptx$/i.test(file.name)) return 'That file isn’t a PowerPoint. Redline reads .pptx files; save older .ppt files as .pptx first.'
  if (file.size > MAX_BYTES) return 'This file is larger than 50 MB.'
  if (file.size === 0) return 'This file is empty. Choose a PowerPoint with slides to review.'
  return null
}

export interface HistoryProps {
  query?: string
  total?: number
  onQuery?: (query: string) => void
  onNext?: () => void
  onPrevious?: () => void
  items: HistoryItem[] | null
  error?: string | null
  busyId?: string | null
  onOpen: (id: string) => void
  onRecheck: (id: string) => void
  slideUrl?: (id: string) => string
}

export function Upload({
  onFile,
  error,
  notice,
  history,
  onLogout,
}: {
  onFile: (f: File, guidelineId: string) => void
  error?: string
  notice?: string
  history?: HistoryProps
  onLogout: () => void
}) {
  const [guidelineId, setGuidelineId] = useState('acme')
  useEffect(() => {
    document.title = 'Check a deck · Redline'
  }, [])
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  function take(files: FileList | null) {
    if (files && files.length > 1) {
      setLocalError('Choose one PowerPoint at a time. Each review is saved under Recent reviews.')
      return
    }
    const file = files?.[0]
    if (!file) return
    const problem = validate(file)
    setLocalError(problem)
    if (!problem) onFile(file, guidelineId)
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    take(e.dataTransfer.files)
  }

  const message = localError ?? error

  return (
    <div className="page">
      <AppBar step={1} right={<button className="btn btn-ghost btn-sm" onClick={onLogout}>Sign out</button>} />
      <main className="home">
        <h1 className="h-page">Check a deck.</h1>
        <p className="home-lede">Every slide, checked against the brand guidelines. Most decks take under a minute.</p>
        <div
          className={`dropzone${dragging ? ' is-dragging' : ''}${message ? ' is-error' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => input.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              input.current?.click()
            }
          }}
          aria-describedby="upload-hint upload-error"
        >
          <span className="dropzone-icon" aria-hidden="true"><UploadIcon /></span>
          <span className="dropzone-title">Drop your PowerPoint here</span>
          <span className="dropzone-or">or</span>
          <span className="btn btn-primary btn-lg">Choose a file</span>
          <span id="upload-hint" className="fine">.pptx up to 50 MB · your original is never changed</span>
        </div>
        <input
          ref={input}
          type="file"
          accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
          hidden
          onChange={(e) => {
            take(e.target.files)
            e.target.value = ''
          }}
        />
        <p id="upload-error" className="form-error" role="alert">{message}</p>
        {notice && !message && <p className="fine" role="status">{notice}</p>}
        <Guidelines value={guidelineId} onChange={setGuidelineId} />
        {history && <History {...history} />}
      </main>
    </div>
  )
}

export interface DeckPreview {
  rendered: number
  total: number | null
  url: (n: number) => string
}

const MAX_THUMBS = 18

export function Progress({
  stage,
  pct,
  filename,
  preview,
  onCancel,
}: {
  stage: Stage
  pct?: number
  filename?: string
  preview?: DeckPreview
  onCancel: () => void
}) {
  const current = STEPS.findIndex((s) => s.stage === stage)
  useEffect(() => {
    document.title = 'Checking · Redline'
  }, [])
  const total = preview?.total ?? null
  const rendered = Math.min(preview?.rendered ?? 0, total ?? 0)
  return (
    <div className="page">
      <AppBar step={1} />
      <main className="home is-narrow" aria-live="polite">
        <h1 className="h-page h-page-md">Checking your deck…</h1>
        <p className="home-lede">Large decks can take a minute. You can leave this page; the review opens when it’s ready.</p>
        <section className="progress-card">
          <div className="progress-file">
            <div>
              <p className="progress-name">{filename ?? 'Deck'}</p>
              <p className="fine">{total ? `${rendered} of ${total} slide${total === 1 ? '' : 's'} ready` : 'Preparing…'}</p>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
          </div>
          <div className="bar" aria-hidden="true">
            <span
              className={stage === 'uploading' ? '' : 'is-indeterminate'}
              style={stage === 'uploading' ? { width: `${Math.round((pct ?? 0) * 100)}%` } : undefined}
            />
          </div>
          <ol className="progress-steps">
            {STEPS.map((s, i) => {
              const state = i < current ? 'done' : i === current ? 'run' : 'todo'
              return (
                <li key={s.stage} className={`is-${state}`}>
                  <span className="progress-icon" aria-hidden="true">{state === 'done' ? <Check /> : null}</span>
                  <span>
                    <span className="progress-label">
                      {s.label}
                      {state === 'run' && stage === 'uploading' && pct !== undefined ? ` · ${Math.round(pct * 100)}%` : ''}
                    </span>
                    {state !== 'done' && <span className="fine">{s.detail}</span>}
                  </span>
                  <span className="sr-only">{state === 'done' ? 'done' : state === 'run' ? 'in progress' : 'waiting'}</span>
                </li>
              )
            })}
          </ol>
        </section>
        {preview && total ? (
          <div className="progress-thumbs" aria-label={`${rendered} of ${total} slide previews ready`}>
            {Array.from({ length: Math.min(total, MAX_THUMBS) }, (_, i) => i + 1).map((n) =>
              n <= rendered ? <img key={n} src={preview.url(n)} alt={`Slide ${n}`} loading="lazy" /> : <span key={n} className="skeleton" aria-hidden="true" />,
            )}
          </div>
        ) : null}
      </main>
    </div>
  )
}
