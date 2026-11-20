# Decisions

How ambiguities in the brand guideline were resolved. Real style guides contradict themselves; the example guideline here keeps the same contradictions on purpose, so you can see how to handle them. All of these can be changed in `backend/app/config/rules.yaml`.

## One policy
**Every written requirement is checked. A requirement that is disputed is reported as a suggestion, with the reason in the finding.** A requirement is disputed when the guideline's own examples contradict it, or when ACME's annotated reviewer evidently doesn't apply it. The reviewer always decides.

## Order of precedence
1. **The guideline's written requirements** ("Requirements by PowerPoint Element") define what is checked.
2. **The deck's own slide master** supplies the exact values the guideline doesn't state, such as the title box position and bullet indents. The master is itself checked against the written requirements; a master that doesn't conform is reported once, and its slides are then checked against the guideline's values.
3. **The guideline's Appendix A examples** are illustrative. Where they contradict a written requirement, the requirement is still checked, as a suggestion.
4. **A reviewer-annotated deck** shows how the firm's reviewers actually apply the guideline. (The original project had one; it isn't public, so this repo ships synthetic demo decks instead.) It adds the consistency checks reviewers enforce but the guideline doesn't spell out, and marks written rules the reviewer doesn't apply as disputed.

## Guideline contradictions
| Topic | Requirements say | Appendix says | Evidence in the decks | Decision |
|---|---|---|---|---|
| Headline alignment | Vertically centred | Bottom-aligned | The master title is centred; every slide inherits it | Centred |
| Chart and table titles | Centred above | Left-aligned | ~160 captions centred, ~20 unset | Centred |
| Table header | Bold, white, fill #006EBE | "No shading" | 40 headers use #006EBE | #006EBE |
| Bullet depth | Levels 1–5 defined | "Two levels maximum" | The master defines five levels | Levels 1–5 allowed |
| Numeric alignment | Consistent within a column | Right-aligned | Mixed | Check consistency only |
| Yellow highlight | — | "Only for changed figures" vs "only for committee decision points" | — | Not checked: the appendix contradicts itself |

## Rules not enforced
- **Items marked "Informative, not enforced"** in the guideline (unit designators, stats rows above client rows). Exception: a missing revenue scale is raised as a low-severity suggestion, because the annotated reviewer flagged it.
- **"No element less than 1" from the left edge" is checked, as a suggestion.** It is a written requirement, so it is enforced. But the annotated reviewer never applies it to full-width tables and charts, so it is disputed: each finding states the edge it crosses and why it's advisory. A 0.05-inch tolerance keeps content aligned with the master's own headline edge from being flagged. On the annotated deck its findings fold into a single additional decision.

## Rules inferred from the annotated reviewer
The reviewer enforces consistency rules the guideline doesn't spell out. These are implemented as deck-level checks:
- "Key" vs "Legend" used consistently
- key items in the same order across slides
- the client named consistently
- column headers worded consistently
- a 0.03" first-column cell margin in data tables

## Reporting policy
Strict, per-element reporting. A valid finding is never capped, sampled or silently dropped to keep the count low. Noise is controlled in how findings are presented, not by hiding them.

A fixed cap on findings wouldn't hold for real decks. A client template's theme can use accent colours outside the guideline's palette, or set tables at 9 pt against the stated 10–12 pt. These are real conflicts between the template and the guideline, so they are reported. They are also grouped, so a reviewer settles each conflict once rather than element by element.

## Disputed examples become suggestions
Where the appendix contradicts a written requirement (headline vertical alignment; table header, zebra, statistics and client-row shading), or where the annotated reviewer doesn't apply a written rule (the 1-inch margin), the written rule is checked but the finding is a suggestion, and its message states the dispute. The reviewer decides.

## Noise policy
- Code findings are graded error or warning. AI findings are always suggestions, and are dropped unless they quote the slide text exactly.
- A repeated issue (the same rule and expected value on three or more slides) becomes a single grouped decision.
- Reviewer notes marked as uncertain ("not clear that was intentional") become suggestions, not errors.
- Colours are matched exactly. A near-miss such as `#006CBD` is reported as "near-miss of #006EBE", not silently accepted.
