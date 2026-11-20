"""Regressions for the remaining QA defects (docs/testing/Redline-test-cases.xlsx, Defects sheet)."""
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.model.extract import extract_deck
from app.model.roles import classify_roles
from app.model.types import BBox, Cell, EMU_PER_INCH, MasterSpec, Paragraph, Run, Table
from app.rules import checks
from app.rules.consistency import infer_client
from tests.test_rules import check, deck, element


def _cell(row, col, text, size=10, bold=False, superscript=False, fill=None):
    runs = [Run(text=text, font='Calibri', size_pt=size, bold=bold, color='FFFFFF' if row == 0 else '000000')]
    if superscript:
        runs.append(Run(text=superscript, font='Calibri', size_pt=size, superscript=True))
    return Cell(row=row, col=col, text=text, paragraphs=[Paragraph(text=text, runs=runs)],
                fill=fill or ('006EBE' if row == 0 else 'FFFFFF' if row % 2 else 'F2F2F2'), anchor='b' if row == 0 else 't', margin_left_in=0.03)


def _table(rows, sizes=None, **kw):
    cells = [_cell(r, c, t, size=(sizes or {}).get((r, c), 10), bold=r == 0) for r, row in enumerate(rows) for c, t in enumerate(row)]
    return element('table', '', kind='table', table=Table(n_rows=len(rows), n_cols=len(rows[0]), cells=cells), paragraphs=[], **kw)


PEOPLE = [['Executive', 'Base salary ($000s)', 'Increase'], ['Jane Doe', '$1,200', '4.0%'], ['John Roe', '$750', '3.5%'], ['Ann Lee', '$600', '3.0%']]


def test_person_in_two_headlines_is_not_the_client():  # D-04
    d = deck()
    slides = []
    for i in (1, 2):
        s = d.slides[0].model_copy(update={'index': i, 'elements': [element('title', "Jane Doe's base salary leads the group", id=f's{i}-1', slide=i), _table(PEOPLE, id=f's{i}-2', slide=i)]})
        slides.append(s)
    d.slides = slides
    assert infer_client(d) is None
    assert not check(d, 'TABLE_ZEBRA')


def test_company_possessive_is_still_the_client():
    d = deck()
    d.slides = [d.slides[0].model_copy(update={'index': i, 'elements': [element('title', "Orion's pay sits near the median", id=f's{i}-1', slide=i)]}) for i in (1, 2)]
    assert infer_client(d) == 'Orion'


@pytest.mark.parametrize('sizes, flagged', [({}, False), ({(0, 0): 12, (0, 1): 12, (0, 2): 12}, True), ({(2, 1): 11}, True)])
def test_table_font_size_is_consistent_across_the_table(sizes, flagged):  # D-09
    found = check(deck([_table(PEOPLE, sizes)]), 'TABLE_FONT')
    assert bool(found) is flagged
    if flagged:
        assert 'mixed sizes' in found[0].actual


def test_percent_precision_is_consistent_across_columns():  # D-18
    rows = [['Metric', 'Threshold', 'Target'], ['Revenue', '6.0%', '8.50%'], ['Margin', '12.0%', '14.00%'], ['TSR', '25.0%', '50.00%']]
    found = check(deck([_table(rows)]), 'TABLE_NUMBER_FORMAT')
    assert found and 'Percentages' in found[0].actual
    rows = [['Metric', 'Threshold', 'Target'], ['Revenue', '6.0%', '$1,200'], ['Margin', '12.0%', '$900'], ['TSR', '25.0%', '$750']]
    assert not check(deck([_table(rows)]), 'TABLE_NUMBER_FORMAT')  # different units may differ


@pytest.mark.parametrize('align, flagged', [('l', False), (None, False), ('ctr', True), ('just', True)])
def test_bullets_are_left_aligned(align, flagged):  # D-14
    p = Paragraph(text='Goal ranges narrowed', bullet='square', align=align, indent_in=None, space_before_pt=6, line_spacing=1.0,
                  runs=[Run(text='Goal ranges narrowed', font='Calibri', size_pt=16)])
    assert bool(check(deck([element('body', p.text, paragraphs=[p])]), 'BULLET_INDENT')) is flagged


@pytest.mark.parametrize('points, flagged', [(None, False), (19.2, False), (24, True)])
def test_exact_point_line_spacing(points, flagged):  # D-15
    p = Paragraph(text='Goal ranges narrowed', bullet='square', space_before_pt=6, line_spacing=None if points else 1.0, line_spacing_pt=points,
                  runs=[Run(text='Goal ranges narrowed', font='Calibri', size_pt=16)])
    assert bool(check(deck([element('body', p.text, paragraphs=[p])]), 'BULLET_SPACING')) is flagged


def test_numbered_list_says_what_it_is():  # D-17
    p = Paragraph(text='First step', bullet='other', bullet_char=None, space_before_pt=6, line_spacing=1.0,
                  runs=[Run(text='First step', font='Calibri', size_pt=16)])
    found = check(deck([element('body', p.text, paragraphs=[p])]), 'BULLET_GLYPH')
    assert found and found[0].actual == 'numbered list' and 'numbered list' in found[0].message


def _footnote_slide(markers, note):
    body = element('body', 'Bonus', id='s1-1', paragraphs=[Paragraph(text='Bonus', runs=[Run(text='Bonus', font='Calibri', size_pt=16)]
                                                                   + [Run(text=m, superscript=True) for m in markers])])
    foot = element('footnote', note, id='s1-2', paragraphs=[Paragraph(text=note, runs=[Run(text=note, size_pt=10, italic=True, font='Calibri')])])
    return deck([body, foot])


@pytest.mark.parametrize('markers, note, flagged', [
    (['†'], '* One-time award; † prorated', False),
    (['(a)'], '(a) Interim chief executive', False),
    (['†'], '* One-time award', True),
    (['(b)'], '(a) Interim chief executive', True),
])
def test_symbol_and_letter_footnote_markers(markers, note, flagged):  # D-11
    assert bool(check(_footnote_slide(markers, note), 'FOOTNOTE_UNMATCHED')) is flagged


def test_invisible_confidentiality_statement_is_a_style_problem():  # D-20
    text = 'Confidential exact text'
    e = element('confidentiality', text, paragraphs=[Paragraph(text=text, runs=[Run(text=text, font='Calibri', size_pt=8, color='FFFFFF')])])
    d = deck([e])
    d.master.confidentiality_text, d.master.confidentiality_bbox = text, e.bbox.model_copy()
    found = check(d, 'CONF_STYLE')
    assert found and 'not visible' in found[0].actual


@pytest.mark.parametrize('text, flagged', [('[JS: check this number]', True), ('[Note] see appendix', False), ('Peer data [2025]', False)])
def test_bracketed_reviewer_comments(text, flagged):  # D-24
    assert bool(check(deck([element(text=text)]), 'LEFTOVER_COMMENT')) is flagged


@pytest.mark.parametrize('text, role, flagged', [
    ('John A Smith joins as chief financial officer', 'body', True),
    ('Jane Doe, John A Smith', 'authors', True),
    ('Plan A Results improved', 'body', False),
    ('Series B Funding closed', 'body', False),
    ('John A. Smith joins as chief financial officer', 'body', False),
])
def test_bare_middle_initial_in_a_name(text, role, flagged):  # D-25
    found = check(deck([element(role, text)], kind='title' if role == 'authors' else 'content'), 'INITIALS_PUNCTUATION')
    assert bool(found) is flagged
    if flagged:
        assert 'John A. Smith' in found[0].expected


def test_bare_middle_initial_in_an_executive_column():
    rows = [['Executive', 'Base salary ($000s)', 'Increase'], ['John A Smith', '$1,200', '4.0%'], ['Ann Lee', '$600', '3.0%'], ['Raj Patel', '$700', '4.5%']]
    assert check(deck([_table(rows)]), 'INITIALS_PUNCTUATION')


def test_url_path_is_not_an_abbreviation_of_target():  # D-23 follow-up
    assert not check(deck([element(text='Source: https://data.example.com/tgt/peers')]), 'TERM_TGT')
    assert check(deck([element(text='TGT bonus held at 100%')]), 'TERM_TGT')


# --- classification -----------------------------------------------------------------

def _textbox(text, y, size, bold, w=8.0, x=1.0, idx=1):
    p = Paragraph(text=text, runs=[Run(text=text, font='Calibri', size_pt=size, bold=bold)])
    return element('other', text, id=f's1-{idx}', kind='textbox', paragraphs=[p],
                   bbox=BBox(x=int(x*EMU_PER_INCH), y=int(y*EMU_PER_INCH), w=int(w*EMU_PER_INCH), h=int(0.35*EMU_PER_INCH)))


@pytest.mark.parametrize('size, bold, caption', [(14, True, True), (12, False, True), (10, False, False)])
def test_non_bold_table_title_is_a_caption(size, bold, caption):  # D-08
    caption_expected = caption
    caption = _textbox('Base salary by executive', 1.5, size, bold, w=4.5, idx=1)
    table = _table(PEOPLE, id='s1-2', bbox=BBox(x=EMU_PER_INCH, y=int(1.9*EMU_PER_INCH), w=int(4.8*EMU_PER_INCH), h=int(1.2*EMU_PER_INCH)))
    caption.role = 'other'
    classify_roles([caption, table], MasterSpec(), 7*EMU_PER_INCH, 'content')
    assert (caption.role == 'table_caption') is caption_expected


def test_free_text_headline_is_the_headline():  # D-10
    headline = _textbox('Base salaries rose 4% while targets held steady', 0.3, 24, True, w=9.0, x=0.5, idx=1)
    body = _textbox('Goal ranges narrowed', 1.6, 16, False, idx=2)
    classify_roles([headline, body], MasterSpec(), int(7.5*EMU_PER_INCH), 'content')
    assert headline.role == 'title' and body.role != 'title'


def test_no_free_text_headline_when_a_placeholder_title_exists():
    title = element('title', 'Headline', id='s1-1', placeholder_type='TITLE')
    banner = _textbox('Bold banner text across the top', 0.3, 24, True, w=9.0, idx=2)
    classify_roles([title, banner], MasterSpec(), int(7.5*EMU_PER_INCH), 'content')
    assert banner.role != 'title'


# --- extraction from real files -----------------------------------------------------

def test_chart_series_and_gradient_colours_are_extracted_and_checked(tmp_path: Path):  # D-12
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = 'Base salaries rose 4% while targets held steady'
    data = CategoryChartData()
    data.categories = ['CEO', 'CFO']
    data.add_series('Base', (1200, 750))
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(3), data).chart
    chart.plots[0].series[0].format.fill.solid()
    chart.plots[0].series[0].format.fill.fore_color.rgb = RGBColor(0xFF, 0x66, 0x00)
    box = slide.shapes.add_shape(1, Inches(7.2), Inches(2), Inches(1.5), Inches(1))
    box.fill.gradient()
    stops = box.fill.gradient_stops
    stops[0].color.rgb, stops[1].color.rgb = RGBColor(0x00, 0x6E, 0xBE), RGBColor(0x12, 0x34, 0x56)
    path = tmp_path / 'colours.pptx'
    prs.save(str(path))
    d = extract_deck(str(path))
    by_kind = {e.kind: e for e in d.slides[0].elements}
    assert 'FF6600' in by_kind['chart'].extra_colors
    assert '123456' in next(e for e in d.slides[0].elements if e.kind in {'autoshape', 'textbox'} and e.extra_colors).extra_colors
    found = {f.element_id for f in check(d, 'COLOR_OFF_PALETTE')}
    assert by_kind['chart'].id in found


def test_exact_line_spacing_is_extracted(tmp_path: Path):  # D-15
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    tb = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    tb.text_frame.text = 'Goal ranges narrowed'
    tb.text_frame.paragraphs[0].line_spacing = Pt(24)
    path = tmp_path / 'spacing.pptx'
    prs.save(str(path))
    p = next(e for e in extract_deck(str(path)).slides[0].elements if e.text).paragraphs[0]
    assert p.line_spacing_pt == 24
