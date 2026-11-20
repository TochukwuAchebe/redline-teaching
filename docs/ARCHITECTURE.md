# Architecture

How Redline turns an uploaded PPTX into reviewable findings, a corrected deck and a durable record.

```
Browser ──HTTPS──► FastAPI (one container)
                     │  password session, request limits, static React app
                     │
                     ├─► PostgreSQL: reviews, decisions, guideline versions, job queue
                     ├─► Azure Blob Storage: originals, slide previews, corrected decks
                     │
                     └─► Queue worker (in-process, leased jobs)
                           ├─ render   LibreOffice → PDF → PyMuPDF PNG per slide
                           ├─ analyse  extract → classify → rules → consistency → AI → noise policy
                           └─ fix      apply accepted fixes to a copy → re-render → re-check
```

## 1. Understanding the deck: `backend/app/model`

**Extraction (`extract.py`, `resolve.py`).** python-pptx gives the shape tree; lxml reads what it doesn't expose. For every run of text Redline resolves the *effective* font, size, weight, colour, bullet glyph, indent and spacing by walking run → paragraph → shape → layout placeholder → master placeholder → master text styles → theme. Each value keeps its source, so a finding can say *where* the wrong value comes from. Theme colours are resolved through the colour map and transforms. Bullet glyphs are read with their font, because `§` in Wingdings is a square, not a section sign.

**Roles (`roles.py`).** Each element is classified: title, body, table, table caption, chart, picture, footnote, source note, key/legend, confidentiality footer, slide number. This uses placeholder types first, then position, text patterns and proximity (a "Key:" text box plus adjacent swatches is a key).

**The master as spec.** The canonical title box, bullet levels and confidentiality text are read from the uploaded deck's own master. If the master itself doesn't conform to the guideline (wrong title font or size), one `TEMPLATE_NONCONFORMING` finding is raised and slides are checked against the guideline values instead.

## 2. Finding issues: `backend/app/rules`, `backend/app/ai`

**Deterministic rules (`rules/checks.py`, `rules.yaml`).** About 50 rules, each configured in `backend/app/config/rules.yaml` (enabled, severity, parameters). Colours are matched exactly against `palette.yaml`; a near-miss is named ("near-miss of #006EBE"). Headline line counts use font metrics.

**Deck consistency (`rules/consistency.py`).** A profile of the whole deck (key label and order, client naming, header wording, key styling) is built first, then majority-supported label/style differences are flagged. Key order uses a disclosed earlier comparable reference, rather than declaring either order brand-correct. This catches what the annotated reviewer enforces but the guideline doesn't spell out.

**AI review (`ai/checks.py`, `ai/prompts.py`).** One Claude API call (Anthropic, `claude-sonnet-5-5`) per slide with text, run concurrently with bounded retries and timeouts, at most 100 slides per deck by default. The model returns findings through a tool schema. Each finding is accepted only if:
- its quote appears verbatim in the slide text sent;
- typo/headline wording suggestions offer a drop-in replacement; parallelism findings quote a whole affected bullet and request manual list review, without replacement wording;
- its confidence is at least 0.7.

The model also gets deck context (appendix, "continued" slides), so it doesn't call a partial list incomplete. AI findings are always suggestions. Speaker notes are never sent.

**Interpretation (`acme_policy.py`).** Written requirements govern supported checks; clauses that are not automated remain explicitly listed for manual review. Where the guideline's own example contradicts it (headline alignment, table shading), or ACME's annotated reviewer doesn't apply it (the 1-inch margin), the finding is a suggestion and its message states the dispute.

**Noise policy (`noise.py`).** Every occurrence is kept as its own finding with its own box. Findings with the same rule and expected value are grouped (`group_id`), so the UI shows one decision for a repeated issue. Findings are ranked error → warning → suggestion.

## 3. Fixing: `backend/app/fix`

Accepted findings are snapshotted when a correction starts. Each fixer edits a copy of the PPTX at the XML level: fonts, sizes, positions, bullet properties, table fills and margins, terminology, the confidentiality text, removing "DRAFT", and AI typo and capitalisation replacements. The copy is then re-rendered and re-analysed:
- a deterministic fix counts as **Fixed** only if the issue is gone;
- AI text replacements are applied as editorial changes for the reviewer to inspect;
- anything without a safe automatic fix (list rewrites, takeaway headlines, palette changes that would alter the shared theme) is returned as **needs a hand edit**, with a reason.

Accepted design suggestions (`redesign.py`) are applied after the fixes, on the same copy, before the re-check. The agent only chose; code wrote every word when the suggestion was made: the headline is the slide's own, one of its sentences, or a comparison computed from the table; bullets are the slide's clauses verbatim; panel titles come from its headings or a fixed neutral list. The redesign also had no more guideline issues than the original, and fewer whenever the original had any. Code draws the slide in fixed ACME layouts: a chart of the table's own figures in palette colours with the key as a legend beneath it, and ACME-blue panel headers over 16 pt bullets. The native table, with its key, moves to a new appendix slide; a text redesign is never applied where it would remove a table, picture or chart. A suggestion that can't be applied safely is reported and the slide left unchanged.

## 4. Durable state and processing: `state.py`, `worker.py`, `objects.py`

- **Metadata** (status, results, decisions, fix reports, guideline versions) is stored in PostgreSQL in production and SQLite locally, behind one `State` interface.
- **Files** (original deck, slide PNGs, corrected deck and previews) go to a private Blob container, accessed through the app's managed identity. Locally they go to disk.
- **Queue.** An upload is persisted, then enqueued. A worker claims the oldest job with `FOR UPDATE SKIP LOCKED` and a 60-second lease, renewed every 2 seconds. Every write (progress, result, fix report) is fenced on the worker's ownership, so a worker that lost its lease can't overwrite newer state. A job abandoned by a crashed worker is reclaimed when its lease expires; after five attempts it is marked failed with a visible error.
- **Cancellation** is recorded in the database and observed by the worker within seconds; a cancelled job never publishes results.
- **Uploads** are idempotent through a client token, so a retried upload doesn't create a duplicate review.
- **Decisions** are saved one at a time, in the order the reviewer made them. If a save fails, the UI rebuilds from saved state plus newer decisions, so an older failure can't undo a newer choice.

Because all state is external, any fresh container serves every past review.

## 5. Custom guidelines: `guidelines.py`

A guideline version is the source text plus the checks the author defined for it: fonts and sizes per element role, allowed colours, and required slide wording. Validation rejects conflicting or invalid values. Source lines not covered by a check are listed as "needs your review" and must be acknowledged before saving. A saved version is immutable and is pinned to each review that uses it, so a later recheck uses the same rules unless the reviewer picks a newer version. Custom guidelines run without AI and never inherit ACME rules.

## 6. Limits and protection

| Limit | Value |
|---|---|
| Upload size | 50 MB, enforced while streaming, before parsing |
| Expanded archive | 128 MB, at most 20,000 parts |
| Slides per review | 500 |
| Render time | 240 seconds per deck |
| AI-reviewed slides | 100 per deck (configurable) |
| Concurrent uploads per API process | 2 |

Every API route requires the password session (HttpOnly, Secure, SameSite cookie). Review and guideline IDs are validated before any path or object key is built. CSV exports escape spreadsheet formulas.

## 7. Front end: `frontend/`

React and TypeScript, built with Vite and served by FastAPI. Every signed-in screen shows the same four steps: **Upload → Review → Apply → Download**.

- **Welcome:** what Redline does and how it decides (code checks, the Redline agent suggests, the reviewer decides), with sign-in.
- **Upload and recent reviews:** one drop zone, then live progress with slide previews.
- **Review:** a slide rail with open counts and a slide with numbered boxes. Cards use plain severity (*Must fix*, *Should fix*, *Suggestion*) and the change as a tracked edit. **Accept** and **Reject** state their consequence, and a decision shows on the slide at once with Undo. On slides with a chartable table or dense text, a **Redesign** tab offers the optional agent suggestion.
- **Apply:** a confirmation listing every change.
- **What changed:** before and after side by side, each change outlined and listed.
- **Deck ready:** downloads, the reviewer's to-do list, and what to check by eye.

The brand score (`src/brandScore.ts`) is the average slide score: each slide starts at 100, and each open must-fix costs 25 points and each should-fix 10.

Design tokens are in `src/styles/tokens.css`: a warm white canvas, black ink and hairlines. Colour carries meaning only: red, amber and blue for severity, green once resolved, and every state also carries its word.
