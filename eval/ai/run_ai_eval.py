"""Score the AI writing review against planted problems and clean controls.

Requires ANTHROPIC_API_KEY. Reports, per rule: planted problems found, false flags
on clean slides, and whether each suggested fix actually corrects the problem.
Usage: PYTHONPATH=backend python eval/ai/run_ai_eval.py [--deck PATH --expected PATH]
"""
import argparse
import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.checks import run_ai_checks  # noqa: E402
from app.model.extract import extract_deck  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default=str(ROOT / "eval/ai/seeded-writing.pptx"))
    ap.add_argument("--expected", default=str(ROOT / "eval/ai/seeded_expected.yaml"))
    ap.add_argument("--report", default=str(ROOT / "eval/ai/report.md"))
    args = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; the AI review cannot be evaluated.", file=sys.stderr)
        return 2
    key = yaml.safe_load(Path(args.expected).read_text())
    deck = extract_deck(args.deck)
    run: dict = {}
    findings = asyncio.run(run_ai_checks(deck, run))
    texts = {s.index: "\n".join(e.text for e in s.elements) for s in deck.slides}

    expected = {(e["slide"], e["rule"]): e for e in key["expected"]}
    clean = set(key["clean_slides"])
    found, false_flags, other = set(), [], []
    fix_ok, fix_checked = 0, 0
    rows = []
    for f in findings:
        k = (f.slide, f.rule_id)
        verdict = "planted ✓" if k in expected else ("FALSE FLAG (clean slide)" if f.slide in clean else "extra on seeded slide")
        if k in expected:
            found.add(k)
            want = expected[k].get("fix_contains")
            if want:
                fix_checked += 1
                good = want.lower() in (f.suggested_fix or "").lower()
                fix_ok += good
                verdict += " · fix correct" if good else " · FIX WRONG"
        elif f.slide in clean:
            false_flags.append(f)
        else:
            other.append(f)
        quoted = bool(f.quote) and f.quote in texts.get(f.slide, "")
        rows.append((f.slide, f.rule_id, f.confidence, f.quote, f.suggested_fix, verdict, quoted))

    per_rule = defaultdict(lambda: [0, 0])
    for (slide, rule) in expected:
        per_rule[rule][1] += 1
        per_rule[rule][0] += (slide, rule) in found
    flagged = len(findings)
    correct = len(found)
    lines = [
        "# AI writing review — seeded evaluation", "",
        f"Model: `{run.get('model')}` · slides reviewed: {run.get('reviewed', 0) + run.get('cached', 0)}/{run.get('slides')} · "
        f"discarded for not matching slide text: {run.get('filtered', 0)}", "",
        f"- Planted problems found: **{correct}/{len(expected)}**",
        f"- False flags on {len(clean)} clean slides: **{len(false_flags)}**",
        f"- Suggestions that hit a planted problem: **{correct}/{flagged}**" + (f" ({correct / flagged:.0%})" if flagged else ""),
        f"- Suggested fixes that correct the planted typo or casing: **{fix_ok}/{fix_checked}**", "",
        "| Rule | Found |", "|---|---|",
        *[f"| {r} | {a}/{b} |" for r, (a, b) in sorted(per_rule.items())], "",
        "| Slide | Rule | Conf. | Quote (verified on slide) | Suggested fix | Verdict |", "|---|---|---|---|---|---|",
        *[f"| {s} | {r} | {c:.2f} | {'✓' if q_ok else '✗'} “{(q or '')[:60]}” | {(fx or '')[:70]} | {v} |" for s, r, c, q, fx, v, q_ok in sorted(rows)],
    ]
    missed = sorted(set(expected) - found)
    if missed:
        lines += ["", "Missed: " + ", ".join(f"slide {s} {r}" for s, r in missed)]
    Path(args.report).write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:9]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
