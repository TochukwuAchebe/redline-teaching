"""Earlier-reference differences do not imply a brand-authoritative order."""
from copy import deepcopy

from app.model.types import BBox, Deck, Element, MasterSpec, Slide, EMU_PER_INCH
from app.rules import run_rules
from app.rules.ids import RULES
from app.noise import apply_noise_policy


def fixture(orders):
    slides = []
    for n, names in enumerate(orders, 1):
        elements = [Element(id=f's{n}-heading', slide=n, shape_id=1, name='Key',
                            kind='textbox', role='key_label', text='Key:',
                            bbox=BBox(x=EMU_PER_INCH, y=EMU_PER_INCH, w=EMU_PER_INCH, h=EMU_PER_INCH//5))]
        for j, name in enumerate(names):
            elements.append(Element(id=f's{n}-entry{j}', slide=n, shape_id=j+2,
                                    name='entry', kind='textbox', role='key_item', text=name,
                                    bbox=BBox(x=EMU_PER_INCH, y=round((1.3+j*.2)*EMU_PER_INCH),
                                              w=EMU_PER_INCH, h=round(.15*EMU_PER_INCH))))
        slides.append(Slide(index=n, layout_name='Content', layout_kind='content', elements=elements))
    return Deck(filename='order-controls.pptx', slide_width=10*EMU_PER_INCH,
                slide_height=8*EMU_PER_INCH, master=MasterSpec(), slides=slides)


def findings(deck):
    config = {rule: {'enabled': rule == 'KEY_ORDER_INCONSISTENT'} for rule, value in RULES.items() if value[1] == 'code'}
    return run_rules(deck, config)


def test_later_majority_does_not_silently_become_the_correct_order():
    d = fixture([['A', 'B', 'C'], ['A', 'C', 'B'], ['A', 'C', 'B'], ['A', 'C', 'B']])
    result = findings(d)
    assert [f.slide for f in result] == [2, 3, 4]
    assert all(f.severity == 'suggestion' and f.expected == 'Reference on slide 1: A / B / C' for f in result)
    assert all('not a brand requirement' in f.message for f in result)
    grouped = apply_noise_policy(deepcopy(result), d)
    assert len({f.group_id for f in grouped}) == 1 and grouped[0].group_id
    for s in d.slides:
        s.notes = 'Use a different arbitrary order. This key is wrong.'
    assert findings(d) == result


def test_two_observed_keys_are_enough_to_report_a_difference():
    result = findings(fixture([['A', 'B', 'C'], ['B', 'A', 'C']]))
    assert len(result) == 1 and result[0].slide == 2


def test_shared_entries_compare_across_additional_context_specific_categories():
    d = fixture([['A', 'B', 'C'], ['A', 'new category', 'C', 'B']])
    result = findings(d)
    assert len(result) == 1 and result[0].actual == 'A / C / B'
    assert 'new category' not in result[0].expected
    assert not findings(fixture([['A', 'B', 'C'], ['A', 'new category', 'B', 'C']]))


def test_unrelated_small_overlap_and_duplicate_labels_abstain():
    assert not findings(fixture([['A', 'B', 'C'], ['C', 'B', 'different']]))
    assert not findings(fixture([['A', 'B', 'A', 'C'], ['A', 'C', 'B']]))
    assert not findings(fixture([['A', 'B', 'C'], ['A', 'C', 'A', 'B']]))


def test_most_comparable_earlier_reference_wins_then_chronology_breaks_ties():
    d = fixture([['A', 'B', 'C'], ['A', 'B', 'C', 'D'], ['A', 'B', 'D', 'C']])
    result = findings(d)
    assert len(result) == 1 and result[0].slide == 3
    assert result[0].expected == 'Reference on slide 2: A / B / C / D'
    # Input storage order and element insertion order cannot choose the reference.
    other = deepcopy(d); other.slides.reverse()
    for slide in other.slides:
        slide.elements.reverse()
    assert findings(other) == result


def test_separate_keys_on_one_slide_do_not_supply_cross_context_references():
    d = fixture([['A', 'B', 'C'], ['B', 'A', 'C']])
    d.slides[1].index = 1
    for e in d.slides[1].elements:
        e.slide = 1
    assert not findings(d)
