import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(new URL('../src/brandScore.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { checkedScore, projectedScore, dismissedCount } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const f = (id, slide, severity = 'warning', auto = true) => ({ id, slide, severity, fix: auto ? { auto: true } : null })
const findings = [f('a', 1, 'error'), f('b', 2), f('c', 2, 'warning', false), f('d', 3, 'suggestion')]

test('open must-fix costs 25, should-fix 10 per slide; suggestions never count', () => {
  // slide 1: 75, slide 2: 80, slides 3-4: 100 -> 88.75
  assert.equal(checkedScore(findings, 4), 89)
  assert.equal(checkedScore([], 4), 100)
  const many = Array.from({ length: 6 }, (_, i) => f(`e${i}`, 1, 'error'))
  assert.equal(checkedScore(many, 1), 0) // never below zero
})

test('decisions never change the checked score', () => {
  assert.equal(checkedScore(findings, 4), 89)
  assert.equal(projectedScore(findings, 4, { a: 'accepted', b: 'rejected', c: 'rejected' }), 95) // only a queued fix projects
})

test('only accepted automatic fixes project; to-dos, manual fixes and dismissals do not', () => {
  assert.equal(projectedScore(findings, 4, { a: 'todo' }), 89)
  assert.equal(projectedScore(findings, 4, { a: 'accepted', b: 'accepted', c: 'accepted' }), 98) // c needs a person
  assert.equal(projectedScore(findings, 4, { a: 'rejected', b: 'rejected', c: 'rejected' }), 89)
  assert.equal(dismissedCount(findings, { a: 'rejected', d: 'rejected' }), 1) // suggestions aren't counted
})

test('after fixes are applied, only verified changes count, in both scores', () => {
  const fix = { state: 'done', report: { a: { status: 'applied', note: '', verified: false }, b: { status: 'applied', note: '', verified: true } } }
  assert.equal(checkedScore(findings, 4, fix), 91)
  assert.equal(projectedScore(findings, 4, { a: 'accepted', b: 'accepted' }, fix), 91)
})
