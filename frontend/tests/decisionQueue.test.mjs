import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Compile the production pure TypeScript module with the project's existing compiler.
const source = await readFile(new URL('../src/decisionQueue.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ES2020 } })
const { DecisionQueue } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return { promise, resolve, reject } }

function fixture(initial = {}) {
  const calls=[], states=[], gates=[]
  const queue = new DecisionQueue(initial, patch => {
    calls.push(patch);const gate=deferred();gates.push(gate);return gate.promise
  }, (decisions,saving) => states.push({decisions,saving}))
  return {queue,calls,states,gates,last:()=>states.at(-1)}
}

test('late older failure cannot erase newer intent; successful writes preserve user order', async () => {
  const f=fixture()
  const first=f.queue.enqueue({x:'accepted'})
  const second=f.queue.enqueue({x:'rejected'})
  assert.deepEqual(f.calls,[{x:'accepted'}])
  assert.deepEqual(f.last(),{decisions:{x:'rejected'},saving:true})
  f.gates[0].reject(new Error('503'))
  assert.equal((await first).saved,false)
  assert.deepEqual(f.calls,[{x:'accepted'},{x:'rejected'}])
  assert.deepEqual(f.last(),{decisions:{x:'rejected'},saving:true})
  f.gates[1].resolve()
  assert.deepEqual(await second,{saved:true,previous:{x:null}})
  assert.deepEqual(f.last(),{decisions:{x:'rejected'},saving:false})
})

test('group failure rolls back only failed intent and retains newer overlap and unrelated decisions', async () => {
  const f=fixture({keep:'accepted',a:'rejected'})
  const group=f.queue.enqueue({a:'accepted',b:'accepted',c:'accepted'})
  const newer=f.queue.enqueue({b:'rejected',d:'accepted'})
  f.gates[0].reject(new Error('503'))
  await group
  assert.deepEqual(f.last().decisions,{keep:'accepted',a:'rejected',b:'rejected',d:'accepted'})
  f.gates[1].resolve()
  assert.deepEqual(await newer,{saved:true,previous:{b:null,d:null}})
  assert.equal(f.queue.busy,false)
})

test('failed newer decision restores confirmed earlier success rather than the initial page state', async () => {
  const f=fixture()
  const first=f.queue.enqueue({x:'accepted'})
  const second=f.queue.enqueue({x:'rejected'})
  f.gates[0].resolve();await first
  f.gates[1].reject(new Error('503'));await second
  assert.deepEqual(f.last(),{decisions:{x:'accepted'},saving:false})
})

test('undo patch has the real confirmed previous values, including clearing a decision', async () => {
  const f=fixture({x:'accepted',other:'rejected'})
  const first=f.queue.enqueue({x:null})
  f.gates[0].resolve()
  const result=await first
  assert.deepEqual(result,{saved:true,previous:{x:'accepted'}})
  const undo=f.queue.enqueue(result.previous)
  f.gates[1].resolve();await undo
  assert.deepEqual(f.last(),{decisions:{x:'accepted',other:'rejected'},saving:false})
})

test('caller mutations do not change a queued patch or initial state', async () => {
  const initial={x:'accepted'}, f=fixture(initial)
  initial.x='rejected'
  const patch={a:'accepted'}, first=f.queue.enqueue(patch)
  patch.a='rejected'
  f.gates[0].resolve();await first
  assert.deepEqual(f.last().decisions,{x:'accepted',a:'accepted'})
})

test('queue admission is bounded and rejected admission does not change visible decisions', async () => {
  const f=fixture(), jobs=[]
  for(let i=0;i<200;i++)jobs.push(f.queue.enqueue({['x'+i]:'accepted'}))
  const overflow=await f.queue.enqueue({overflow:'accepted'})
  assert.equal(overflow.saved,false)
  assert.equal(f.last().decisions.overflow,undefined)
  for(let i=0;i<200;i++){f.gates[i].resolve();await jobs[i]}
  assert.equal(f.calls.length,200)
  assert.equal(f.queue.busy,false)
})

test('a save that fails while offline is retried and kept, not rolled back', async () => {
  const sent=[], states=[]
  let failures=2
  const queue=new DecisionQueue({}, async patch => { sent.push(patch); if (failures-- > 0) throw new TypeError('Failed to fetch') },
    (decisions,saving) => states.push({decisions,saving}), { delays:[0,0,0], retryable:() => true })
  const result=await queue.enqueue({x:'accepted'})
  assert.equal(sent.length,3)
  assert.deepEqual(result,{saved:true,previous:{x:null}})
  assert.deepEqual(states.at(-1),{decisions:{x:'accepted'},saving:false})
})

test('a rejected request is not retried', async () => {
  let calls=0
  const queue=new DecisionQueue({}, async () => { calls++; throw Object.assign(new Error('Bad request'),{status:400}) },
    () => {}, { delays:[0,0], retryable:e => !(e.status >= 400 && e.status < 500) })
  assert.equal((await queue.enqueue({x:'accepted'})).saved,false)
  assert.equal(calls,1)
})
