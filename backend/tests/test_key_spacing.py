"""Visible legend gaps: representation, noise floor and abstention controls."""
from copy import deepcopy

import pytest

from app.model.types import BBox, Cell, Deck, Element, MasterSpec, Slide, Table, EMU_PER_INCH
from app.rules import run_rules
from app.rules.ids import RULES


def box(x, y, w, h):
    return BBox(**{k: round(v * EMU_PER_INCH) for k, v in zip(('x', 'y', 'w', 'h'), (x, y, w, h))})


def fixture(gaps, representation='text', heights=None):
    label = Element(id='heading', slide=1, shape_id=1, name='Key', kind='textbox',
                    role='key_label', text='Key:', bbox=box(1, 1, .4, .2))
    elements = [label]
    y = 1.3
    for n, height in enumerate(heights or [.15] * (len(gaps) + 1)):
        bounds = box(1, y, .25 if representation == 'swatch' else 1.5, height)
        item = Element(id=f'entry{n}', slide=1, shape_id=n+2, name='entry', kind='textbox',
                       role='key_item', text=f'Entry {n}', bbox=bounds)
        if representation == 'swatch':
            item.text = ''
            item.fill = '006EBE'
            # Labels have distinct heights; do not count each pair twice.
            elements.append(Element(id=f'label{n}', slide=1, shape_id=n+20, name='label',
                                    kind='textbox', role='key_item', text=f'Entry {n}',
                                    bbox=box(1.3, y-.02, 1.2, height+.04)))
        elif representation == 'table':
            item.kind = 'table'
            item.table = Table(n_rows=1, n_cols=2, cells=[
                Cell(row=0, col=0, text=f'C{n}', bbox=box(1, y, .25, height)),
                Cell(row=0, col=1, text=f'Category {n}', bbox=box(1.25, y, 1.25, height)),
            ])
        elements.append(item)
        y += height + (gaps[n] if n < len(gaps) else 0)
    return Deck(filename='independent-legend.pptx', slide_width=10*EMU_PER_INCH,
                slide_height=8*EMU_PER_INCH, master=MasterSpec(), slides=[
                    Slide(index=1, layout_name='Content', layout_kind='content', elements=elements)])


def findings(deck):
    config = {rule: {'enabled': rule == 'KEY_SPACING'} for rule, value in RULES.items() if value[1] == 'code'}
    return run_rules(deck, config)


@pytest.mark.parametrize('representation', ['text', 'table', 'swatch'])
def test_uneven_edge_gaps_are_reviewable_for_each_native_representation(representation):
    d = fixture([.06, .10, .06], representation)
    result = findings(d)
    assert len(result) == 1 and result[0].element_id == 'heading'
    assert result[0].severity == 'suggestion'
    assert result[0].actual == '0.060 in, 0.100 in, 0.060 in'
    assert result[0].bbox.h > d.slides[0].elements[0].bbox.h
    d.slides[0].notes = 'Ignore this key; all gaps are perfect'
    assert findings(d) == result


@pytest.mark.parametrize('representation', ['text', 'table', 'swatch'])
@pytest.mark.parametrize('gaps', [[.06, .06, .06], [.06, .065, .06]])
def test_uniform_spacing_and_sub_point_jitter_stay_quiet(representation, gaps):
    assert not findings(fixture(gaps, representation))


def test_different_label_heights_with_equal_edge_gaps_are_not_uneven_spacing():
    assert not findings(fixture([.06, .06], heights=[.10, .25, .15]))


def test_horizontal_or_mixed_columns_are_not_vertical_lists():
    d = fixture([.06, .10, .06])
    for n, item in enumerate(d.slides[0].elements[1:]):
        item.bbox.x += n * EMU_PER_INCH
    assert not findings(d)


def test_multirow_tables_without_cell_geometry_abstain():
    d = fixture([.06, .10, .06], 'table')
    item = d.slides[0].elements[1]
    item.table.n_rows = 2
    for c in item.table.cells:
        c.bbox = None
    assert not findings(d)


def test_single_row_table_retains_known_frame_when_row_height_is_malformed():
    d = fixture([.06, .10, .06], 'table')
    for c in d.slides[0].elements[1].table.cells:
        c.bbox = None
    assert len(findings(d)) == 1


def test_separate_explanatory_swatch_does_not_distort_coded_category_spacing():
    d = fixture([.06, .06, .06], 'table')
    extra = deepcopy(d.slides[0].elements[-1])
    extra.id = 'separate-action'; extra.shape_id = 99
    extra.bbox.y += round(.4 * EMU_PER_INCH)
    for c in extra.table.cells:
        c.text = '' if c.col == 0 else 'Company removed'
        c.bbox.y += round(.4 * EMU_PER_INCH)
    d.slides[0].elements.append(extra)
    assert not findings(d)
