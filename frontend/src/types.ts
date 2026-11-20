// Mirrors backend/app/model/types.py (Finding, BBox, AnalysisResult). Keep in sync.
// Units: positions/sizes in EMU (914400 per inch). Optional[...] fields arrive as null.

export type Severity = 'error' | 'warning' | 'suggestion'
export type Origin = 'code' | 'ai'

export interface BBox {
  x: number
  y: number
  w: number
  h: number
}

export interface Finding {
  id: string
  rule_id: string
  severity: Severity
  origin: Origin
  slide: number
  element_id: string | null
  bbox: BBox | null
  title: string
  message: string
  expected: string | null
  actual: string | null
  quote: string | null
  rule_ref: string
  suggested_fix: string | null
  confidence: number
  group_id: string | null
  fix: Record<string, unknown> | null
}

export interface AnalysisResult {
  coverage?: { manual_checks?: string[]; formatting?: string; ai?: { state: string; reviewed?: number; cached?: number; unavailable?: number }; unmapped_clauses?: {line: number; text: string}[] }
  deck_id: string
  filename: string
  slide_count: number
  slide_width: number
  slide_height: number
  findings: Finding[]
  stats: Record<string, number>
  /** What actually ran, so a partial check never looks like a clean deck. */
  checks?: {
    formatting?: { state: string; slides?: number; findings?: number }
    writing?: { state: 'off' | 'running' | 'completed' | 'partial' | 'unavailable'; slides?: number; reviewed?: number; cached?: number; unavailable?: number; filtered?: number; suggestions?: number }
  }
}
