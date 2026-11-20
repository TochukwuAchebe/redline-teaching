import type { Finding, Severity } from './types'

export const SEVERITIES: Severity[] = ['error', 'warning', 'suggestion']

/** Plain-language severity: what the reviewer should do about it. */
export const SEVERITY_LABEL: Record<Severity, string> = {
  error: 'Must fix',
  warning: 'Should fix',
  suggestion: 'Suggestion',
}

/** CSS name for each severity's colour. */
export const SEVERITY_NAME: Record<Severity, 'must' | 'should' | 'sugg'> = {
  error: 'must',
  warning: 'should',
  suggestion: 'sugg',
}

export const severityRank = (s: Severity) => SEVERITIES.indexOf(s)

export function worstSeverity(findings: Finding[]): Severity | null {
  let worst: Severity | null = null
  for (const f of findings) {
    if (worst === null || severityRank(f.severity) < severityRank(worst)) worst = f.severity
  }
  return worst
}

/** Stable sort: must fix > should fix > suggestion, preserving backend order within a severity. */
export function sortBySeverity(findings: Finding[]): Finding[] {
  return findings
    .map((f, i) => ({ f, i }))
    .sort((a, b) => severityRank(a.f.severity) - severityRank(b.f.severity) || a.i - b.i)
    .map((x) => x.f)
}
