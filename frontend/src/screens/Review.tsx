import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { DecisionQueue } from '../decisionQueue'
import { ApiError, type DeckState, type Decision, type DecisionPatch, type FixState, type Upgrades } from '../client'
import { AppBar } from '../components/AppBar'
import { ApplyDialog } from '../components/ApplyDialog'
import { ChangesView } from '../components/ChangesView'
import { DoneView } from '../components/DoneView'
import { FindingCard, outcome, type GroupInfo } from '../components/FindingCard'
import { ChevronDown, ChevronLeft, ChevronRight, Spark } from '../components/Icons'
import { ScoreChip } from '../components/ScoreChip'
import { SlideStage } from '../components/SlideStage'
import { UpgradePanel } from '../components/UpgradePanel'
import { checkedScore, dismissedCount, isAuto, projectedScore } from '../brandScore'
import { SEVERITY_NAME, sortBySeverity, worstSeverity } from '../severity'
import type { Finding } from '../types'

interface HistoryEntry {
  prev: DecisionPatch
  focus: string
  slide: number
}

type Mode = 'review' | 'changes' | 'done'
type Tab = 'issues' | 'redesign'

const SHORTCUTS: [string, string][] = [
  ['J / K', 'Previous / next slide'],
  ['↑ / ↓', 'Previous / next issue'],
  ['A', 'Accept'],
  ['R', 'Reject'],
  ['T', 'Accept as a to-do (you change it)'],
  ['Shift + A / R', 'Accept or reject every place it appears'],
  ['U', 'Undo'],
  ['?', 'Show or hide this help'],
]

const STACK_MIN = 2

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
}

export function Review({ deck, onNewDeck, onLogout }: { deck: DeckState; onNewDeck: () => void; onLogout: () => void }) {
  const result = deck.result!
  const { findings, slide_width: W, slide_height: H } = result
  const slideCount = Math.max(result.slide_count, ...findings.map((f) => f.slide), 1)

  // The slide is part of the address, so a reload or a shared link opens where the reviewer was.
  const [slide, setSlide] = useState(() => {
    const n = Number(window.location.hash.match(/\/slide\/(\d+)/)?.[1])
    return n >= 1 && n <= slideCount ? n : 1
  })
  useEffect(() => {
    const base = `#/deck/${encodeURIComponent(deck.deck_id)}`
    window.history.replaceState(null, '', slide > 1 ? `${base}/slide/${slide}` : base)
  }, [slide, deck.deck_id])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [decisions, setDecisions] = useState<Record<string, Decision>>(deck.decisions ?? {})
  const [fix, setFix] = useState<FixState | null>(deck.fix ?? null)
  const [mode, setMode] = useState<Mode>('review')
  const [tab, setTab] = useState<Tab>('issues')
  const [menuOpen, setMenuOpen] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [openStacks, setOpenStacks] = useState<Set<string>>(() => new Set())
  const [showSuggestions, setShowSuggestions] = useState<Set<number>>(() => new Set())
  const [toast, setToast] = useState<{ text: string; undo?: boolean } | null>(null)
  const [upgrades, setUpgrades] = useState<Upgrades | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const cardRefs = useRef(new Map<string, HTMLElement>())
  const thumbRefs = useRef(new Map<number, HTMLElement>())
  const toastTimer = useRef<number | undefined>(undefined)

  const showToast = useCallback((text: string, undo = false) => {
    setToast({ text, undo })
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), undo ? 5000 : 3200)
  }, [])

  // --- decisions are saved in order, with rollback on failure ---------------------
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const decisionQueue = useRef<DecisionQueue | null>(null)
  if (!decisionQueue.current) {
    decisionQueue.current = new DecisionQueue(deck.decisions ?? {},
      (patch) => api.putDecisions(deck.deck_id, patch),
      (next, busy) => { setDecisions(next); setSaving(busy) },
      // Network and server errors are retried for about half a minute; a rejected request is final.
      { delays: [1000, 3000, 8000, 15000], retryable: (e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) })
  }
  useEffect(() => {
    if (!saving) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [saving])

  // --- design suggestions (optional, separate from guideline issues) ---------------
  useEffect(() => {
    let stop = false
    api.listUpgrades(deck.deck_id).then((u) => { if (!stop) setUpgrades(u) }).catch(() => undefined)
    return () => { stop = true }
  }, [deck.deck_id])
  const canRedesign = !!upgrades?.candidates.some((c) => c.slide === slide)
  const chosenUpgrades = Object.entries(upgrades?.items ?? {}).filter(([, v]) => v.choice === 'use').map(([k]) => Number(k))
  useEffect(() => { if (!canRedesign) setTab('issues') }, [canRedesign])

  // --- derived ---------------------------------------------------------------------
  const bySlide = useMemo(() => {
    const m = new Map<number, Finding[]>()
    for (const f of findings) m.set(f.slide, [...(m.get(f.slide) ?? []), f])
    for (const [k, v] of m) m.set(k, sortBySeverity(v))
    return m
  }, [findings])

  const groups = useMemo(() => {
    const m = new Map<string, Finding[]>()
    for (const f of findings) if (f.group_id) m.set(f.group_id, [...(m.get(f.group_id) ?? []), f])
    return m
  }, [findings])

  const slideFindings = bySlide.get(slide) ?? []
  const primary = slideFindings.filter((f) => f.severity !== 'suggestion')
  const suggestions = slideFindings.filter((f) => f.severity === 'suggestion')
  const suggestionsOpen = showSuggestions.has(slide) || suggestions.some((f) => f.id === selectedId)
  const stacks = new Map<string, Finding[]>()
  for (const f of primary) stacks.set(f.rule_id, [...(stacks.get(f.rule_id) ?? []), f])
  const stackKey = (rule: string) => `${slide}:${rule}`
  const isHidden = (f: Finding) => {
    const members = stacks.get(f.rule_id)!
    return members.length >= STACK_MIN && members[0].id !== f.id && !openStacks.has(stackKey(f.rule_id))
      && !members.some((m) => m.id === selectedId && m.id !== members[0].id)
  }
  const primaryVisible = primary.filter((f) => !isHidden(f))
  // One unit throughout the panel: a card. Repeats of a rule on this slide share a card; optional
  // suggestions are counted on their own and never block "All decided".
  const slideIssues = stacks.size
  const navList = suggestionsOpen ? [...primaryVisible, ...suggestions] : primaryVisible
  const selected = slideFindings.find((f) => f.id === selectedId) ?? null
  const units = new Map<string, Finding[]>()
  for (const f of findings) units.set(f.group_id ?? f.id, [...(units.get(f.group_id ?? f.id) ?? []), f])
  const totalDecisions = units.size
  const reviewed = [...units.values()].filter((members) => members.every((m) => decisions[m.id])).length
  const slideOpen = [...stacks.values()].filter((members) => members.some((m) => !decisions[m.id])).length
  const numbers = new Map([...primaryVisible, ...suggestions].map((f, i) => [f.id, i + 1]))

  const startScore = useMemo(() => checkedScore(findings, slideCount), [findings, slideCount])
  const score = checkedScore(findings, slideCount, fix)
  const projected = projectedScore(findings, slideCount, decisions, fix)
  const dismissed = dismissedCount(findings, decisions)

  // --- selection -------------------------------------------------------------------
  // Every issue on the slide in panel order, including repeats folded under "more like this"
  // and collapsed suggestions, so "next open issue" can never skip one.
  const ordered = useMemo(() => [...primary, ...suggestions], [primary, suggestions])
  const reveal = useCallback((f: Finding) => {
    if (f.severity === 'suggestion') setShowSuggestions((s) => new Set(s).add(f.slide))
    else if ((stacks.get(f.rule_id)?.length ?? 0) >= STACK_MIN) setOpenStacks((st) => new Set(st).add(`${f.slide}:${f.rule_id}`))
  }, [stacks])

  // Arriving on a slide selects its first open issue; a finished slide selects nothing.
  useEffect(() => {
    if (selectedId && slideFindings.some((f) => f.id === selectedId)) return
    const first = ordered.find((f) => !decisions[f.id])
    if (first) reveal(first)
    setSelectedId(first?.id ?? null)
  }, [slideFindings, selectedId, ordered, decisions, reveal])

  useEffect(() => {
    if (!selectedId) return
    const card = cardRefs.current.get(selectedId)
    card?.scrollIntoView({ block: 'nearest' })
    const focused = document.activeElement?.closest<HTMLElement>('article[data-finding-id]')
    if (focused && focused.dataset.findingId !== selectedId) card?.querySelector<HTMLButtonElement>('.card-select')?.focus({ preventScroll: true })
  }, [selectedId])

  useEffect(() => {
    thumbRefs.current.get(slide)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [slide])

  const goSlide = useCallback((n: number) => {
    if (n < 1 || n > slideCount) return
    setSlide(n)
    setSelectedId(null)
    setHoveredId(null)
  }, [slideCount])

  const select = useCallback((f: Finding) => {
    if (f.severity === 'suggestion') setShowSuggestions((s) => new Set(s).add(f.slide))
    setTab('issues')
    setSelectedId(f.id)
  }, [])

  const moveSelection = useCallback((delta: 1 | -1) => {
    const idx = navList.findIndex((f) => f.id === selectedId)
    if (delta > 0 && idx === navList.length - 1 && !suggestionsOpen && suggestions.length) {
      setShowSuggestions((s) => new Set(s).add(slide))
      setSelectedId(suggestions[0].id)
      return
    }
    const next = navList[Math.min(Math.max(idx + delta, 0), navList.length - 1)]
    if (next) setSelectedId(next.id)
  }, [navList, selectedId, suggestionsOpen, suggestions, slide])

  /** After a decision: the next open issue on this slide, or, when the slide is done, the next slide with open issues. */
  const advanceTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(advanceTimer.current), [])
  const advance = useCallback((from: Finding, decided: Record<string, Decision>) => {
    const next = ordered.find((f) => f.id !== from.id && !decided[f.id])
    if (next) {
      reveal(next)
      setSelectedId(next.id)
      return false
    }
    setSelectedId(null)
    const later = Array.from({ length: slideCount - from.slide }, (_, i) => from.slide + 1 + i)
      .find((n) => (bySlide.get(n) ?? []).some((f) => !decided[f.id]))
    window.clearTimeout(advanceTimer.current)
    if (later) advanceTimer.current = window.setTimeout(() => goSlide(later), 900)
    return later ?? true
  }, [ordered, reveal, slideCount, bySlide, goSlide])

  // --- decisions -------------------------------------------------------------------
  const applyPatch = useCallback((patch: DecisionPatch, record: { focus: string; slide: number } | null) => {
    return decisionQueue.current!.enqueue(patch).then((saved) => {
      if (saved.saved) {
        setSaveError(null)
        if (record) setHistory((h) => [...h.slice(-199), { prev: saved.previous, ...record }])
        return true
      }
      const e = saved.error
      if (!(e instanceof ApiError && e.status === 401)) {
        const message = `Decision not saved: ${(e as Error).message.replace(/[.\s]+$/, '')}. Please try again.`
        setSaveError(message)
        showToast(message)
      }
      return false
    })
  }, [showToast])

  const decide = useCallback((f: Finding, value: Decision | null, opts: { group: boolean }) => {
    const members = opts.group && f.group_id ? groups.get(f.group_id) ?? [f] : [f]
    const patch: DecisionPatch = {}
    for (const m of members) patch[m.id] = value === 'accepted' && !isAuto(m) ? 'todo' : value
    applyPatch(patch, { focus: f.id, slide: f.slide })
    const after = { ...decisions }
    for (const [id, v] of Object.entries(patch)) { if (v) after[id] = v; else delete after[id] }
    if (value) {
      const label = outcome(value, isAuto(f))!.label
      const said = members.length > 1 ? `${label}: ${members.length} places` : label === 'Will be fixed' ? 'Will be fixed when you apply' : label
      const moved = advance(f, after)
      const tail = typeof moved === 'number' ? ` · Slide ${f.slide} done, opening slide ${moved}` : moved === true ? ' · Every issue in the deck is decided' : ''
      showToast(said + tail, true)
    }
  }, [groups, decisions, applyPatch, showToast, advance])

  const undo = useCallback(() => {
    window.clearTimeout(advanceTimer.current)
    if (decisionQueue.current!.busy) { showToast('Wait for decisions to save before undoing.'); return }
    const last = history[history.length - 1]
    if (!last) return
    void applyPatch(last.prev, null).then((saved) => { if (saved) setHistory((h) => h.slice(0, -1)) })
    setSlide(last.slide)
    setSelectedId(last.focus)
    setToast(null)
  }, [history, applyPatch, showToast])

  // --- applying fixes ---------------------------------------------------------------
  const acceptedFixable = findings.filter((f) => decisions[f.id] === 'accepted' && isAuto(f))
  const changeCount = acceptedFixable.length + chosenUpgrades.length
  const fixDone = fix?.state === 'done' && !!fix.report
  const acceptedIds = findings.filter((f) => decisions[f.id] === 'accepted').map((f) => f.id)
  const appliedUpgrades = Object.keys(fix?.upgrades ?? {}).map(Number).sort().join(',')
  const fixStale = fixDone && (acceptedIds.length !== Object.keys(fix!.report!).length || acceptedIds.some((id) => !fix!.report![id])
    || appliedUpgrades !== [...chosenUpgrades].sort().join(','))
  const [applying, setApplying] = useState(false)

  const pollFix = useCallback(async (signal: AbortSignal) => {
    let polls = 0
    let failures = 0
    while (!signal.aborted) {
      await new Promise((resolve) => setTimeout(resolve, polls++ < 60 ? 1500 : 5000))
      if (signal.aborted) return null
      try {
        const latest = await api.getDeck(deck.deck_id)
        if (!latest.fix) throw new Error('missing correction status')
        failures = 0
        if (latest.fix.state !== 'running') return latest.fix
      } catch (error) {
        if ((error instanceof ApiError && error.status === 401) || ++failures >= 5) {
          throw error instanceof ApiError && error.status === 401 ? error : new Error('Lost touch with the fixes. Reopen the review to check on them.')
        }
      }
    }
    return null
  }, [deck.deck_id])

  useEffect(() => {
    if (fix?.state !== 'running') return
    const controller = new AbortController()
    pollFix(controller.signal).then((done) => {
      if (!done || controller.signal.aborted) return
      setFix(done)
      if (done.state === 'done') {
        setMode('changes')
        setSlide(firstChangedSlide(done) ?? 1)
      } else showToast(done.error ?? 'The fixes could not be applied.')
    }).catch((error) => {
      if (controller.signal.aborted) return
      setFix((current) => (current?.state === 'running' ? null : current))
      if (!(error instanceof ApiError && error.status === 401)) showToast((error as Error).message)
    })
    return () => controller.abort()
  }, [fix?.state, pollFix, showToast]) // eslint-disable-line react-hooks/exhaustive-deps

  function firstChangedSlide(state: FixState): number | null {
    const slides = Object.entries(state.report ?? {}).filter(([, r]) => r.status === 'applied')
      .map(([id]) => findings.find((f) => f.id === id)?.slide ?? 0).filter(Boolean)
    const upgraded = Object.entries(state.upgrades ?? {}).filter(([, u]) => u.status === 'applied').map(([k]) => Number(k))
    const all = [...slides, ...upgraded].sort((a, b) => a - b)
    return all[0] ?? null
  }

  async function applyFixes() {
    if (decisionQueue.current!.busy) { showToast('Wait for decisions to save before applying.'); return }
    if (applying || fix?.state === 'running') return
    setApplying(true)
    try {
      await api.applyFixes(deck.deck_id)
      setConfirming(false)
      setFix((current) => ({ ...(current ?? {}), state: 'running' }))
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) showToast((error as Error).message)
    } finally {
      setApplying(false)
    }
  }

  // --- export ---------------------------------------------------------------------------
  async function exportCsv() {
    if (decisionQueue.current!.busy) { showToast('Wait for decisions to save before downloading the report.'); return }
    if (!findings.some((f) => decisions[f.id] === 'accepted' || decisions[f.id] === 'todo')) {
      showToast('Nothing to download yet. The report lists the issues you accept.')
      return
    }
    try {
      await api.exportCsv(deck.deck_id, result.filename)
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) showToast(`Download failed: ${(e as Error).message}`)
    }
  }

  // --- keyboard ------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || mode !== 'review') return
      if ((menuOpen || showHelp || confirming) && e.key !== 'Escape') return
      switch (e.key) {
        case 'j': case 'J': case 'ArrowLeft': goSlide(slide - 1); break
        case 'k': case 'K': case 'ArrowRight': goSlide(slide + 1); break
        case 'ArrowDown': moveSelection(1); break
        case 'ArrowUp': moveSelection(-1); break
        case 'a': case 'A': if (selected) decide(selected, isAuto(selected) ? 'accepted' : 'todo', { group: e.shiftKey }); break
        case 'r': case 'R': if (selected) decide(selected, 'rejected', { group: e.shiftKey }); break
        case 't': case 'T': if (selected) decide(selected, 'todo', { group: e.shiftKey }); break
        case 'u': case 'U': undo(); break
        case '?': setShowHelp((s) => !s); break
        case 'Escape':
          if (menuOpen) { setMenuOpen(false); menuRef.current?.querySelector<HTMLButtonElement>('.file-btn')?.focus() }
          if (showHelp) { setShowHelp(false); document.querySelector<HTMLButtonElement>('.help-wrap > button')?.focus() }
          break
        default: return
      }
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [goSlide, slide, moveSelection, selected, decide, undo, menuOpen, showHelp, confirming, mode])

  useEffect(() => {
    if (!menuOpen) return
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus()
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  useEffect(() => {
    document.title = `${result.filename} · Redline`
  }, [result.filename])

  // --- render ----------------------------------------------------------------------------
  const groupInfo = (f: Finding): GroupInfo | null => {
    if (!f.group_id) return null
    const members = groups.get(f.group_id) ?? []
    return { slides: [...new Set(members.map((m) => m.slide))].sort((a, b) => a - b), total: members.length }
  }

  const renderCard = (f: Finding) => (
    <FindingCard
      key={f.id}
      ref={(el) => { if (el) cardRefs.current.set(f.id, el); else cardRefs.current.delete(f.id) }}
      finding={f}
      number={numbers.get(f.id) ?? 0}
      decision={decisions[f.id]}
      selected={f.id === selectedId}
      group={groupInfo(f)}
      onSelect={() => select(f)}
      onHover={(h) => setHoveredId(h ? f.id : null)}
      onDecide={(value, all) => { setSelectedId(f.id); decide(f, value, { group: all }) }}
      onGoToSlide={goSlide}
      fixable={isAuto(f)}
      fixRow={fix?.state === 'done' && !fixStale ? fix.report?.[f.id] : undefined}
    />
  )

  const step = mode === 'review' ? 2 : mode === 'changes' ? 3 : 4
  const primaryAction = fix?.state === 'running' || applying ? (
    <button className="btn btn-primary" disabled>Applying…</button>
  ) : fixDone && !fixStale ? (
    mode === 'review'
      ? <button className="btn btn-primary" onClick={() => { setMode('changes'); setSlide(firstChangedSlide(fix!) ?? slide) }}>See what changed</button>
      : mode === 'changes' ? <button className="btn btn-primary" onClick={() => setMode('done')}>Finish</button> : null
  ) : (
    <button className="btn btn-primary" onClick={() => setConfirming(true)} disabled={saving || !changeCount}
      title={changeCount ? 'Review the changes before they are made' : 'Accept an issue Redline can fix to apply it'}>
      {changeCount ? `Apply ${changeCount} change${changeCount === 1 ? '' : 's'}` : 'Apply changes'}
    </button>
  )

  const fileMenu = (
    <div className="file-menu" ref={menuRef}>
      <button className="file-btn" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)} title={result.filename}>
        <span className="file-name">{result.filename}</span><ChevronDown />
      </button>
      {menuOpen && (
        <div className="menu" role="menu" onKeyDown={(event) => {
          if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
          const items = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')]
          const index = items.indexOf(document.activeElement as HTMLElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
          event.preventDefault()
          items[next]?.focus()
        }}>
          <button role="menuitem" onClick={onNewDeck} disabled={saving}>Recent reviews and new deck</button>
          {fixDone && <a role="menuitem" href={api.correctedDeckUrl(deck.deck_id)} download>Download corrected deck</a>}
          <button role="menuitem" onClick={() => { setMenuOpen(false); exportCsv() }}>Download review report (CSV)</button>
          <span className="menu-sep" role="separator" />
          <button role="menuitem" onClick={onLogout} disabled={saving}>Sign out</button>
        </div>
      )}
    </div>
  )

  const help = (
    <div className="help-wrap">
      <button className={`btn btn-secondary btn-icon${showHelp ? ' is-on' : ''}`} onClick={() => setShowHelp((s) => !s)} aria-expanded={showHelp} aria-label="Help and shortcuts" title="Help (?)">?</button>
      {showHelp && (
        <div className="popover help-pop" role="dialog" aria-label="Help">
          <p className="h-small">How Redline works</p>
          <p className="fine-strong"><b>Code checks</b> every slide against the guidelines. <b>The Redline agent suggests</b> wording and clearer designs, quoting the slide. <b>You decide</b>: nothing changes until you accept it.</p>
          <p className="fine-strong"><b>Accept</b> an issue Redline can fix and it’s changed in a copy when you apply; otherwise it goes on your to-do list. <b>Reject</b> keeps the slide as it is. Your original file is never changed.</p>
          <dl className="keys">{SHORTCUTS.map(([k, v]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>)}</dl>
        </div>
      )}
    </div>
  )

  const bar = (
    <AppBar
      step={step}
      heading={mode === 'changes' ? `What changed in ${result.filename}` : mode === 'done' ? undefined : `Reviewing ${result.filename}`}
      onHome={saving ? undefined : onNewDeck}
      left={<><span className="divider" aria-hidden="true" />{fileMenu}</>}
      right={<>
        <ScoreChip checked={score} projected={projected} dismissed={dismissed} />
        {saving && <span className="fine" role="status">Saving…</span>}
        {primaryAction}
        {help}
      </>}
    />
  )

  if (mode === 'changes' && fix?.state === 'done') {
    return (
      <div className="review">
        {bar}
        <ChangesView deck={deck} fix={fix} findings={findings} slide={slide} onSlide={setSlide}
          ratio={W / H} startScore={startScore} score={score} onBack={() => setMode('review')} onFinish={() => setMode('done')} />
        <Toast toast={toast} onUndo={undo} />
      </div>
    )
  }
  if (mode === 'done') {
    return (
      <div className="review is-page">
        {bar}
        <DoneView deck={deck} fix={fix} findings={findings} decisions={decisions} startScore={startScore} score={score}
          onExport={exportCsv} onNewDeck={onNewDeck} onBack={() => setMode(fixDone ? 'changes' : 'review')} onGoTo={(n) => { setMode('review'); goSlide(n) }} />
      </div>
    )
  }

  const redesignItem = upgrades?.items[String(slide)]
  const showRedesign = tab === 'redesign' && redesignItem?.state === 'ready'
  const stageFindings = showRedesign ? [] : [...primaryVisible, ...(suggestionsOpen ? suggestions : [])]

  return (
    <div className="review">
      <a className="skip-link" href="#issues-panel">Skip to issues</a>
      {bar}
      <div className="review-body">
        <nav className="rail" aria-label="Slides">
          {Array.from({ length: slideCount }, (_, i) => i + 1).map((n) => {
            const fs = bySlide.get(n) ?? []
            const open = fs.filter((f) => !decisions[f.id])
            const done = fs.length > 0 && open.length === 0
            const worst = worstSeverity(open.length ? open : fs)
            return (
              <button key={n} ref={(el) => { if (el) thumbRefs.current.set(n, el); else thumbRefs.current.delete(n) }}
                className={`thumb${n === slide ? ' is-current' : ''}`} onClick={() => goSlide(n)} aria-current={n === slide ? 'true' : undefined}
                aria-label={`Slide ${n}: ${fs.length ? `${open.length} of ${fs.length} open` : 'no issues'}`}>
                <span className="thumb-img" style={{ aspectRatio: `${W} / ${H}` }}><img src={api.slideUrl(deck.deck_id, n)} alt="" loading="lazy" draggable={false} /></span>
                <span className="thumb-meta">
                  <span className="thumb-num">{n}</span>
                  {done ? <span className="thumb-done" aria-hidden="true">✓</span>
                    : worst ? <span className="thumb-count" aria-hidden="true"><span className={`dot dot-${SEVERITY_NAME[worst]}`} />{open.length}</span> : null}
                </span>
              </button>
            )
          })}
        </nav>

        <main className="stage-area">
          <div className="stage-head">
            <button className="btn btn-secondary btn-icon btn-sm" onClick={() => goSlide(slide - 1)} disabled={slide <= 1} aria-label="Previous slide" title="Previous slide (J)"><ChevronLeft /></button>
            <span className="stage-title">Slide {slide} <span className="faint">of {slideCount}</span></span>
            <button className="btn btn-secondary btn-icon btn-sm" onClick={() => goSlide(slide + 1)} disabled={slide >= slideCount} aria-label="Next slide" title="Next slide (K)"><ChevronRight /></button>
            <span className="grow" />
            {redesignItem?.state === 'ready' && (
              <div className="seg" role="group" aria-label="Show">
                <button className={tab === 'issues' ? 'is-on' : ''} onClick={() => setTab('issues')}>Original</button>
                <button className={tab === 'redesign' ? 'is-on' : ''} onClick={() => setTab('redesign')}>Suggested redesign</button>
              </div>
            )}
          </div>
          <SlideStage
            src={showRedesign ? api.upgradePreviewUrl(deck.deck_id, slide) : api.slideUrl(deck.deck_id, slide)}
            slide={slide}
            slideWidth={W}
            slideHeight={H}
            findings={stageFindings}
            numbers={numbers}
            decisions={decisions}
            selectedId={selectedId}
            hoveredId={hoveredId}
            onSelect={select}
          />
        </main>

        <aside className="panel" aria-label="Issues" id="issues-panel" tabIndex={-1}>
          <div className="panel-head">
            <div className="panel-title-row">
              <h2 className="h-small">
                {slideIssues ? `${slideIssues} issue${slideIssues === 1 ? '' : 's'} on this slide` : 'No issues on this slide'}
              </h2>
              {slideIssues > 0 && <span className="fine">{slideOpen ? `${slideOpen} to decide` : 'All decided'}</span>}
            </div>
            {canRedesign && (
              <div className="seg seg-full" role="tablist" aria-label="Panel">
                <button role="tab" aria-selected={tab === 'issues'} className={tab === 'issues' ? 'is-on' : ''} onClick={() => setTab('issues')}>Issues · {slideIssues}</button>
                <button role="tab" aria-selected={tab === 'redesign'} className={tab === 'redesign' ? 'is-on' : ''} onClick={() => setTab('redesign')}><Spark /> Redesign</button>
              </div>
            )}
          </div>
          {saveError && <p role="alert" className="form-error panel-note">{saveError}</p>}

          {tab === 'redesign' && canRedesign ? (
            <UpgradePanel deckId={deck.deck_id} slide={slide} item={redesignItem}
              kind={upgrades?.candidates.find((c) => c.slide === slide)?.kind}
              onChange={(item) => setUpgrades((u) => u && { ...u, items: { ...u.items, [String(slide)]: item } })}
              onShow={() => setTab('redesign')} onToast={showToast} />
          ) : slideFindings.length === 0 ? (
            <div className="empty">
              <span className="empty-check" aria-hidden="true">✓</span>
              <p className="h-small">Nothing to fix here</p>
              <p className="fine">{slide < slideCount ? 'Press K for the next slide.' : 'That was the last slide.'}</p>
              {slide < slideCount && <button className="btn btn-secondary btn-sm" onClick={() => goSlide(slide + 1)}>Next slide <ChevronRight /></button>}
            </div>
          ) : (
            <div className="card-list">
              {primaryVisible.map((f) => {
                const members = stacks.get(f.rule_id)!
                const isHead = members.length >= STACK_MIN && members[0].id === f.id
                if (!isHead) return renderCard(f)
                const open = openStacks.has(stackKey(f.rule_id)) || members.some((m) => m.id === selectedId && m !== f)
                const toggle = () => setOpenStacks((st) => {
                  const n = new Set(st)
                  if (n.has(stackKey(f.rule_id))) n.delete(stackKey(f.rule_id)); else n.add(stackKey(f.rule_id))
                  return n
                })
                const decideStack = (value: 'accepted' | 'rejected') => {
                  const patch: DecisionPatch = {}
                  for (const m of members) patch[m.id] = value === 'accepted' && !isAuto(m) ? 'todo' : value
                  applyPatch(patch, { focus: f.id, slide })
                  const after = { ...decisions, ...(patch as Record<string, Decision>) }
                  const moved = advance(f, after)
                  showToast(`${value === 'accepted' ? 'Accepted' : 'Rejected'} ${members.length} on this slide${typeof moved === 'number' ? ` · opening slide ${moved}` : ''}`, true)
                }
                return (
                  <Fragment key={`stack-${f.id}`}>
                    {renderCard(f)}
                    <div className="stack-row">
                      <button className="link-btn" aria-expanded={open} onClick={toggle}>{open ? 'Show fewer' : `${members.length - 1} more like this`}</button>
                      <button className="link-btn" onClick={() => decideStack('accepted')}>Accept all {members.length}</button>
                      <button className="link-btn" onClick={() => decideStack('rejected')}>Reject all</button>
                    </div>
                  </Fragment>
                )
              })}
              {suggestions.length > 0 && (
                <>
                  <button className="disclosure" aria-expanded={suggestionsOpen} onClick={() => setShowSuggestions((s) => {
                    const n = new Set(s)
                    if (n.has(slide)) n.delete(slide); else n.add(slide)
                    return n
                  })}>
                    <span className="dot dot-sugg" aria-hidden="true" />
                    {suggestions.length} suggestion{suggestions.length === 1 ? '' : 's'} <span className="faint">· optional</span>
                    <span className="grow" /><span aria-hidden="true">{suggestionsOpen ? '−' : '+'}</span>
                  </button>
                  {suggestionsOpen && suggestions.map(renderCard)}
                </>
              )}
              {slideOpen === 0 && slide < slideCount && (
                <div className="slide-done">
                  <p className="h-small">Slide {slide} done</p>
                  <button className="btn btn-secondary btn-sm" onClick={() => goSlide(slide + 1)}>Next slide <ChevronRight /></button>
                </div>
              )}
            </div>
          )}

          <div className="panel-foot">
            <div className="progress-line" title={`${findings.length} findings grouped into ${totalDecisions} decisions`}>
              <span className="fine"><b>{reviewed}</b> of {totalDecisions} decided</span>
              <span className="mini-bar" aria-hidden="true"><span style={{ width: `${totalDecisions ? (reviewed / totalDecisions) * 100 : 0}%` }} /></span>
            </div>
            {!!result.coverage?.manual_checks?.length && (
              <details className="by-eye">
                <summary>What to check by eye</summary>
                <ul>{result.coverage.manual_checks.map((t) => <li key={t}>{t}</li>)}</ul>
              </details>
            )}
            {deck.guideline?.id && deck.guideline.id !== 'acme' && <p className="fine">Checked against {deck.guideline.name}. Custom rules run without the agent.</p>}
            {!!result.coverage?.unmapped_clauses?.length && (
              <details className="by-eye">
                <summary>{result.coverage.unmapped_clauses.length} guideline {result.coverage.unmapped_clauses.length === 1 ? 'line needs' : 'lines need'} your review</summary>
                <ul>{result.coverage.unmapped_clauses.map((c) => <li key={c.line}>Line {c.line}: {c.text}</li>)}</ul>
              </details>
            )}
            <ChecksLine deck={deck} />
          </div>
        </aside>
      </div>

      {confirming && (
        <ApplyDialog findings={findings} decisions={decisions} upgrades={chosenUpgrades} busy={applying}
          onCancel={() => setConfirming(false)} onApply={applyFixes} />
      )}
      <Toast toast={toast} onUndo={undo} />
    </div>
  )
}

function ChecksLine({ deck }: { deck: DeckState }) {
  const w = deck.result?.checks?.writing
  if (deck.guideline?.id && deck.guideline.id !== 'acme') return null
  const ok = w?.state === 'completed'
  const text = !w || w.state === 'off' ? 'Agent suggestions off'
    : ok ? `Agent reviewed ${w.slides ?? 0} slide${w.slides === 1 ? '' : 's'}`
    : w.state === 'partial' ? `Agent review incomplete · ${w.unavailable} of ${w.slides} slide${w.slides === 1 ? '' : 's'} missed`
    : 'Agent review unavailable'
  return (
    <p className={`checks-line${ok ? '' : ' is-limited'}`}>
      <span aria-hidden="true">✓</span> Code checks · <span aria-hidden="true">{ok ? '✓' : '–'}</span> {text}
    </p>
  )
}

function Toast({ toast, onUndo }: { toast: { text: string; undo?: boolean } | null; onUndo: () => void }) {
  return (
    <>
      <div className="sr-only" role="status" aria-live="polite">{toast?.text ?? ''}</div>
      {toast && (
        <div className="toast">
          <span aria-hidden="true">{toast.text}</span>
          {toast.undo && <button className="toast-btn" onClick={onUndo}>Undo</button>}
        </div>
      )}
    </>
  )
}
