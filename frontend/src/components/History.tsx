import { useState } from 'react'
import type { HistoryItem } from '../client'

interface Props {
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

const STATUS: Record<HistoryItem['status'], string> = {
  queued: 'Waiting',
  rendering: 'Processing',
  analysing: 'Processing',
  done: 'Ready',
  error: 'Failed',
  cancelled: 'Cancelled',
}


function when(iso: string): string {
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return 'Date unavailable'
  const mins = Math.round((Date.now() - d.getTime()) / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins} min ago`
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Decks already checked on this server, so a reviewer can reopen one instead of uploading again. */
export function History({ items, error, busyId, onOpen, onRecheck, query: remoteQuery, total, onQuery, onNext, onPrevious, slideUrl }: Props) {
  const [query, setQuery] = useState('')
  // Keep the search box with the error: a failed search must be fixable without a refresh.
  if (error && !(onQuery && remoteQuery)) return <p className="form-error history-error">{error}</p>
  if (items === null && !error) return <p className="fine history-empty">Loading recent reviews…</p>
  if (error) {
    return (
      <section className="history" aria-labelledby="history-title">
        <div className="history-head">
          <h2 id="history-title" className="h-small">Recent reviews</h2>
          <input className="history-search" maxLength={100} type="search" placeholder="Search by file name"
            aria-label="Search history by file name" value={remoteQuery} onChange={(e) => onQuery?.(e.target.value)} />
        </div>
        <p className="form-error history-error">{error} <button className="btn-text" onClick={() => onQuery?.('')}>Clear search</button></p>
      </section>
    )
  }
  if (items === null) return null

  const q = (remoteQuery ?? query).trim().toLowerCase()
  const shown = onQuery ? items : q ? items.filter((i) => i.filename.toLowerCase().includes(q)) : items

  return (
    <section className="history" aria-labelledby="history-title">
      <div className="history-head">
        <h2 id="history-title" className="h-small">
          Recent reviews
        </h2>
        <span className="fine">{total ?? items.length} review{(total ?? items.length) === 1 ? '' : 's'}</span>
        {((total ?? items.length) > 5 || onQuery) && (
          <input
            className="history-search"
            maxLength={100}
            type="search"
            placeholder="Search by file name"
            aria-label="Search history by file name"
            value={remoteQuery ?? query}
            onChange={(e) => onQuery ? onQuery(e.target.value) : setQuery(e.target.value)}
          />
        )}
      </div>
      {(onNext || onPrevious) && <div className="row-gap"><button className="btn btn-ghost btn-sm" disabled={!onPrevious} onClick={onPrevious}>Newer reviews</button><button className="btn btn-ghost btn-sm" disabled={!onNext} onClick={onNext}>Older reviews</button></div>}

      {q && shown.length === 0 ? (
        <p className="fine history-empty">No reviews match “{remoteQuery ?? query}”. <button className="btn-text" onClick={() => onQuery ? onQuery('') : setQuery('')}>Clear search</button></p>
      ) : items.length === 0 ? (
        <p className="fine history-empty">No decks yet. Upload one above; it appears here for anyone on the team to reopen.</p>
      ) : shown.length === 0 ? (
        <p className="fine history-empty">No decks match “{remoteQuery ?? query}”.</p>
      ) : (
        <ul className="history-list">
          {shown.map((item) => {
            const ready = item.status === 'done'
            const working = item.status === 'queued' || item.status === 'rendering' || item.status === 'analysing'
            return (
              <li key={item.deck_id} className="history-row">
                {slideUrl && ready ? <img className="history-thumb" src={slideUrl(item.deck_id)} alt="" loading="lazy" /> : <span className="history-thumb" aria-hidden="true" />}
                <div className="history-main">
                  <span className="history-name" title={item.filename}>
                    {item.filename}
                  </span>
                  <span className="fine">
                    {when(item.created_at)}
                    {item.slide_count ? ` · ${item.slide_count} slides` : ''}
                  </span>
                </div>
                <div className="history-facts">
                  <span className={`history-status is-${ready ? 'ready' : working ? 'working' : item.status === 'error' ? 'failed' : 'stopped'}`}>{STATUS[item.status]}</span>
                  {ready && (
                    <>
                      <span className="history-progress">
                        {item.decisions_made} of {item.decisions_total} decided
                      </span>
                    </>
                  )}
                </div>
                <div className="history-actions">
                  {(ready || working) && (
                    <button className={`btn btn-sm ${ready ? 'btn-primary' : 'btn-secondary'}`} onClick={() => onOpen(item.deck_id)}>
                      {ready ? (item.decisions_made ? 'Resume' : 'Open') : 'View progress'}
                    </button>
                  )}
                  <button
                    className="btn btn-sm btn-secondary"
                    onClick={() => onRecheck(item.deck_id)}
                    disabled={busyId === item.deck_id}
                    title="Check this deck again with the current rules, as a new review"
                  >
                    {busyId === item.deck_id ? 'Starting…' : 'Check again'}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
