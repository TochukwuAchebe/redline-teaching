import type { ReactNode } from 'react'
import { Stepper } from './Stepper'
import { Wordmark } from './Wordmark'

/** The bar on every signed-in screen: brand, where you are in the journey, and the next action. */
export function AppBar({ step, left, right, onHome, heading }: { step: 1 | 2 | 3 | 4; left?: ReactNode; right?: ReactNode; onHome?: () => void; heading?: string }) {
  return (
    <header className="appbar">
      {heading && <h1 className="sr-only">{heading}</h1>}
      <div className="appbar-left">
        {onHome ? (
          <button type="button" className="mark-btn" onClick={onHome} aria-label="Redline home"><Wordmark /></button>
        ) : (
          <Wordmark />
        )}
        {left}
      </div>
      <div className="appbar-center"><Stepper active={step} /></div>
      <div className="appbar-right">{right}</div>
    </header>
  )
}
