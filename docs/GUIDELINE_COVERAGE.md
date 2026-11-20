# Guideline coverage

Every requirement in the ACME brand guideline (`backend/app/config/acme-guidelines.md`, a fictional example), and how Redline handles it.

- **Checked:** an automatic rule flags it.
- **Partly:** an automatic rule covers part of it; the rest is listed for the reviewer.
- **By eye:** it can't be checked reliably from the PowerPoint file. It is listed under "What to check by eye" on the review screen, so the reviewer knows it's theirs.

Rules marked *(AI)* use the Claude API; all others are deterministic code. *Suggestion* means the rule is disputed (see [DECISIONS.md](../DECISIONS.md)) and reported as advisory.

## Title, section and content slides

| Requirement | Status | Rule |
|---|---|---|
| Title slide: Calibri 24 pt | Checked | `TITLESLIDE_FONT` |
| Title slide: valid date; no "DRAFT" or other designator | Checked | `TITLESLIDE_DATE` (written, day-first and month-year dates), `TITLESLIDE_DRAFT` (DRAFT, Preliminary, WIP, "for discussion only", version stamps such as v0.3; DRAFT labels are fixable, other designators go on the to-do list) |
| Title slide: authors identified | Checked | `TITLESLIDE_AUTHORS` |
| Section header: Calibri 24 pt | Checked | `TITLE_FONT` (bold not required, as written) |
| Clear separation between title and body | Partly | `TITLE_BODY_OVERLAP` flags body content that starts inside the headline text; `MARGIN_EDGE` checks the headline's edges; white space and density are by eye |
| Headline: Calibri Bold 24 pt | Checked | `TITLE_FONT` |
| Headline: sentence case | Checked | `TITLE_CASE` *(AI)* |
| Headline: no ending punctuation | Checked | `TITLE_PUNCTUATION` (fixable) |
| Headline: vertically centred | Checked | `TITLE_VERTICAL_ALIGNMENT` *(suggestion: the appendix shows bottom-aligned)* |
| Headline: size and position as the master | Checked | `TITLE_POSITION` (fixable) |
| Headline: states the takeaway | Checked | `TITLE_TAKEAWAY` *(AI)*, with a code backstop `TITLE_TOPIC_ONLY` for short verbless labels such as "Plan Design" |
| Headline: warn above 3 lines | Checked | `TITLE_TOO_LONG` |

## Confidentiality statement

| Requirement | Status | Rule |
|---|---|---|
| Calibri 8 pt | Checked | `CONF_STYLE` (fixable) |
| Position as the master | Checked | `CONF_MOVED` (fixable) |
| Legal language not modified | Checked | `CONF_TEXT_MODIFIED` (fixable) |
| On every slide, including title and section slides | Checked | `CONF_MISSING` (fixable) |
| *(Added from the annotated review)* Not overlapped by other content | Checked | `CONF_OVERLAP` |

## Content placement and bulleted lists

| Requirement | Status | Rule |
|---|---|---|
| Content within the headline's left and right edges; at least 1" from the left edge | Checked | `MARGIN_EDGE` *(suggestion: the annotated reviewer doesn't apply it to full-width tables)* |
| Calibri 16 pt, primary and sub-bullets | Checked | `BULLET_FONT` (fixable) |
| Bullet symbols by level (square, dash, arrowhead, circle) | Checked | `BULLET_GLYPH` (glyphs are matched with their font; a to-do, not an automatic fix) |
| Left-aligned; consistent indentation; level 2 at 0.4", levels 3–5 at 0.6" | Checked | `BULLET_INDENT` (fixable; exact indents come from the deck's master; centred or justified bullets are flagged) |
| Single line spacing; 6 pt between level-1 bullets, 0 pt below | Checked | `BULLET_SPACING` (fixable; exact point leading counts as single only when it matches the font's line height) |
| No ending punctuation | Checked | `BULLET_PUNCTUATION` (fixable) |
| Parallel grammatical structure | Checked | `BULLET_PARALLEL` *(AI; manual review of the whole list, no rewrite)* |

## Tables

| Requirement | Status | Rule |
|---|---|---|
| Calibri 10–12 pt, consistent across the table | Checked | `TABLE_FONT` (range and one size throughout), `TABLE_HEADER_SIZE_INCONSISTENT` |
| No left or right border | Checked | `TABLE_BORDERS` (fixable) |
| Subtle gridlines, clarity over density, no unnecessary shading | By eye | |
| Consistent decimal precision within a table | Checked | `TABLE_NUMBER_FORMAT` (within each column; percentages across the whole table) |
| Percentages with %, currency with a symbol | Partly | `TABLE_NUMBER_FORMAT` flags columns that mix them; a column consistently without symbols isn't flagged |
| En dashes or blanks for missing values | Checked | `TABLE_MISSING_VALUE` ("N/A", "n/a", "--" and similar placeholders; fixable) |
| "%ile", not "Percentile" | Checked | `TERM_PERCENTILE` (fixable) |
| Table title: Calibri Bold 14 pt, centred above | Checked | `TABLE_CAPTION` (fixable) |
| Table title: 0 pt line spacing | Not checked | Low impact; not measured |
| Header: bold, bottom-aligned, white text, #006EBE fill | Checked | `TABLE_HEADER_STYLE` *(suggestion: the appendix discourages shaded headers)*, fixable |
| Header: thin black lines top and bottom | By eye | |
| Header: "Executive", not "Incumbent" | Checked | `TERM_INCUMBENT` (fixable) |
| Header: "Title" or "Position" for job titles | Not checked | The job-title column can't be identified reliably |
| Units designator, e.g. ($000s) *(informative)* | Checked | `TABLE_UNITS_MISSING` *(suggestion; the annotated reviewer flags it)* |
| Body: zebra striping from the second row (#F2F2F2) | Checked | `TABLE_ZEBRA` *(suggestion: appendix conflict)*; a to-do, not an automatic fix |
| Body: consistent alignment within each column | By eye | Prototyped and held back: on the samples it flagged summary rows that are likely centred on purpose |
| Statistics rows: #FFFFDB | Checked | `TABLE_STATS_ROW` *(suggestion: appendix conflict)*, fixable |
| Client rows: #F1FFE1 | Checked | `TABLE_CLIENT_ROW` *(suggestion: appendix conflict)*, fixable |
| Statistics and client rows: thin black lines top and bottom | By eye | |
| Stats rows above client rows *(informative)* | Not enforced | As the guideline states |

## Charts, shapes, footnotes and emphasis

| Requirement | Status | Rule |
|---|---|---|
| Chart title: Calibri Bold 14 pt, centred above | By eye | Chart titles aren't reliably distinguishable from other text near a picture |
| Chart is a picture, not PowerPoint elements; axis labels and legends clear | By eye | |
| Chart colours from the palette | Partly | `COLOR_OFF_PALETTE` checks native shapes, text, gradient stops and the series colours a native chart sets; colours inside pictures can't be read |
| Tables not pasted as pictures | By eye | |
| Shapes: Calibri; colours from the palette | Partly | `SHAPE_FONT` checks the typeface; colours are checked by `COLOR_OFF_PALETTE`; size consistency across a shape collection is by eye |
| Footnotes: Calibri italic 10 pt | Checked | `FOOTNOTE_STYLE` (fixable) |
| Every footnote marker has a matching footnote | Checked | `FOOTNOTE_UNMATCHED` |
| Chart source notes: Calibri italic 9 pt | Checked | `SOURCE_NOTE_STYLE` (fixable) |
| Chart source notes directly below the visual | Not checked | |
| Red callout content referenced in the headline | By eye | |

## Writing, terminology and colour

| Requirement | Status | Rule |
|---|---|---|
| Declarative, insight-driven headlines | Checked | `TITLE_TAKEAWAY` *(AI)* |
| Board-ready tone, minimal jargon, implications over narration | By eye | AI suggestions help but don't prove compliance |
| Consistent terminology across slides | Checked | `CLIENT_NAME_INCONSISTENT`, `HEADER_TERM_INCONSISTENT` (configurable synonym groups: Company / Company Name, Base Salary / Base Pay, Target Bonus / Target STI, Long-Term Incentive / LTI, Total Direct Compensation / TDC), `KEY_LABEL_INCONSISTENT`, and `TERM_INCONSISTENT` for slide text (one name for the annual incentive plan: AIP, STIP, STI or annual bonus plan, configurable) |
| Spell out "Target" (no "TGT") | Checked | `TERM_TGT` (fixable) |
| Colours from the ACME palette | Checked | `COLOR_OFF_PALETTE` (exact match; near-misses named) |

## Checks added from the annotated review

The annotated deck shows ACME's reviewer enforcing rules the guideline doesn't spell out. These became checks:
- key and legend consistency: `KEY_LABEL_INCONSISTENT`, `KEY_ORDER_INCONSISTENT`, `KEY_SPACING`, `KEY_BOX_INCONSISTENT`, `KEY_MISSING_LABEL`;
- a 0.03" first-column cell margin: `TABLE_CELL_MARGIN`;
- merged explanation cells: `TABLE_MERGED_CELL`;
- leftover reviewer comments: `LEFTOVER_COMMENT`;
- initials punctuation: `INITIALS_PUNCTUATION`;
- spelling: `TYPO` *(AI)*;
- a deck not built on the ACME template: `TEMPLATE_NONCONFORMING`.

## Summary

The tables above list the guideline's requirements one per row, 59 rows in all. **42 are checked automatically**: 38 by deterministic code and 4 by the agent's advisory review (sentence case, takeaway headlines with a partial code backstop, declarative headlines, parallel bullets). 4 are checked partly, 9 are left to the reviewer by eye, 3 aren't checked, and 1 is informative and not enforced, as the guideline says. Every by-eye item is named on the review screen. The automated checks cover what can be measured reliably from the PowerPoint file. The gaps are visual judgements (gridlines, density, chart design), content inside pictures, and a few details that can't yet be identified without raising doubtful flags.
