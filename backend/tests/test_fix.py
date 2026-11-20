"""Accepting a flag must change the deck: apply fixes, then prove them by re-checking."""
from pathlib import Path

import pytest
from pptx import Presentation

from app.fix import apply_fixes
from app.model.extract import extract_deck

ROOT = Path(__file__).resolve().parents[2]


def _text_after_draft_fix(runs):
    from app.fix import _remove_draft
    prs = Presentation()
    shape = prs.slides.add_slide(prs.slide_layouts[6]).shapes.add_textbox(0, 0, 100, 100)
    para = shape.text_frame.paragraphs[0]
    for text in runs:
        para.add_run().text = text
    _remove_draft(None, None, shape._element, None)
    return shape.text_frame.text


@pytest.mark.parametrize("runs, expected", [
    # D-01: the word after the label is the title, not part of the designator.
    (["DRAFT ACME compensation committee review"], "ACME compensation committee review"),
    (["ACME DRAFT compensation review"], "ACME compensation review"),
    (["draft Board materials"], "Board materials"),
    # Version stamps still go with the label.
    (["May 11, 2026 –", " DRAFT 20260330"], "May 11, 2026"),
    (["ACME review – DRAFT"], "ACME review"),
    (["draft v2 ACME review"], "ACME review"),
    (["ACME review (Draft v0.3)"], "ACME review"),
    (["DRAFT – For discussion"], "For discussion"),
    (["DRAFT"], ""),
    # Words that merely contain "draft" are content.
    (["Draftkings Inc. review – DRAFT"], "Draftkings Inc. review"),
])
def test_draft_fix_removes_only_the_designator(runs, expected):
    assert _text_after_draft_fix(runs) == expected


def _ai_finding(quote, fix, element="s1-3"):
    from app.model.types import Finding
    return Finding(id=f"TYPO:1:{element}:x", rule_id="TYPO", severity="suggestion", origin="ai", slide=1,
                   element_id=element, title="Possible typo", message="m", quote=quote, suggested_fix=fix)


@pytest.mark.parametrize("quote, fix, ok", [
    ("retrun", "return", True),
    ("The committe approved", "The committee approved the revised targets in March", True),
    ("Market data is refreshed each spring", "Use the same construction for each bullet, e.g. 'Refreshed…'", False),
    ("Stronger retention", "Strengthen retention / Improve retention", False),
    ("Methodology", "Consider stating the conclusion", False),
])
def test_only_drop_in_ai_suggestions_are_auto_applied(quote, fix, ok):
    from app.fix import drop_in
    assert drop_in(quote, fix) is ok


def test_whole_sentence_suggestion_does_not_duplicate_words(tmp_path):
    from pptx import Presentation as P
    from pptx.util import Inches
    src, dst = tmp_path / "s.pptx", tmp_path / "d.pptx"
    prs = P()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Targets"
    s.placeholders[1].text = "The committe approved the revised targets in March"
    prs.save(src)
    deck = extract_deck(str(src))
    body = next(e for e in deck.slides[0].elements if e.role == "body")
    f = _ai_finding("The committe approved", "The committee approved the revised targets in March", body.id)
    report = apply_fixes(src, dst, deck, [f])
    assert report[f.id]["status"] == "applied"
    text = [sh.text_frame.text for sh in P(str(dst)).slides[0].shapes if sh.has_text_frame][-1]
    assert text == "The committee approved the revised targets in March"


