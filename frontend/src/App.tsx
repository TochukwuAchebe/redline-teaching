import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { ApiError, setUnauthorizedHandler, type DeckState, type HistoryItem } from './client'
import { Login } from './screens/Login'
import { Upload, Progress, type Stage } from './screens/Upload'
import { Review } from './screens/Review'
import { ErrorState } from './screens/ErrorState'

type View =
  | { kind: 'boot' }
  | { kind: 'login' }
  | { kind: 'upload'; error?: string; notice?: string }
  | { kind: 'progress'; stage: Stage; filename?: string; pct?: number; deckId?: string; rendered?: number; total?: number | null }
  | { kind: 'review'; deck: DeckState }
  | { kind: 'error'; title: string; message: string; retry: 'poll' | 'upload' | null }

const POLL_MS = 1500

function readHashDeckId(): string {
  const m = window.location.hash.match(/^#\/deck\/([^/?#]+)/)
  return m ? decodeURIComponent(m[1]) : ''
}

function clearHash() {
  history.pushState(null, '', window.location.pathname + window.location.search)
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [deckId, setDeckId] = useState(readHashDeckId)
  const [view, setView] = useState<View>({ kind: 'boot' })
  const [pollNonce, setPollNonce] = useState(0)
  const lastFile = useRef<File | null>(null)
  const lastGuideline = useRef('acme')
  // Each upload gets a run number; anything that resolves for an older run is ignored,
  // so a response arriving after Cancel can never reopen a review.
  const run = useRef(0)
  const uploadAbort = useRef<AbortController | null>(null)

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false))
    const onHash = () => setDeckId(readHashDeckId())
    window.addEventListener('hashchange', onHash)
    window.addEventListener('popstate', onHash)
    let cancelled = false
    api.checkSession().then((ok) => {
      if (cancelled) return
      if (ok && !readHashDeckId() && api.defaultDeckId) {
        window.location.hash = `#/deck/${api.defaultDeckId}`
        setDeckId(api.defaultDeckId)
      }
      setAuthed(ok)
    })
    // A tab left on the sign-in page picks up a sign-in made in another tab when it comes back into view.
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      api.checkSession().then((ok) => {
        if (!cancelled && ok) setAuthed((was) => (was === false ? true : was))
      })
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      cancelled = true
      window.removeEventListener('hashchange', onHash)
      window.removeEventListener('popstate', onHash)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [])

  useEffect(() => {
    if (authed === false) {
      setView({ kind: 'login' })
      return
    }
    if (authed !== true) return
    if (!deckId) {
      setView((v) => (v.kind === 'progress' && v.stage === 'uploading' ? v : { kind: 'upload' }))
      return
    }
    let cancelled = false
    let timer: number | undefined
    const tick = async () => {
      try {
        const deck = await api.getDeck(deckId)
        if (cancelled) return
        if (deck.status === 'done') {
          setView(
            deck.result
              ? { kind: 'review', deck }
              : { kind: 'error', title: 'No results', message: 'Analysis finished but returned no results.', retry: 'poll' },
          )
        } else if (deck.status === 'cancelled') {
          setView({ kind: 'error', title: 'Review cancelled', message: 'This upload was cancelled before it was reviewed. Upload the deck again to review it.', retry: null })
        } else if (deck.status === 'error') {
          setView({
            kind: 'error',
            title: 'Analysis failed',
            message: deck.error || 'The deck could not be analysed.',
            retry: lastFile.current ? 'upload' : null,
          })
        } else {
          setView({
            kind: 'progress',
            stage: deck.status === 'queued' ? 'queued' : deck.status === 'analysing' ? 'analysing' : 'rendering',
            filename: deck.filename,
            deckId,
            rendered: deck.rendered,
            total: deck.slides_total,
          })
          timer = window.setTimeout(tick, POLL_MS)
        }
      } catch (e) {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 401) return // handler shows login
        if (e instanceof ApiError && e.status === 404) {
          setView({
            kind: 'error',
            title: 'Deck not found',
            message: 'We could not find a review at this link. Open History to find your saved work, or upload a new deck.',
            retry: null,
          })
        } else {
          setView({ kind: 'error', title: 'Could not load the deck', message: (e as Error).message, retry: 'poll' })
        }
      }
    }
    tick()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [authed, deckId, pollNonce])

  const uploading = view.kind === 'progress' && view.stage === 'uploading'
  useEffect(() => {
    if (!uploading) return
    // Before the server has the file there is nothing to resume; warn before leaving.
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [uploading])

  // --- shared history -----------------------------------------------------------
  const [history, setHistory] = useState<HistoryItem[] | null>(null)
  const [historyQuery, setHistoryQuery] = useState('')
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyTotal, setHistoryTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [recheckingId, setRecheckingId] = useState<string | null>(null)
  const [historyRefresh, setHistoryRefresh] = useState(0)
  const onHome = view.kind === 'upload'
  useEffect(() => {
    if (!onHome) return
    let stop = false
    let timer: number | undefined
    const load = async () => {
      try {
        const page = api.listDecksPage ? await api.listDecksPage(historyQuery, historyOffset) : { decks: await api.listDecks(), total: 0, next_offset: null }
        const items = page.decks
        if (stop) return
        setHistory(items)
        setHistoryTotal(page.total || items.length)
        setNextOffset(page.next_offset)
        setHistoryError(null)
        // Keep statuses fresh while anything is still processing.
        if (items.some((i) => ['queued', 'rendering', 'analysing'].includes(i.status))) timer = window.setTimeout(load, 4000)
      } catch (e) {
        if (!stop && !(e instanceof ApiError && e.status === 401)) setHistoryError('History could not be loaded. Refresh to try again.')
      }
    }
    load()
    return () => {
      stop = true
      window.clearTimeout(timer)
    }
  }, [onHome, historyQuery, historyOffset, historyRefresh])

  const openDeck = useCallback((id: string) => {
    run.current++
    window.location.hash = `#/deck/${encodeURIComponent(id)}`
    setDeckId(id)
    setPollNonce((n) => n + 1)
  }, [])

  const recheck = useCallback(
    async (id: string) => {
      const mine = ++run.current
      setRecheckingId(id)
      try {
        const { deck_id } = await api.recheckDeck(id)
        if (mine !== run.current) return
        openDeck(deck_id)
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 401)) setHistoryError(`Could not start a new check: ${(e as Error).message}`)
      } finally {
        setRecheckingId(null)
      }
    },
    [openDeck],
  )

  const upload = useCallback(async (file: File, guidelineId = 'acme') => {
    lastFile.current = file
    lastGuideline.current = guidelineId
    uploadAbort.current?.abort()
    const controller = new AbortController()
    uploadAbort.current = controller
    const mine = ++run.current
    setView({ kind: 'progress', stage: 'uploading', filename: file.name, pct: 0 })
    try {
      const { deck_id } = await api.uploadDeck(
        file,
        (pct) => {
          if (mine === run.current) setView((v) => (v.kind === 'progress' && v.stage === 'uploading' ? { ...v, pct } : v))
        },
        controller.signal,
        guidelineId,
      )
      if (mine !== run.current) {
        // Cancelled after the server accepted the file: stop the job it started.
        api.cancelDeck(deck_id).catch(() => undefined)
        return
      }
      window.location.hash = `#/deck/${encodeURIComponent(deck_id)}`
      setDeckId(deck_id)
      setPollNonce((n) => n + 1)
    } catch (e) {
      if (mine !== run.current || (e instanceof ApiError && e.aborted)) return
      if (e instanceof ApiError && e.status === 401) return
      setView({ kind: 'upload', error: (e as Error).message })
    }
  }, [])

  const newDeck = useCallback(() => {
    run.current++
    uploadAbort.current?.abort()
    lastFile.current = null
    clearHash()
    setDeckId('')
    setView({ kind: 'upload' })
  }, [])

  /** Cancel stops the work, not just the waiting: abort the upload and the server job. */
  const cancel = useCallback(async () => {
    const id = deckId
    newDeck()
    const mine = run.current
    if (!id) {
      setView({ kind: 'upload', notice: 'Cancellation requested. Check History if a review had already started.' })
      return
    }
    try {
      const stopped = await api.cancelDeck(id)
      if (mine !== run.current) return
      setView({ kind: 'upload', notice: stopped ? 'Review cancelled.' : 'Processing had already stopped. Saved reviews are available in History.' })
      setHistoryRefresh((n) => n + 1) // History loaded while the job was still running; show it stopped.
    } catch {
      if (mine !== run.current) return
      setView({ kind: 'upload', error: 'Could not confirm cancellation. Check the review in History and try again.' })
    }
  }, [deckId, newDeck])

  const logout = useCallback(async () => {
    run.current++
    uploadAbort.current?.abort()
    await api.logout().catch(() => undefined)
    setAuthed(false)
  }, [])

  const retry = useCallback(() => {
    if (view.kind !== 'error') return
    if (view.retry === 'upload' && lastFile.current) upload(lastFile.current, lastGuideline.current)
    else setPollNonce((n) => n + 1)
  }, [view, upload])

  switch (view.kind) {
    case 'boot':
      return <div className="boot" aria-busy="true" />
    case 'login':
      return <Login onSuccess={() => setAuthed(true)} />
    case 'upload':
      return (
        <Upload
          onFile={upload}
          error={view.error}
          notice={view.notice}
          history={{ items: history, error: historyError, busyId: recheckingId, onOpen: openDeck, onRecheck: recheck, query: historyQuery, total: historyTotal, onQuery: (q) => { setHistoryQuery(q); setHistoryOffset(0) }, onNext: nextOffset !== null ? () => setHistoryOffset(nextOffset) : undefined, onPrevious: historyOffset > 0 ? () => setHistoryOffset(Math.max(0, historyOffset - 25)) : undefined, slideUrl: (id) => api.slideUrl(id, 1) }}
          onLogout={logout}
        />
      )
    case 'progress':
      return (
        <Progress
          stage={view.stage}
          pct={view.pct}
          filename={view.filename}
          preview={view.deckId ? { rendered: view.rendered ?? 0, total: view.total ?? null, url: (n) => api.slideUrl(view.deckId!, n) } : undefined}
          onCancel={cancel}
        />
      )
    case 'review':
      return <Review key={view.deck.deck_id} deck={view.deck} onNewDeck={newDeck} onLogout={logout} />
    case 'error':
      return (
        <ErrorState
          title={view.title}
          message={view.message}
          onRetry={view.retry ? retry : undefined}
          onNewDeck={newDeck}
        />
      )
  }
}
