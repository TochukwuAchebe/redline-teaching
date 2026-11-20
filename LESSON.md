# Build the core of Redline in an hour

This is the lesson version of Redline. In one hour you build the core: a script that reads a PowerPoint deck, works out what each piece of text really looks like, checks it against a house style, asks an AI model only for the judgement calls, and groups the results so a person can decide quickly.

The finished lesson script is [`lesson/core.py`](lesson/core.py), about 270 lines. The full product in `backend/` and `frontend/` grew from the same five steps over about three days of iteration; each step below says where its full version lives.

**You need:** Python 3.12 or later and `pip install python-pptx lxml anthropic`. An Anthropic API key is optional, for step 4 only.

**Test decks:** [`samples/demo-clean.pptx`](samples/demo-clean.pptx) follows the house style. [`samples/demo-with-issues.pptx`](samples/demo-with-issues.pptx) has 14 planted issues, listed in [`samples/demo-answer-key.yaml`](samples/demo-answer-key.yaml). Both are generated from scratch by `scripts/make_sample_decks.py`, so you can read exactly how each issue was planted.

| Time | Step | You end with |
|---|---|---|
| 0–10 min | 1. Read the deck | Every run of text, with its slide, shape and role |
| 10–25 min | 2. Resolve inheritance | The real size and weight of every run, and where each came from |
| 25–40 min | 3. Code checks | Four rules that find the measurable issues |
| 40–50 min | 4. AI review, verified | Judgement suggestions you can trust to be grounded |
| 50–60 min | 5. Group and decide | One decision per issue, not one per occurrence |

---

## Before you start: decide what the tool is for

Write down three things before any code. They shape every later choice.

1. **Who uses it and what do they decide?** A reviewer checks a deck before it goes to a client. They need to accept or reject each flag quickly.
2. **What does a wrong answer cost?** A false flag wastes the reviewer's time and teaches them to ignore the tool. So precision matters more than flagging everything.
3. **What can be measured, and what needs judgement?** "Bullets are 16 pt" is measurable. "The headline states the takeaway" is judgement. Measurable things go to code; judgement goes to the model.

That last split is the whole design in one sentence: **code checks, AI suggests, people decide.**

## Step 1: read the deck (0–10 min)

A `.pptx` is a zip of XML files. [python-pptx](https://python-pptx.readthedocs.io) gives you slides, shapes, paragraphs and runs.

```python
from pptx import Presentation

prs = Presentation("samples/demo-with-issues.pptx")
for number, slide in enumerate(prs.slides, start=1):
    for shape in slide.shapes:
        if shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    print(number, shape.name, paragraph.level, run.text, run.font.size)
```

Run it and look at the last column. **Almost every size is `None`.** That's the first lesson: the text doesn't carry its own formatting. If you check `run.font.size` against "16 pt", you'll either flag everything or nothing.

In `core.py` this is `read_deck()`, which also records each shape's **role** (title, body, other) from its placeholder type. Full version: `backend/app/model/extract.py` and `roles.py`, which also classify tables, footnotes, keys and the confidentiality footer.

## Step 2: resolve inheritance (10–25 min)

When a run has no size, PowerPoint looks it up the chain:

```
run → shape's list style → layout placeholder → master placeholder → master text styles → theme
```

Write a function that walks that chain for one run and returns the first value it finds, **plus where it found it**:

```python
size, bold, source = resolve(slide, shape, paragraph, run)
# (24.0, True, "master text styles") for a headline that sets nothing itself
# (12.0, None, "run") for a bullet someone resized by hand
```

The source matters as much as the value. "12 pt, set on the run" tells the reviewer someone overrode the template; "12 pt, from the master" means the template itself is wrong.

**The master is the spec.** The deck's own master already records the house style: title font and size, bullet glyphs and indents per level, and the exact confidentiality wording in the footer. Read the rules' expected values from it, and the tool works on decks it has never seen. In `core.py`, the confidentiality statement is read from the master's footer, not typed into the code.

Full version: `backend/app/model/resolve.py` also resolves fonts, colours (through the theme's colour map), bullet glyphs (a `§` in Wingdings is a square), indents and spacing.

## Step 3: check what can be measured, in code (25–40 min)

Now rules are short, because they compare resolved values:

```python
if r.role == "body" and r.size_pt not in (None, 16):
    out.append(Finding("BULLET_FONT", r.slide, r.shape, "Bullets should be 16 pt", "16 pt", f"{r.size_pt} pt"))
```

`core.py` has four: headline size and weight, bullet size, the confidentiality statement on every slide, and two terminology rules ("Target" not "TGT", "%ile" not "Percentile"). Run it:

```bash
python lesson/core.py samples/demo-with-issues.pptx
python lesson/core.py samples/demo-clean.pptx      # should print 0 findings
```

**Always test on a clean deck too.** A rule that fires on the clean deck is a false flag, and false flags are what make people stop using a tool.

Full version: about 50 rules in `backend/app/rules/checks.py` and `consistency.py`, each switched on or off and tuned in `backend/app/config/rules.yaml` without touching code. Deck-level rules compare slides with each other: "Key" on four slides and "Legend" on one is flagged even though neither word is wrong on its own.

## Step 4: ask the model only for judgement, and verify it (40–50 min)

Some rules need a reader: does the headline state a conclusion? Are the bullets parallel? Send each slide's text to the model with a short prompt that names only those rules, and ask for structured output through a tool.

Then **don't trust the answer; check it in code:**

```python
if item["quote"] in slide_text and item["confidence"] >= 0.7:
    keep(item)        # otherwise drop it
```

- The model must **quote the slide**. If the quote isn't really there, the finding is invented, so it's dropped.
- It must clear a **confidence bar**.
- AI findings are always **suggestions**, never errors.
- Slide text is **data, not instructions**: the prompt says so, so a slide that says "ignore your rules" can't steer it.

```bash
ANTHROPIC_API_KEY=... python lesson/core.py samples/demo-with-issues.pptx --ai
```

Slide 6's "Peer group overview" headline and its mixed bullets are the planted judgement issues. Model output varies between runs, so treat a single run as an observation, not a guarantee.

Full version: `backend/app/ai/prompts.py` (the production prompt, with a confidence scale and rules for appendix and continued slides) and `backend/app/ai/checks.py` (one call per slide in parallel, retries, caching by content, and the same verification).

## Step 5: group repeats into one decision (50–60 min)

If the same issue appears on 20 slides, the reviewer should decide once, not 20 times. Group by rule and expected value:

```python
groups[(f.rule, f.expected)].append(f)
```

Keep every occurrence, so each can still be highlighted on its slide, but present one decision. **Control noise in how you present findings, not by hiding them.**

Full version: `backend/app/noise.py`, and the review screen in `frontend/`: each slide with numbered boxes, a card per issue, Accept or Reject, and "accept all" for a group.

---

## From the core to the product

The hour gives you the engine. Everything else in this repository is what it took to make it something people can rely on:

| Added | Why | Where |
|---|---|---|
| Fixes applied to a copy, then the deck checked again | A fix only counts as fixed if the re-check no longer finds the issue; the original is never touched | `backend/app/fix/` |
| Rendering each slide to an image | So highlights sit on what the reviewer actually sees | `backend/app/render.py` (LibreOffice → PDF → PNG) |
| A review UI with accept, reject and undo | The person decides; the tool never changes anything on its own | `frontend/` |
| A durable job queue with leases | Long jobs survive restarts and crashed workers | `backend/app/worker.py`, `state.py` |
| Upload limits | Corrupt, password-protected, huge or zip-bomb files are refused with a clear message | `backend/app/limits.py` |
| Optional slide redesign | The model only chooses layout and which of the slide's own sentences to use; code writes every word | `backend/app/redesign.py` |
| Custom guidelines | Another team's style, as versioned checks, with no code change | `backend/app/guidelines.py` |
| An explicit policy for contradictions | Real style guides contradict their own examples; decide once and write it down | [`DECISIONS.md`](DECISIONS.md) |
| Tests and evaluations | Accuracy is measured, not asserted | `backend/tests/`, `eval/` |

## Exercises

1. **Add a rule.** Flag headlines that end in punctuation. Add a planted case to `scripts/make_sample_decks.py`, rebuild the decks, and check the clean deck still has 0 findings.
2. **Find the false flag.** Change the clean deck so a bullet is legitimately 14 pt inside a table. Does your bullet rule fire? How would you scope it?
3. **Break the AI on purpose.** Write a fake `ask` function that returns a finding whose quote isn't on the slide. Confirm `ai_review` drops it. (See `backend/tests/test_lesson.py`.)
4. **Read the master.** Make the bullet-size rule read 16 pt from the master's body style instead of hard-coding it. Then build a deck whose master says 18 pt and see what happens.
5. **Compare with the product.** Run both on the same deck and explain each difference:

   ```bash
   python lesson/core.py samples/demo-with-issues.pptx
   PYTHONPATH=backend python -c "from app.pipeline import analyze_sync; [print(f.slide, f.rule_id) for f in analyze_sync('samples/demo-with-issues.pptx', use_ai=False).findings]"
   ```
