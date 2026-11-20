/** Brand score: the average slide score (see brandScore.ts). */
export function ScoreRing({ score, size = 24 }: { score: number; size?: number }) {
  const tone = score >= 80 ? 'var(--done)' : score >= 50 ? 'var(--should)' : 'var(--must)'
  return (
    <span className="ring" style={{ width: size, height: size, background: `conic-gradient(${tone} ${score}%, var(--sub-2) 0)` }} aria-hidden="true">
      <span />
    </span>
  )
}

/** The checked score, with what the accepted fixes would make it and how many flags were dismissed. */
export function ScoreChip({ checked, projected, dismissed }: { checked: number; projected: number; dismissed: number }) {
  const ahead = projected !== checked
  const title = `Brand score ${checked}, as last checked: each slide starts at 100; an open must-fix costs 25 and a should-fix 10. `
    + 'It changes only when a fix is applied and verified by the re-check. '
    + (ahead ? `Accepted fixes would make it ${projected} once applied. ` : '')
    + (dismissed ? `${dismissed} flag${dismissed === 1 ? '' : 's'} dismissed by the reviewer still count${dismissed === 1 ? 's' : ''}; dismissing a flag is not proof the slide complies.` : '')
  return (
    <span className="score" title={title} aria-label={title}>
      <ScoreRing score={checked} />
      <b>{checked}</b>
      <span className="score-label">Brand score</span>
      {ahead && <span className="score-ahead">→ {projected} after apply</span>}
    </span>
  )
}
