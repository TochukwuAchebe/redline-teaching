"""lesson/core.py, the one-hour version, keeps working on the demo decks."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lesson"))
import core  # noqa: E402


def test_core_finds_the_measurable_issues_and_nothing_on_the_clean_deck():
    runs, deck = core.read_deck(str(ROOT / "samples/demo-with-issues.pptx"))
    found = {(f.rule, f.slide) for f in core.check(runs, deck)}
    assert {("BULLET_FONT", 2), ("TERM_TGT", 3), ("TERM_PERCENTILE", 3), ("CONF_MISSING", 4)} <= found
    runs, deck = core.read_deck(str(ROOT / "samples/demo-clean.pptx"))
    assert core.check(runs, deck) == []


def test_inherited_sizes_say_where_they_came_from():
    runs, _ = core.read_deck(str(ROOT / "samples/demo-clean.pptx"))
    title = next(r for r in runs if r.slide == 2 and r.role == "title")
    assert (title.size_pt, title.bold, title.source) == (24, True, "master text styles")


def test_ai_output_is_kept_only_when_its_quote_is_on_the_slide_and_confident():
    _, deck = core.read_deck(str(ROOT / "samples/demo-with-issues.pptx"))

    def fake(text):
        if "Peer group overview" not in text:
            return []
        return [{"rule": "TITLE_TAKEAWAY", "quote": "Peer group overview", "message": "Topic only", "confidence": 0.9},
                {"rule": "TITLE_TAKEAWAY", "quote": "Not on the slide", "message": "Invented", "confidence": 0.99},
                {"rule": "BULLET_PARALLEL", "quote": "Peer group overview", "message": "Unsure", "confidence": 0.4}]

    assert [(f.rule, f.slide, f.origin) for f in core.ai_review(deck, fake)] == [("TITLE_TAKEAWAY", 6, "ai")]


def test_repeats_become_one_decision():
    same = [core.Finding("TERM_TGT", n, "", "m", "Target") for n in (1, 2, 3)]
    assert len(core.group(same)) == 1
