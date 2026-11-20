const STEPS = ['Upload', 'Review', 'Apply', 'Download'] as const

/** The same four steps on every screen, so the reviewer always knows what comes next. */
export function Stepper({ active }: { active: 1 | 2 | 3 | 4 }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STEPS.map((name, i) => {
        const n = i + 1
        const state = n < active ? 'done' : n === active ? 'on' : 'todo'
        return (
          <li key={name} className={`step is-${state}`} aria-current={state === 'on' ? 'step' : undefined}>
            <span className="step-dot" aria-hidden="true">{state === 'done' ? <Check /> : n}</span>
            <span className="step-name">{name}</span>
            {state === 'done' && <span className="sr-only"> (done)</span>}
          </li>
        )
      })}
    </ol>
  )
}

export function Check({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  )
}
