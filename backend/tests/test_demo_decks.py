"""The public demo decks: a clean control, and a deck with planted issues (samples/demo-answer-key.yaml).

Rebuild them with: python -I scripts/make_sample_decks.py
"""
import shutil
from pathlib import Path

import pytest
import yaml

from app import redesign
from app.fix import apply_fixes
from app.model.extract import extract_deck
from app.pipeline import analyze_sync
from app.upgrade import NotUpgradable

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
CLEAN, ISSUES = SAMPLES / "demo-clean.pptx", SAMPLES / "demo-with-issues.pptx"
KEY = yaml.safe_load((SAMPLES / "demo-answer-key.yaml").read_text())["issues"]


@pytest.fixture(scope="module")
def issue_findings():
    return analyze_sync(str(ISSUES), use_ai=False).findings


def test_the_template_is_read_as_the_spec():
    master = extract_deck(str(CLEAN)).master
    assert (master.title_font, master.title_size_pt, master.title_bold, master.title_anchor) == ("Calibri", 24, True, "ctr")
    assert [level.bullet for level in master.body_levels] == ["square", "dash", "arrow", "circle", "circle"]
    assert [level.indent_in for level in master.body_levels] == [0.2, 0.4, 0.6, 0.8, 1.0]
    assert master.confidentiality_text.startswith("Confidential.")


def test_clean_deck_raises_nothing():
    assert analyze_sync(str(CLEAN), use_ai=False).findings == []


@pytest.mark.parametrize("issue", [i for i in KEY if i["by"] == "code"], ids=lambda i: f"{i['slide']}-{i['rule']}")
def test_every_planted_code_issue_is_caught(issue, issue_findings):
    assert any(f.slide == issue["slide"] and f.rule_id == issue["rule"] for f in issue_findings)


def test_nothing_is_flagged_beyond_the_answer_key(issue_findings):
    planted = {(i["slide"], i["rule"]) for i in KEY}
    extra = {(f.slide, f.rule_id) for f in issue_findings if f.severity != "suggestion"} - planted
    assert not extra


def test_applied_fixes_disappear_on_recheck(issue_findings, tmp_path):
    out = tmp_path / "fixed.pptx"
    report = apply_fixes(ISSUES, out, extract_deck(str(ISSUES)), issue_findings)
    applied = {fid for fid, r in report.items() if r["status"] == "applied"}
    assert applied
    remaining = {f.id for f in analyze_sync(str(out), use_ai=False).findings}
    assert not applied & remaining
    assert analyze_sync(str(ISSUES), use_ai=False).findings  # the original is untouched


def chart(*_):
    return {"layout": "chart_panels", "headline_id": "H0",
            "chart": {"label_column": 0, "value_column": 2, "category_column": -1,
                      "highlight": ["Northwind Traders", "Not a row"]},
            "panels": []}


def test_redesign_charts_the_tables_own_figures_and_moves_the_table(tmp_path):
    deck = extract_deck(str(CLEAN))
    assert [c["slide"] for c in redesign.candidates(deck)] == [3]
    proposal = redesign.propose(deck, 3, chart)
    assert proposal["headline"] == "Northwind Traders' revenue growth is above the peer median"
    assert proposal["chart"]["highlight"] == ["Northwind Traders"]  # labels not in the table are dropped
    work = tmp_path / "deck.pptx"
    shutil.copyfile(CLEAN, work)
    appendix = redesign.apply(work, deck, proposal, tmp_path)
    after = extract_deck(str(work))
    assert appendix == len(deck.slides) + 1 == len(after.slides)
    assert not any(e.table for e in after.slides[2].elements)
    assert any(e.table for e in after.slides[appendix - 1].elements)


def test_redesign_refuses_text_the_slide_does_not_contain():
    deck = extract_deck(str(CLEAN))
    with pytest.raises(NotUpgradable):
        redesign.propose(deck, 3, lambda *_: {**chart(), "headline_id": "C99"})
