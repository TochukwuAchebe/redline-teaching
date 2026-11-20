// Offline fixture for `npm run dev:mock`. Not used by the default build.
import { ApiError, csvFilename, downloadBlob, type Api, type DeckState, type Decision, type HistoryItem } from './client'
import type { AnalysisResult, BBox, Finding } from './types'

const W = 9144000
const H = 6858000
const DEMO_ID = 'demo'

const b = (x: number, y: number, w: number, h: number): BBox => ({ x, y, w, h })

// Shared slide geometry (EMU)
const TITLE = b(457200, 274320, 8229600, 868680)
const CONF = b(457200, 6400800, 6400800, 228600)
const BODY = b(457200, 1371600, 8229600, 4572000)
const TABLE = b(457200, 1554480, 8229600, 3657600)
const HEADER_ROW = b(457200, 1554480, 8229600, 457200)
const FIRST_COL = b(457200, 2011680, 1828800, 3200400)

type Shape = { box: BBox; kind: 'title' | 'text' | 'lines' | 'table' | 'chart' | 'footer' | 'key' | 'note'; label?: string }

const SLIDES: Shape[][] = [
  [
    { box: b(685800, 2286000, 7772400, 1143000), kind: 'title', label: 'Executive Compensation Review' },
    { box: b(685800, 3520440, 7772400, 457200), kind: 'text', label: 'DRAFT — Compensation Committee' },
    { box: b(685800, 4114800, 7772400, 640080), kind: 'lines' },
  ],
  [
    { box: TITLE, kind: 'title', label: 'Our approach to peer group selection.' },
    { box: BODY, kind: 'lines' },
    { box: CONF, kind: 'footer' },
  ],
  [
    { box: TITLE, kind: 'title', label: 'Base salary is positioned near the market median' },
    { box: TABLE, kind: 'table' },
    { box: CONF, kind: 'footer' },
  ],
  [
    { box: TITLE, kind: 'title', label: 'Target bonus opportunity trails peers' },
    { box: b(457200, 1463040, 8229600, 4480560), kind: 'chart' },
  ],
  [
    { box: TITLE, kind: 'title', label: 'Long-term incentive mix is in line with market' },
    { box: b(457200, 1463040, 4023360, 4480560), kind: 'lines' },
    { box: b(4663440, 1463040, 4023360, 4480560), kind: 'chart' },
    { box: CONF, kind: 'footer' },
  ],
  [
    { box: TITLE, kind: 'title', label: 'Total direct compensation vs. peer group' },
    { box: b(457200, 1554480, 6400800, 3657600), kind: 'table' },
    { box: b(7040880, 1554480, 1645920, 1188720), kind: 'key', label: 'Legend' },
    { box: b(7040880, 3017520, 1645920, 914400), kind: 'note', label: 'Question: confirm FY24 numbers before sending?' },
    { box: CONF, kind: 'footer' },
  ],
]

const f = (p: Partial<Finding> & Pick<Finding, 'id' | 'rule_id' | 'severity' | 'slide' | 'title' | 'message' | 'rule_ref'>): Finding => ({
  origin: 'code',
  element_id: null,
  bbox: null,
  expected: null,
  actual: null,
  quote: null,
  suggested_fix: null,
  confidence: 1,
  group_id: null,
  fix: null,
  ...p,
})

const PCT_GROUP = 'TERM_PERCENTILE:"50th %ile"'

const buildFindings = (): Finding[] => [
  f({
    id: 'TITLESLIDE_DRAFT:1:s1-3', rule_id: 'TITLESLIDE_DRAFT', severity: 'error', slide: 1, element_id: 's1-3',
    bbox: b(685800, 3520440, 7772400, 457200), title: 'Draft designator on title slide',
    message: 'The title slide still carries a "DRAFT" designator, which must be removed before the deck is shared.',
    expected: 'No draft designator', actual: '"DRAFT" in subtitle', quote: 'DRAFT', rule_ref: 'Title Slides',
  }),
  f({
    id: 'TITLESLIDE_DATE:1:slide', rule_id: 'TITLESLIDE_DATE', severity: 'warning', slide: 1,
    title: 'Title slide date missing or invalid', message: 'No presentation date was found on the title slide.',
    expected: 'Month YYYY', actual: 'No date', rule_ref: 'Title Slides',
  }),
  f({
    id: 'TITLE_PUNCTUATION:2:s2-2', rule_id: 'TITLE_PUNCTUATION', severity: 'warning', slide: 2, element_id: 's2-2',
    bbox: TITLE, title: 'Headline ends with punctuation', message: 'Slide headlines should not end with a full stop.',
    expected: 'No terminal punctuation', actual: 'Ends with "."', quote: 'Our approach to peer group selection.',
    rule_ref: 'Content Slides › Slide Title',
  }),
  f({
    id: 'BULLET_GLYPH:2:s2-3:1', rule_id: 'BULLET_GLYPH', severity: 'warning', slide: 2, element_id: 's2-3',
    bbox: b(822960, 2560320, 7680960, 320040), title: 'Wrong bullet symbol for level',
    message: 'Second-level bullets should use a dash, not a round bullet.',
    expected: 'Dash (–) at level 2', actual: 'Circle (•)', rule_ref: 'Bulleted Lists',
  }),
  f({
    id: 'BULLET_PARALLEL:2:s2-3', rule_id: 'BULLET_PARALLEL', severity: 'suggestion', origin: 'ai', slide: 2, element_id: 's2-3',
    bbox: BODY, title: 'Bullets not parallel in structure', confidence: 0.82,
    message: 'Most bullets start with a verb, but one is phrased as a noun clause.',
    quote: 'Market data for each role reviewed', suggested_fix: 'Review market data for each role',
    rule_ref: 'Bulleted Lists',
  }),
  f({
    id: 'TABLE_HEADER_STYLE:3:s3-4', rule_id: 'TABLE_HEADER_STYLE', severity: 'warning', slide: 3, element_id: 's3-4',
    bbox: HEADER_ROW, title: 'Table header styling', message: 'The table header fill is not the ACME header blue.',
    expected: '#006EBE', actual: '#1F4E79', rule_ref: 'Tables › Table Header',
  }),
  f({
    id: 'TERM_PERCENTILE:3:s3-4', rule_id: 'TERM_PERCENTILE', severity: 'warning', slide: 3, element_id: 's3-4',
    bbox: b(4572000, 1554480, 1371600, 457200), title: 'Use "%ile", not "Percentile"',
    message: 'Column headers should abbreviate "Percentile" to "%ile".',
    expected: '50th %ile', actual: '50th Percentile', quote: '50th Percentile', rule_ref: 'Tables › Overall', group_id: PCT_GROUP,
  }),
  f({
    id: 'TABLE_CELL_MARGIN:3:s3-4', rule_id: 'TABLE_CELL_MARGIN', severity: 'warning', slide: 3, element_id: 's3-4',
    bbox: FIRST_COL, title: 'First column cell margin', message: 'First-column text sits flush against the cell edge.',
    expected: '0.03 in', actual: '0.00 in', rule_ref: 'Tables › Overall',
  }),
  f({
    id: 'CONF_MISSING:4:slide', rule_id: 'CONF_MISSING', severity: 'error', slide: 4, bbox: CONF,
    title: 'Confidentiality statement missing', message: 'This slide has no confidentiality statement in the footer.',
    expected: 'Confidential. Prepared for teaching purposes only…', actual: 'Not present', rule_ref: 'Content Slides › Confidentiality Statement',
  }),
  f({
    id: 'TERM_PERCENTILE:4:s4-3', rule_id: 'TERM_PERCENTILE', severity: 'warning', slide: 4, element_id: 's4-3',
    bbox: b(822960, 5577840, 2286000, 274320), title: 'Use "%ile", not "Percentile"',
    message: 'The axis label should abbreviate "Percentile" to "%ile".',
    expected: '50th %ile', actual: '50th Percentile', quote: '50th Percentile', rule_ref: 'Tables › Overall', group_id: PCT_GROUP,
  }),
  f({
    id: 'TERM_PERCENTILE:6:s6-4', rule_id: 'TERM_PERCENTILE', severity: 'warning', slide: 6, element_id: 's6-4',
    bbox: b(3657600, 1554480, 1371600, 457200), title: 'Use "%ile", not "Percentile"',
    message: 'Column headers should abbreviate "Percentile" to "%ile".',
    expected: '50th %ile', actual: '50th Percentile', quote: '50th Percentile', rule_ref: 'Tables › Overall', group_id: PCT_GROUP,
  }),
  f({
    id: 'KEY_LABEL_INCONSISTENT:6:s6-7', rule_id: 'KEY_LABEL_INCONSISTENT', severity: 'warning', slide: 6, element_id: 's6-7',
    bbox: b(7040880, 1554480, 1645920, 274320), title: '"Key" vs "Legend" inconsistent',
    message: 'Most slides label the key "Key"; this slide uses "Legend".',
    expected: '"Key" (4 of 5 slides)', actual: '"Legend"', quote: 'Legend', rule_ref: 'Writing Style › Consistent terminology',
  }),
  f({
    id: 'LEFTOVER_COMMENT:6:s6-9', rule_id: 'LEFTOVER_COMMENT', severity: 'warning', slide: 6, element_id: 's6-9',
    bbox: b(7040880, 3017520, 1645920, 914400), title: 'Leftover reviewer comment',
    message: 'A reviewer note was left on the slide and should be resolved and removed.',
    quote: 'Question: confirm FY24 numbers before sending?', rule_ref: 'Writing Style',
  }),
  f({
    id: 'TABLE_UNITS_MISSING:6:s6-4', rule_id: 'TABLE_UNITS_MISSING', severity: 'suggestion', slide: 6, element_id: 's6-4',
    bbox: b(457200, 1554480, 6400800, 457200), title: 'Units not stated',
    message: 'Monetary columns do not state their units (e.g. "$000s").',
    expected: 'Units in header', actual: 'None', rule_ref: 'Tables › Table Header',
  }),
]

let fixture: AnalysisResult | null = null

function demoResult(): AnalysisResult {
  if (fixture) return fixture
  const findings = buildFindings()
  const stats: Record<string, number> = {}
  for (const x of findings) stats[x.severity] = (stats[x.severity] ?? 0) + 1
  fixture = {
    deck_id: DEMO_ID,
    filename: 'ACME-exec-comp-review.pptx',
    slide_count: SLIDES.length,
    slide_width: W,
    slide_height: H,
    findings,
    stats,
  }
  return fixture
}

// --- placeholder slide images -------------------------------------------------

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const k = (v: number) => Math.round(v / 1000) // EMU -> viewBox units

function shapeSvg({ box, kind, label }: Shape): string {
  const x = k(box.x), y = k(box.y), w = k(box.w), h = k(box.h)
  switch (kind) {
    case 'title':
      return `<text x="${x}" y="${y + h * 0.62}" font-family="Calibri, Arial, sans-serif" font-size="${Math.min(h * 0.42, 360)}" font-weight="700" fill="#3a3d42">${esc(label ?? '')}</text>`
    case 'text':
      return `<text x="${x}" y="${y + h * 0.65}" font-family="Calibri, Arial, sans-serif" font-size="${h * 0.5}" fill="#55595f">${esc(label ?? '')}</text>`
    case 'lines': {
      const rows = Math.max(2, Math.floor(h / 380))
      return Array.from({ length: rows }, (_, i) => {
        const indent = i % 3 === 2 ? 370 : 0
        return `<rect x="${x + indent}" y="${y + i * 380 + 90}" width="90" height="90" fill="#9ea2a8"/><rect x="${x + indent + 200}" y="${y + i * 380 + 80}" width="${(w - indent - 200) * (0.55 + ((i * 37) % 40) / 100)}" height="110" rx="20" fill="#c9ccd1"/>`
      }).join('')
    }
    case 'table': {
      const rows = 7, cols = 5, rh = h / rows, cw = w / cols
      let s = `<rect x="${x}" y="${y}" width="${w}" height="${rh}" fill="#8f949b"/>`
      for (let r = 1; r < rows; r++) if (r % 2 === 0) s += `<rect x="${x}" y="${y + r * rh}" width="${w}" height="${rh}" fill="#dcdee2"/>`
      for (let r = 0; r < rows; r++)
        for (let c = 0; c < cols; c++)
          s += `<rect x="${x + c * cw + 60}" y="${y + r * rh + rh * 0.38}" width="${cw * (c === 0 ? 0.7 : 0.4)}" height="${rh * 0.24}" rx="16" fill="${r === 0 ? '#eef0f2' : '#b4b8be'}"/>`
      return s + `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="#b4b8be" stroke-width="12"/>`
    }
    case 'chart': {
      const bars = 6, gap = w / bars
      let s = `<line x1="${x}" y1="${y + h - 300}" x2="${x + w}" y2="${y + h - 300}" stroke="#9ea2a8" stroke-width="14"/>`
      for (let i = 0; i < bars; i++) {
        const bh = (h - 600) * (0.35 + ((i * 53) % 60) / 100)
        s += `<rect x="${x + i * gap + gap * 0.2}" y="${y + h - 300 - bh}" width="${gap * 0.6}" height="${bh}" fill="${i === 3 ? '#8f949b' : '#c9ccd1'}"/>`
      }
      return s + `<rect x="${x + 365}" y="${y + h - 220}" width="${2286000 / 1000 - 100}" height="110" rx="20" fill="#b4b8be"/>`
    }
    case 'footer':
      return `<rect x="${x}" y="${y + 70}" width="${w * 0.9}" height="80" rx="20" fill="#c9ccd1"/>`
    case 'key':
      return `<text x="${x}" y="${y + 210}" font-family="Calibri, Arial, sans-serif" font-size="180" font-weight="700" fill="#55595f">${esc(label ?? '')}</text>` +
        [0, 1, 2].map((i) => `<rect x="${x}" y="${y + 420 + i * 260}" width="180" height="180" fill="${['#8f949b', '#c9ccd1', '#dcdee2'][i]}"/><rect x="${x + 260}" y="${y + 460 + i * 260}" width="900" height="100" rx="20" fill="#b4b8be"/>`).join('')
    case 'note':
      return `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#f4f5f6" stroke="#9ea2a8" stroke-width="14"/><foreignObject x="${x + 80}" y="${y + 60}" width="${w - 160}" height="${h - 120}"><div xmlns="http://www.w3.org/1999/xhtml" style="font:150px Calibri,Arial,sans-serif;color:#55595f">${esc(label ?? '')}</div></foreignObject>`
  }
}

const imageCache = new Map<number, string>()

function slideImage(n: number): string {
  const cached = imageCache.get(n)
  if (cached) return cached
  const shapes = SLIDES[n - 1] ?? []
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${k(W)} ${k(H)}"><rect width="100%" height="100%" fill="#e9eaec"/>${shapes.map(shapeSvg).join('')}<text x="${k(W) - 300}" y="${k(H) - 200}" font-family="Arial" font-size="140" fill="#9ea2a8" text-anchor="end">${n}</text></svg>`
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
  imageCache.set(n, url)
  return url
}

// --- in-memory API ------------------------------------------------------------

const DECISIONS_KEY = 'redline-mock-decisions'

function loadDecisions(): Record<string, Record<string, Decision>> {
  try {
    return JSON.parse(sessionStorage.getItem(DECISIONS_KEY) ?? '{}')
  } catch {
    return {}
  }
}

let decisionStore: Record<string, Record<string, Decision>> | null = null
const store = () => (decisionStore ??= loadDecisions())
const uploads = new Map<string, { filename: string; started: number }>()
let authed = true

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))

function guard() {
  if (!authed) throw new ApiError(401, 'Not signed in')
}

export const mockApi: Api = {
  defaultDeckId: DEMO_ID,

  async checkSession() {
    return authed
  },

  async login(password) {
    await delay(300)
    if (!password) throw new ApiError(401, 'Incorrect password')
    authed = true
  },

  async logout() {
    authed = false
  },

  async uploadDeck(file, onProgress, signal) {
    guard()
    for (let i = 1; i <= 10; i++) {
      await delay(80)
      if (signal?.aborted) {
        const err = new ApiError(0, 'Upload cancelled')
        err.aborted = true
        throw err
      }
      onProgress?.(i / 10)
    }
    const id = `mock-${Date.now().toString(36)}`
    uploads.set(id, { filename: file.name, started: Date.now() })
    return { deck_id: id }
  },

  async getDeck(id) {
    guard()
    await delay(120)
    const upload = uploads.get(id)
    if (id !== DEMO_ID && !upload && !id.startsWith('mock-')) throw new ApiError(404, 'Deck not found')
    const RESULT = demoResult()
    const filename = upload?.filename ?? RESULT.filename
    const elapsed = upload ? Date.now() - upload.started : Infinity
    const base: DeckState = { deck_id: id, filename, status: 'done', decisions: { ...(store()[id] ?? {}) } }
    if (elapsed < 1000) return { ...base, status: 'queued' }
    if (elapsed < 3500) return { ...base, status: 'rendering' }
    if (elapsed < 6500) return { ...base, status: 'analysing' }
    if (/fail/i.test(filename)) return { ...base, status: 'error', error: 'The slides could not be rendered (simulated failure).' }
    return { ...base, slide_count: RESULT.slide_count, result: { ...RESULT, deck_id: id, filename } }
  },

  async putDecisions(id, patch) {
    guard()
    await delay(60)
    const all = store()
    const d = (all[id] ??= {})
    for (const [fid, v] of Object.entries(patch)) {
      if (v === null) delete d[fid]
      else d[fid] = v
    }
    try {
      sessionStorage.setItem(DECISIONS_KEY, JSON.stringify(all))
    } catch {
      /* storage unavailable */
    }
  },

  async listDecks() {
    guard()
    const rows: HistoryItem[] = [...uploads.entries()].map(([id, u]) => ({
      deck_id: id, filename: u.filename, status: 'done', created_at: new Date(u.started).toISOString(),
      slide_count: 6, stats: { error: 2, warning: 9, suggestion: 3 }, decisions_total: 12, decisions_made: 0,
    }))
    return [...rows.reverse(), {
      deck_id: DEMO_ID, filename: 'Northwind Traders pay review.pptx', status: 'done',
      created_at: new Date(Date.now() - 86400000).toISOString(), slide_count: 6,
      stats: { error: 2, warning: 9, suggestion: 3 }, decisions_total: 12, decisions_made: 5,
    }]
  },

  async recheckDeck(id) {
    const id2 = `mock-${Date.now().toString(36)}`
    uploads.set(id2, { filename: uploads.get(id)?.filename ?? 'Northwind Traders pay review.pptx', started: Date.now() })
    return { deck_id: id2 }
  },

  async cancelDeck(id) {
    uploads.delete(id)
    return true
  },

  async applyFixes() {
    await delay(300)
  },

  fixedSlideUrl(id, n) {
    return mockApi.slideUrl(id, n)
  },

  correctedDeckUrl() {
    return '#'
  },

  slideUrl(_id, n) {
    return slideImage(n)
  },

  async listUpgrades() {
    return { candidates: [], items: {} }
  },

  async generateUpgrade() {
    return { state: 'unavailable', note: 'Design suggestions need the server.' }
  },

  async chooseUpgrade() {
    return { state: 'unavailable', note: 'Design suggestions need the server.' }
  },

  upgradePreviewUrl(id, n) {
    return mockApi.slideUrl(id, n)
  },

  async exportCsv(id, filename) {
    guard()
    const d = store()[id] ?? {}
    const cell = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
    const rows = demoResult().findings.filter((x) => d[x.id] === 'accepted').map((x) =>
      [x.slide, x.severity, x.rule_id, x.title, x.message, x.expected, x.actual, x.rule_ref].map(cell).join(','),
    )
    const csv = ['slide,severity,rule_id,title,message,expected,actual,rule_ref', ...rows].join('\n')
    downloadBlob(new Blob([csv], { type: 'text/csv' }), csvFilename(filename))
  },
}
