import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(new URL('../src/highlightHits.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { highlightHits } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const f = (id, x, y, w, h) => ({ id, bbox: { x, y, w, h } })

test('coincident findings remain separate choices instead of picking the last box', () => {
  const items = [f('initials', 10, 20, 100, 50), f('typo', 10, 20, 100, 50)]
  assert.deepEqual(highlightHits(items, 60, 45, 0).map(f => f.id), ['initials', 'typo'])
  assert.deepEqual(items.map(f => f.id), ['initials', 'typo'])
})

test('nested hits prioritise the precise object and retain containing-object issues', () => {
  const items = [f('table', 0, 0, 500, 500), f('unrelated', 600, 0, 50, 50), f('cell', 100, 100, 50, 20)]
  assert.deepEqual(highlightHits(items, 110, 110, 0).map(f => f.id), ['cell', 'table'])
  assert.deepEqual(highlightHits(items, 400, 400, 0).map(f => f.id), ['table'])
})

test('outline padding is bounded and missing geometry is not selectable', () => {
  const items = [{ id: 'missing', bbox: null }, f('box', 10, 10, 20, 20)]
  assert.equal(highlightHits(items, 7, 20, 3).length, 1)
  assert.equal(highlightHits(items, 6, 20, 3).length, 0)
  assert.equal(highlightHits(items, 20, 34, 3).length, 0)
})
