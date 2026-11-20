import type { Finding } from './types'

/** An overlapping outline must not silently choose whichever box paints last. */
export function highlightHits(findings: Finding[], x: number, y: number, padding: number): Finding[] {
  return findings.filter(({ bbox: b }) => b && x >= b.x - padding && x <= b.x + b.w + padding
    && y >= b.y - padding && y <= b.y + b.h + padding)
    .sort((a, b) => a.bbox!.w * a.bbox!.h - b.bbox!.w * b.bbox!.h)
}
