import type { AnalysisResult } from './types'

/** accepted: Redline changes it (or, when it can't, it's a to-do). todo: the reviewer changes it. rejected: keep as is. */
export type Decision = 'accepted' | 'todo' | 'rejected'
export type DeckStatus = 'queued' | 'rendering' | 'analysing' | 'done' | 'error' | 'cancelled'

/** Outcome of applying one accepted flag to the corrected copy of the deck. */
export interface FixRow {
  status: 'applied' | 'manual' | 'failed'
  note: string
  /** true once a re-check of the corrected deck no longer finds the issue; null when it can't be re-checked. */
  verified?: boolean | null
}

export interface FixState {
  state: 'running' | 'done' | 'error'
  report?: Record<string, FixRow>
  counts?: Partial<Record<'applied' | 'manual' | 'failed', number>>
  version?: number
  error?: string
  /** Redesigns applied with the fixes, by slide number. */
  upgrades?: Record<string, { status: 'applied' | 'failed'; appendix_slide?: number; note?: string }>
}

export interface UpgradeProposal {
  slide: number
  element_id: string
  headline: string
  original_headline: string
  chart_title: string
  appendix_title: string
  highlight: string[]
  points: [string, number, string][]
  stats: Record<string, number>
  model: string
  red_box?: string[]
  layout?: 'chart_panels' | 'panels'
  headline_source?: 'slide' | 'template'
  kind?: 'chart' | 'text'
  panels?: { title: string; bullets: string[] }[]
  /** Guideline issues on this slide before and after the redesign (code checks). */
  issues_before?: number
  issues_after?: number
  /** Each change and the ACME guideline line it follows. */
  basis?: { change: string; guideline: string }[]
}

export interface UpgradeItem {
  state: 'ready' | 'unavailable'
  proposal?: UpgradeProposal
  note?: string
  choice?: 'use' | null
}

export interface Upgrades {
  candidates: { slide: number; element_id: string; rows: number; kind?: 'chart' | 'text' }[]
  items: Record<string, UpgradeItem>
}

export interface DeckState {
  deck_id: string
  filename: string
  status: DeckStatus
  guideline?: { id: string; name: string; version: string }
  analysis_coverage?: { ai: string; detail?: string }
  error?: string | null
  slide_count?: number | null
  /** Slide images available so far, and the deck's slide total once known. */
  rendered?: number
  slides_total?: number | null
  result?: AnalysisResult | null
  decisions: Record<string, Decision>
  fix?: FixState | null
}

/** One row of the shared review history. */
export interface HistoryItem {
  deck_id: string
  filename: string
  status: DeckStatus
  created_at: string
  slide_count?: number | null
  stats: Partial<Record<'error' | 'warning' | 'suggestion', number>>
  decisions_total: number
  decisions_made: number
}

export type DecisionPatch = Record<string, Decision | null>

export interface Api {
  /** True when a valid session cookie exists. */
  checkSession(): Promise<boolean>
  login(password: string): Promise<void>
  logout(): Promise<void>
  /** Rejects with an ApiError whose `aborted` flag is set when `signal` aborts. */
  uploadDeck(file: File, onProgress?: (fraction: number) => void, signal?: AbortSignal, guidelineId?: string): Promise<{ deck_id: string }>
  /** Stops a queued or running analysis; safe to call for finished decks. */
  cancelDeck(id: string): Promise<boolean>
  getDeck(id: string): Promise<DeckState>
  /** Every review on the server, newest first. */
  listDecks(): Promise<HistoryItem[]>
  listDecksPage?(query: string, offset: number): Promise<{decks: HistoryItem[]; total: number; next_offset: number | null}>
  /** Review a stored deck again as a new review; the original and its decisions are kept. */
  recheckDeck(id: string): Promise<{ deck_id: string }>
  putDecisions(id: string, patch: DecisionPatch): Promise<void>
  slideUrl(id: string, n: number): string
  /** Apply every accepted flag to a corrected copy of the deck (runs in the background). */
  applyFixes(id: string): Promise<void>
  fixedSlideUrl(id: string, n: number, version: number): string
  correctedDeckUrl(id: string): string
  exportCsv(id: string, filename: string): Promise<void>
  /** Slides whose table could read better as a chart, and any designs made so far. */
  listUpgrades(id: string): Promise<Upgrades>
  /** Ask for a redesign of one slide (takes a few seconds). */
  generateUpgrade(id: string, slide: number): Promise<UpgradeItem>
  chooseUpgrade(id: string, slide: number, choice: 'use' | null): Promise<UpgradeItem>
  upgradePreviewUrl(id: string, slide: number, appendix?: boolean): string
  /** Mock mode only: deck to open when the URL has none. */
  defaultDeckId?: string
}

export class ApiError extends Error {
  status: number
  aborted = false
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let unauthorizedHandler: () => void = () => {}

export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn
}

export function notifyUnauthorized() {
  unauthorizedHandler()
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function csvFilename(deckFilename: string) {
  return `${deckFilename.replace(/\.pptx$/i, '') || 'deck'}-redline.csv`
}
