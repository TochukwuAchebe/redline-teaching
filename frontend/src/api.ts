import {
  ApiError,
  csvFilename,
  downloadBlob,
  notifyUnauthorized,
  type Api,
  type DeckState,
  type DecisionPatch,
  type HistoryItem,
} from './client'
import { mockApi } from './mock'

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) return body.detail.map((item: {msg?: string}) => (item.msg ?? 'Check this value').replace(/^Value error, /, '')).join('. ')
  } catch {
    /* non-JSON error body */
  }
  return `Request failed (${res.status})`
}

async function request(path: string, init: RequestInit = {}, handle401 = true): Promise<Response> {
  let res: Response
  try {
    res = await fetch(path, { credentials: 'same-origin', ...init })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.')
  }
  if (res.status === 401) {
    if (handle401) notifyUnauthorized()
    throw new ApiError(401, 'Not signed in')
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res))
  return res
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

const realApi: Api = {
  async checkSession() {
    try {
      const res = await fetch('/api/session', { credentials: 'same-origin' })
      return res.ok
    } catch {
      return true // server unreachable; let the next call surface the error
    }
  },

  async login(password) {
    await request('/api/login', { method: 'POST', ...json({ password }) }, false)
  },

  async logout() {
    await request('/api/logout', { method: 'POST' }, false)
  },

  uploadDeck(file, onProgress, signal, guidelineId = 'acme') {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      // If the server already has the file when we abort, this token lets us stop the job it started.
      const token = crypto.randomUUID()
      const abort = () => {
        xhr.abort()
        request(`/api/uploads/${token}/cancel`, { method: 'POST' }).catch(() => undefined)
      }
      signal?.addEventListener('abort', abort, { once: true })
      xhr.onloadend = () => signal?.removeEventListener('abort', abort)
      xhr.onabort = () => {
        const err = new ApiError(0, 'Upload cancelled')
        err.aborted = true
        reject(err)
      }
      xhr.open('POST', '/api/decks')
      xhr.withCredentials = true
      xhr.responseType = 'json'
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress?.(e.loaded / e.total)
      }
      xhr.onload = () => {
        const body = xhr.response
        if (xhr.status === 401) {
          notifyUnauthorized()
          reject(new ApiError(401, 'Not signed in'))
        } else if (xhr.status >= 200 && xhr.status < 300 && body?.deck_id) {
          resolve({ deck_id: String(body.deck_id) })
        } else {
          const detail = typeof body?.detail === 'string' ? body.detail : `Upload failed (${xhr.status})`
          reject(new ApiError(xhr.status, detail))
        }
      }
      xhr.onerror = () => reject(new ApiError(0, 'Upload failed. Check your connection and try again.'))
      const form = new FormData()
      form.append('guideline_id', guidelineId)
      form.append('upload_token', token)
      form.append('file', file)
      if (signal?.aborted) return abort()
      xhr.send(form)
    })
  },

  async cancelDeck(id) {
    const response = await request(`/api/decks/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
    return Boolean((await response.json()).cancelled)
  },

  async listDecks() {
    const res = await request('/api/decks')
    return ((await res.json()) as { decks: HistoryItem[] }).decks
  },

  async listDecksPage(query, offset) {
    const res = await request(`/api/decks?limit=25&offset=${offset}&query=${encodeURIComponent(query)}`)
    return res.json()
  },

  async recheckDeck(id) {
    const res = await request(`/api/decks/${encodeURIComponent(id)}/recheck`, { method: 'POST' })
    return (await res.json()) as { deck_id: string }
  },

  async getDeck(id) {
    const res = await request(`/api/decks/${encodeURIComponent(id)}`)
    const deck = (await res.json()) as DeckState
    return { ...deck, decisions: deck.decisions ?? {} }
  },

  async putDecisions(id, patch: DecisionPatch) {
    await request(`/api/decks/${encodeURIComponent(id)}/decisions`, { method: 'PUT', ...json(patch) })
  },

  async applyFixes(id) {
    await request(`/api/decks/${encodeURIComponent(id)}/fixes`, { method: 'POST' })
  },

  fixedSlideUrl(id, n, version) {
    return `/api/decks/${encodeURIComponent(id)}/fixed-slides/${n}.png?v=${version}`
  },

  correctedDeckUrl(id) {
    return `/api/decks/${encodeURIComponent(id)}/fixed.pptx`
  },

  slideUrl(id, n) {
    return `/api/decks/${encodeURIComponent(id)}/slides/${n}.png`
  },

  async listUpgrades(id) {
    return (await request(`/api/decks/${encodeURIComponent(id)}/upgrades`)).json()
  },

  async generateUpgrade(id, slide) {
    return (await request(`/api/decks/${encodeURIComponent(id)}/upgrades/${slide}`, { method: 'POST' })).json()
  },

  async chooseUpgrade(id, slide, choice) {
    return (await request(`/api/decks/${encodeURIComponent(id)}/upgrades/${slide}`, { method: 'PUT', ...json({ choice }) })).json()
  },

  upgradePreviewUrl(id, slide, appendix = false) {
    return `/api/decks/${encodeURIComponent(id)}/upgrades/${slide}${appendix ? '-appendix' : ''}.png`
  },

  async exportCsv(id, filename) {
    const res = await request(`/api/decks/${encodeURIComponent(id)}/export.csv`)
    const disposition = res.headers.get('content-disposition') ?? ''
    const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
    downloadBlob(await res.blob(), match ? decodeURIComponent(match[1]) : csvFilename(filename))
  },
}

export const api: Api = import.meta.env.MODE === 'mock' ? mockApi : realApi

export async function guidelineRequest(path: string, body?: unknown) {
  const response = await request(path, body === undefined ? {} : { method: 'POST', ...json(body) }); return response.json()
}
