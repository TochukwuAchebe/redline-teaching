import type { Decision, DecisionPatch } from './client'

type Decisions = Record<string, Decision>
export type SaveOutcome = { saved: true; previous: DecisionPatch } | { saved: false; error: unknown }
type Pending = { patch: DecisionPatch; resolve: (outcome: SaveOutcome) => void }
/** Retry a failed save after each delay (ms) while `retryable(error)` holds; no retries by default. */
export type RetryPolicy = { delays: number[]; retryable: (error: unknown) => boolean }
const NO_RETRY: RetryPolicy = { delays: [], retryable: () => false }

function merge(base: Decisions, patch: DecisionPatch): Decisions {
  const next = { ...base }
  for (const [id, value] of Object.entries(patch)) {
    if (value === null) delete next[id]
    else next[id] = value
  }
  return next
}

/** Preserve user order; rollback recomputes from confirmed state plus newer intent. */
export class DecisionQueue {
  private confirmed: Decisions
  private pending: Pending[] = []
  private running = false

  constructor(initial: Decisions, private send: (patch: DecisionPatch) => Promise<void>,
    private changed: (decisions: Decisions, saving: boolean) => void, private retry: RetryPolicy = NO_RETRY) {
    this.confirmed = { ...initial }
  }

  get busy() { return this.pending.length > 0 }

  enqueue(patch: DecisionPatch): Promise<SaveOutcome> {
    if (this.pending.length >= 200) return Promise.resolve({ saved: false,
      error: new Error('Decisions are still saving. Please wait before making more changes.') })
    return new Promise((resolve) => {
      this.pending.push({ patch: { ...patch }, resolve })
      this.publish()
      void this.drain()
    })
  }

  private publish() {
    const visible = this.pending.reduce((state, item) => merge(state, item.patch), this.confirmed)
    this.changed({ ...visible }, this.busy)
  }

  private async drain() {
    if (this.running) return
    this.running = true
    try {
      while (this.pending.length) {
        const item = this.pending[0]
        const previous: DecisionPatch = {}
        for (const id of Object.keys(item.patch)) previous[id] = this.confirmed[id] ?? null
        let outcome: SaveOutcome
        for (let attempt = 0; ; attempt++) {
          try {
            await this.send({ ...item.patch })
            this.confirmed = merge(this.confirmed, item.patch)
            outcome = { saved: true, previous }
            break
          } catch (error) {
            // A dropped connection usually comes back: keep the decision pending and try again.
            if (attempt < this.retry.delays.length && this.retry.retryable(error)) {
              await new Promise((resolve) => setTimeout(resolve, this.retry.delays[attempt]))
              continue
            }
            outcome = { saved: false, error }
            break
          }
        }
        this.pending.shift()
        this.publish()
        item.resolve(outcome)
      }
    } finally {
      this.running = false
    }
  }
}
