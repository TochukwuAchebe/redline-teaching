import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from '../api'
import { ApiError } from '../client'
import { Wordmark } from '../components/Wordmark'
import { SampleAfter, SampleBefore } from '../components/SampleSlide'
import { Arrow, UploadIcon } from '../components/Icons'

const STEPS = [
  { title: 'Upload', text: 'Drop in a PowerPoint. Nothing to install, nothing to set up.', visual: 'upload' },
  { title: 'See the issues', text: 'Every check Redline can measure from the file, marked on the slide and ranked by what matters. The rest is listed for you to check by eye.', visual: 'issues' },
  { title: 'Accept or reject', text: 'One click per flag. Repeated issues settle in one go, and you can undo anything.', visual: 'decide' },
  { title: 'Download a better deck', text: 'Accepted fixes go onto a copy and are checked again. Your original is never touched.', visual: 'download' },
] as const

const PROOF = [
  ['36 / 36', 'issues the brand reviewer marked on the annotated sample deck, found with the agent (34 by code alone)'],
  ['0', 'confirmed false positives in our own review of the 203 code findings on that deck: 193 supported, 10 ambiguous'],
  ['53 s', 'to check six decks, 112 slides, submitted together on the production image (one burst, measured in the container)'],
  ['100%', 'of agent suggestions quote the slide word for word, or they are dropped'],
] as const

function StepVisual({ kind }: { kind: (typeof STEPS)[number]['visual'] }) {
  if (kind === 'upload') return <div className="sv sv-upload"><UploadIcon /><span>Upload a file</span></div>
  if (kind === 'issues') {
    return (
      <div className="sv sv-issues">
        <span className="sv-line w80" /><span className="sv-line w60" />
        <span className="sv-box sev-must" style={{ left: '8%', top: '12%', width: '72%', height: '22%' }}><i>1</i></span>
        <span className="sv-box sev-should" style={{ left: '8%', top: '58%', width: '40%', height: '26%' }}><i>2</i></span>
        <span className="sv-box sev-sugg" style={{ left: '56%', top: '58%', width: '34%', height: '26%' }}><i>3</i></span>
      </div>
    )
  }
  if (kind === 'decide') {
    return (
      <div className="sv sv-decide">
        <span className="pill pill-should"><span className="dot" />Should fix · 7 places</span>
        <span className="sv-diff"><del>Percentile</del> <ins>%ile</ins></span>
        <span className="sv-buttons"><span className="btn btn-primary btn-sm">Accept all 7</span><span className="btn btn-ghost btn-sm">Reject</span></span>
      </div>
    )
  }
  return (
    <div className="sv sv-download">
      <span className="sv-mini" /><Arrow /><span className="sv-mini is-after" />
    </div>
  )
}

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const field = useRef<HTMLInputElement>(null)
  useEffect(() => {
    document.title = 'Redline · every deck, on brand'
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    try {
      await api.login(password)
      onSuccess()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'That password didn’t work. Check it and try again.' : (err as Error).message)
      setBusy(false)
      field.current?.focus()
    }
  }

  const toSignIn = () => {
    field.current?.focus()
    field.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  return (
    <div className="welcome">
      <header className="welcome-bar">
        <Wordmark size="large" />
        <nav aria-label="Welcome">
          <a href="#how">How it works</a>
          <a href="#better">What changes</a>
          <a href="#trust">Why trust it</a>
          <button type="button" className="btn btn-secondary btn-sm" onClick={toSignIn}>Sign in</button>
        </nav>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <h1 className="display">Every deck,<br />on brand.</h1>
            <p className="lede">
              Upload a PowerPoint. Code checks every slide against the ACME brand guidelines, the Redline agent suggests improvements, and
              you decide. Redline fixes what you accept and hands back a better deck, not a list of problems.
            </p>
            <form className="signin" onSubmit={submit}>
              <label className="sr-only" htmlFor="password">Access password</label>
              <input
                ref={field}
                id="password"
                className="field field-pill"
                type="password"
                autoComplete="current-password"
                placeholder="Access password"
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? 'login-error' : 'login-note'}
              />
              <button className="btn btn-primary btn-lg" type="submit" disabled={!password || busy}>
                {busy ? 'Signing in…' : <>Start reviewing <Arrow /></>}
              </button>
            </form>
            <p id="login-error" className="form-error" role="alert">{error}</p>
            {!error && <p id="login-note" className="fine">Private by invitation. Decks are visible only to your team.</p>}
          </div>
          <div className="hero-art" aria-hidden="true">
            <div className="hero-slide"><SampleBefore /><span className="hero-hl" /></div>
            <div className="hero-card">
              <span className="pill pill-should"><span className="dot" />Should fix · Headline</span>
              <p className="hero-card-title">Headline runs to four lines</p>
              <p className="hero-card-text">State the takeaway in three lines or fewer.</p>
              <span className="sv-buttons"><span className="btn btn-primary btn-sm">Accept</span><span className="btn btn-ghost btn-sm">Reject</span></span>
            </div>
            <div className="hero-after"><SampleAfter /><span className="pill pill-done hero-after-tag">Checked again</span></div>
          </div>
        </section>

        <section id="how" className="band-section">
          <div className="section-head">
            <div>
              <p className="eyebrow">How it works</p>
              <h2 className="h-section">From draft to board‑ready<br />in four steps.</h2>
            </div>
            <p className="section-aside">The same four steps sit at the top of every screen, so you always know where you are and what comes next.</p>
          </div>
          <ol className="how-grid">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <StepVisual kind={s.visual} />
                <p className="how-title"><span className="how-num">0{i + 1}</span>{s.title}</p>
                <p className="how-text">{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="better" className="band-section is-tinted">
          <div className="section-head">
            <div>
              <p className="eyebrow">Beyond the rulebook</p>
              <h2 className="h-section">Not just compliant.<br />Clearer.</h2>
            </div>
            <p className="section-aside">
              Once the rules are met, Redline can suggest a clearer layout for a slide: a headline that states the point and a
              chart where a long table hides the story. It stays inside the guidelines, and nothing changes until you accept it.
            </p>
          </div>
          <div className="compare-showcase">
            <figure><figcaption>Before · compliant, but hard to read</figcaption><div className="showcase-slide"><SampleBefore /></div></figure>
            <span className="compare-arrow" aria-hidden="true"><Arrow /></span>
            <figure><figcaption className="is-strong">After · the point in five seconds</figcaption><div className="showcase-slide is-raised"><SampleAfter /></div></figure>
          </div>
          <ul className="reasons">
            <li><b>Headline states the point</b>Worked out from the slide’s own numbers. Any figure not on the slide is refused.</li>
            <li><b>Chart where it helps</b>The subject in brand blue, peers in grey, the median marked. The full table moves to the appendix.</li>
            <li><b>Less to read</b>One clear message per slide, so the audience sees the point first.</li>
            <li><b>Still on brand</b>Calibri, palette colours, 14 pt chart title, 9 pt source note. Checked again after the change.</li>
          </ul>
        </section>

        <section id="trust" className="band-section">
          <p className="eyebrow">Why trust it</p>
          <h2 className="h-section">Code checks. The agent suggests.<br />You decide.</h2>
          <ul className="principles">
            <li><b>Code checks</b>Fonts, sizes, colours, positions, number formats and annotations are measured against the guideline, the same way every time. What can't be measured from the file is listed for you to check by eye.</li>
            <li><b>The agent suggests</b>The Redline agent reviews wording and proposes clearer slides, working only from the guidelines. Each wording suggestion quotes the slide; a redesign uses only the slide's own sentences and comparisons computed from its table.</li>
            <li><b>You decide</b>Nothing changes until you accept it. Accepted fixes go onto a copy, which is checked again.</li>
          </ul>
          <dl className="proof">
            {PROOF.map(([n, t]) => (
              <div key={n}><dt>{n}</dt><dd>{t}</dd></div>
            ))}
          </dl>
          <p className="fine">Measured on the ACME sample decks. Methods and results are published with the code.</p>
        </section>

        <section className="closing">
          <h2 className="display display-md">Ready for your<br />next deck.</h2>
          <button type="button" className="btn btn-inverse btn-lg" onClick={toSignIn}>Sign in and upload <Arrow /></button>
        </section>
      </main>
      <footer className="welcome-foot">
        <Wordmark />
        <span>Code checks · the agent suggests · you decide.</span>
      </footer>
    </div>
  )
}
