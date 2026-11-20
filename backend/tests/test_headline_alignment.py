"""The disputed written headline rule stays advisory and respects pinned policy."""
import asyncio
import pytest
from app import pipeline
from app.acme_policy import apply_interpretation
from app.guidelines import builtin_snapshot
from app.rules import run_rules
from app.rules.ids import RULES
from tests.test_rules import deck, element


def source(anchor, layout='content'):
    title = element('title', 'Revenue growth accelerates')
    title.vertical_anchor = anchor
    result = deck([title])
    result.slides[0].layout_kind = layout
    return result


def alignment(deck):
    config = {r: {'enabled': r == 'TITLE_VERTICAL_ALIGNMENT'} for r in RULES}
    return run_rules(deck, config)


@pytest.mark.parametrize('anchor', ['t', 'b'])
def test_known_misalignment_has_exact_native_target_and_conflict_evidence(anchor):
    findings = apply_interpretation(alignment(source(anchor)))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == 'TITLE_VERTICAL_ALIGNMENT'
    assert f.severity == 'suggestion' and f.origin == 'code'
    assert f.bbox == source(anchor).slides[0].elements[0].bbox
    assert f.actual == ('Top-aligned' if anchor == 't' else 'Bottom-aligned')
    assert 'line 36' in f.message and 'line 230' in f.message
    assert 'disputed' in f.rule_ref
    from app.fix import is_fixable
    assert not is_fixable(f)


@pytest.mark.parametrize('anchor', ['ctr', None, 'unknown'])
def test_compliant_or_unknown_anchors_are_not_flags(anchor):
    assert alignment(source(anchor)) == []


@pytest.mark.parametrize('layout', ['title', 'section', 'other'])
def test_content_headline_rule_does_not_invent_other_layout_requirements(layout):
    assert alignment(source('b', layout)) == []


def test_saved_policy_cannot_acquire_a_new_rule_or_mutate_its_snapshot(monkeypatch):
    from copy import deepcopy
    d = source('b')
    monkeypatch.setattr(pipeline, 'extract_deck', lambda _: d)
    current = builtin_snapshot()
    old = deepcopy(current)
    old['config'].pop('TITLE_VERTICAL_ALIGNMENT')
    original = deepcopy(old)
    before = asyncio.run(pipeline.analyze('unseen.pptx', use_ai=False, guideline=old))
    assert not any(f.rule_id == 'TITLE_VERTICAL_ALIGNMENT' for f in before.findings)
    assert old == original
    after = asyncio.run(pipeline.analyze('unseen.pptx', use_ai=False, guideline=current))
    matches = [f for f in after.findings if f.rule_id == 'TITLE_VERTICAL_ALIGNMENT']
    assert len(matches) == 1 and matches[0].severity == 'suggestion'
    assert after.coverage['ai']['state'] == 'off'
