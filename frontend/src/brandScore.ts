import type { Decision, FixState } from './client'
import type { Finding } from './types'

/** Redline can make this change itself. */
export const isAuto = (f: Finding) => Boolean((f.fix as { auto?: boolean } | null)?.auto)

/** The change was written into the corrected deck and the re-check no longer finds the issue. */
export function isVerified(f: Finding, fix?: FixState | null): boolean {
  const row = fix?.state === 'done' ? fix.report?.[f.id] : undefined
  return Boolean(row && row.status === 'applied' && row.verified !== false)
}

/** Points an open issue costs its slide. Suggestions are advice and never count. */
const COST = { error: 25, warning: 10, suggestion: 0 } as const

function average(findings: Finding[], slideCount: number, open: (f: Finding) => boolean): number {
  if (slideCount < 1) return 100
  const lost = new Map<number, number>()
  for (const f of findings) if (open(f)) lost.set(f.slide, (lost.get(f.slide) ?? 0) + COST[f.severity])
  let total = 0
  for (let n = 1; n <= slideCount; n++) total += Math.max(0, 100 - (lost.get(n) ?? 0))
  return Math.round(total / slideCount)
}

/**
 * Checked score: the deck as last measured. Every slide starts at 100; each open must-fix costs 25
 * and each should-fix 10 (never below 0). Only a fix applied and verified by the re-check raises it.
 * Decisions alone never change it: a rejected flag is the reviewer's call, not proof the slide complies.
 */
export function checkedScore(findings: Finding[], slideCount: number, fix?: FixState | null): number {
  return average(findings, slideCount, (f) => !isVerified(f, fix))
}

/** Projected score: the checked score once the accepted automatic fixes are applied. */
export function projectedScore(findings: Finding[], slideCount: number, decisions: Record<string, Decision> = {}, fix?: FixState | null): number {
  const applied = fix?.state === 'done'
  return average(findings, slideCount, (f) => !isVerified(f, fix) && !(!applied && decisions[f.id] === 'accepted' && isAuto(f)))
}

/** Must-fix and should-fix flags the reviewer dismissed: shown, never counted as compliance. */
export function dismissedCount(findings: Finding[], decisions: Record<string, Decision>): number {
  return findings.filter((f) => f.severity !== 'suggestion' && decisions[f.id] === 'rejected').length
}
