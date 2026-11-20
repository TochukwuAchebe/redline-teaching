import { useEffect, useState } from 'react'
import { guidelineRequest } from '../api'

export interface GuidelineProfile {
  id: string; name: string; version: string; source: string
  unmapped_clauses: { line: number; text: string }[]
}
type Check = { kind: 'font' | 'font_size' | 'palette' | 'required_text'; scope: 'title' | 'body' | 'all'; value: string; clause: number; severity: 'warning' | 'suggestion' }
const blank = (): Check => ({ kind: 'font', scope: 'title', value: '', clause: 1, severity: 'warning' })

export function Guidelines({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [profiles, setProfiles] = useState<GuidelineProfile[]>([])
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [source, setSource] = useState('')
  const [checks, setChecks] = useState<Check[]>([blank()])
  // The acknowledgement covers a specific set of uncovered lines; it lapses only if that set changes.
  const [ackedFor, setAckedFor] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    guidelineRequest('/api/guidelines').then(r => setProfiles(r.guidelines)).catch(() => setError('Saved guidelines could not be loaded. Try refreshing before choosing a custom guideline.'))
  }, [])
  const lines = source.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  const gaps = lines.map((text, i) => ({ text, line: i + 1 })).filter(l => !checks.some(c => c.clause === l.line))
  const gapsKey = JSON.stringify(gaps) // A changed uncovered clause needs fresh acknowledgement.
  const ack = ackedFor === gapsKey
  const selected = profiles.find(p => p.id === value)
  function change(i: number, update: Partial<Check>) { setChecks(cs => cs.map((c, j) => j === i ? { ...c, ...update } : c)); setError('') }
  const blocker = !name.trim() ? 'Add a name for this guideline.'
    : !source.trim() ? 'Paste or upload the guideline text.'
    : checks.some(c => !c.value.trim()) ? 'Fill in the expected value for every check.'
    : gaps.length > 0 && !ack ? 'Tick the box above to confirm the lines that need manual review.'
    : ''
  async function save() {
    setBusy(true); setError('')
    try {
      const p: GuidelineProfile = await guidelineRequest('/api/guidelines', { name, source, checks, acknowledge_gaps: ack })
      setProfiles(ps => [p, ...ps]); onChange(p.id); setEditing(false)
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="guideline-picker" aria-label="Review guidelines">
    <label htmlFor="guideline-choice">Checking against</label>
    <select id="guideline-choice" value={value} onChange={e => onChange(e.target.value)}>
      <option value="acme">ACME brand guidelines</option>
      {profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.version.slice(0, 8)}</option>)}
    </select>
    {selected && <p className="meta">Custom checks only. {selected.unmapped_clauses.length} guideline {selected.unmapped_clauses.length === 1 ? 'line is' : 'lines are'} not automatically checked. The saved version stays with this review.</p>}
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(e => !e)}>{editing ? 'Close' : 'Add your own'}</button>
    {editing && <div className="guideline-editor">
      <h2>Create a guideline version</h2>
      <p>Paste your guidance or add a text file. Then choose the checks to run. Other instructions remain visible as coverage gaps. Coverage is counted per line, so put each instruction on its own line: a line with any check counts as covered. Saving creates a new version; existing reviews stay unchanged.</p>
      <label>Guideline name<input maxLength={100} value={name} onChange={e => { setName(e.target.value); setError('') }} /></label>
      <label>Guideline file (.txt or .md)<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={async e => {
        const f = e.target.files?.[0]; e.target.value = ''; if (!f) return
        if (!/\.(txt|md)$/i.test(f.name) || f.size > 120000) { setError('Choose a .txt or .md file up to 120 KB.'); return }
        try { const text = await f.text(); if (text.length > 30000) throw new Error('Guidelines must contain at most 30,000 characters.'); setSource(text); setError('') } catch (err) { setError((err as Error).message) }
      }} /></label>
      <label>Guideline text<textarea rows={7} maxLength={30000} value={source} onChange={e => { setSource(e.target.value); setError('') }} /></label>
      <details><summary>View numbered guideline lines ({lines.length})</summary><ol>{lines.map((l, i) => <li key={i}>{l}</li>)}</ol></details>
      <h3>Automatic checks</h3>
      <p className="meta">Font sizes use points; palettes use six-digit hex colours, separated by commas. Required wording is matched exactly on every slide. Images containing text are not checked.</p>
      {checks.map((c, i) => <fieldset key={i}><legend>Check {i + 1}</legend>
        <label>Guideline line<input type="number" min={1} max={Math.max(lines.length, 1)} value={c.clause} onChange={e => change(i, { clause: Number(e.target.value) })} /></label>
        <label>Check<select value={c.kind} onChange={e => change(i, { kind: e.target.value as Check['kind'], scope: e.target.value === 'required_text' ? 'all' : c.scope })}>
          <option value="font">Font</option><option value="font_size">Font size</option><option value="palette">Allowed colours</option><option value="required_text">Required wording</option>
        </select></label>
        <label>Apply to<select value={c.scope} disabled={c.kind === 'required_text'} onChange={e => change(i, { scope: e.target.value as Check['scope'] })}>
          <option value="title">Slide titles</option><option value="body">Body text</option><option value="all">All native slide content</option>
        </select></label>
        <label>Expected value<input maxLength={1000} value={c.value} onChange={e => change(i, { value: e.target.value })} /></label>
        <button type="button" className="btn-text" disabled={checks.length === 1} onClick={() => { setChecks(cs => cs.filter((_, j) => j !== i)); setError('') }}>Remove check {i + 1}</button>
      </fieldset>)}
      <button type="button" className="btn btn-secondary" disabled={checks.length >= 100} onClick={() => setChecks(cs => [...cs, blank()])}>Add a check</button>
      {gaps.length > 0 && <div><p>{gaps.length} {gaps.length === 1 ? 'line has' : 'lines have'} no automatic check:</p><ul>{gaps.map(l => <li key={l.line}>Line {l.line}: {l.text}</li>)}</ul>
        <label className="ack"><input type="checkbox" checked={ack} onChange={e => setAckedFor(e.target.checked ? gapsKey : null)} /> I understand these lines will need manual review.</label></div>}
      <p className="meta">These checks inspect native PowerPoint content. They do not prove that every instruction in the document is satisfied. Custom-guideline text is never sent to the agent.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="button" className="btn btn-primary" disabled={busy || !!blocker} onClick={save}>{busy ? 'Saving…' : 'Save and use this version'}</button>
      {blocker && !busy && <p className="meta" aria-live="polite">{blocker}</p>}
    </div>}
    {error && !editing && <p className="form-error" role="alert">{error}</p>}
  </section>
}
