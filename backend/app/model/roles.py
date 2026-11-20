"""Conservative semantic classification based on structure and geometry only."""
from __future__ import annotations
import re
from app.model.types import Element, MasterSpec


def classify_roles(elements: list[Element], master: MasterSpec, slide_height: int,
                   layout_kind: str) -> None:
    for e in elements:
        ph = e.placeholder_type
        if ph in {'TITLE', 'CENTER_TITLE'}:
            e.role = 'title'
        elif ph == 'SUBTITLE':
            e.role = 'subtitle'
        elif ph == 'SLIDE_NUMBER':
            e.role = 'slide_number'
        elif master.confidentiality_text and e.text.strip().startswith(master.confidentiality_text[:20]):
            e.role = 'confidentiality'
        elif ph == 'FOOTER':
            e.role, e.role_confidence = 'other', .5
        elif e.kind in {'table', 'picture', 'chart'}:
            e.role = e.kind
        elif e.text.strip().rstrip(':').lower() in {'key', 'legend'}:
            e.role = 'key_label'
        elif e.text.lstrip().lower().startswith('source'):
            e.role, e.role_confidence = 'source_note', .9
        elif _is_footnote(e, slide_height):
            e.role, e.role_confidence = 'footnote', .85
        elif e.line_color and int(e.line_color[:2], 16) > 160 and int(e.line_color[2:4], 16) < 130 and not e.fill:
            e.role, e.role_confidence = 'callout', .8
        elif layout_kind == 'title' and re.search(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d|\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b', e.text, re.I):
            e.role, e.role_confidence = 'subtitle', .85
        elif layout_kind == 'title' and re.search(r'\b(?:chief|officer|manager|principal|director|author|prepared by)\b', e.text, re.I):
            e.role, e.role_confidence = 'authors', .8
        elif layout_kind == 'title' and ph not in {'TITLE', 'CTRTITLE'} and _names_only(e.text):
            e.role, e.role_confidence = 'authors', .7
        elif any(p.bullet for p in e.paragraphs) or ph in {'BODY', 'OBJECT'}:
            e.role, e.role_confidence = 'body', .85
        elif e.kind in {'textbox', 'autoshape'}:
            e.role, e.role_confidence = 'shape', .5
    # Caption inference requires both proximity and horizontal overlap.
    for e in elements:
        if e.role not in {'shape', 'body', 'other'} or not e.text or len(e.text) > 220:
            continue
        runs = [r for p in e.paragraphs for r in p.runs if r.text.strip()]
        if not runs:
            continue
        bold = all(r.bold is True for r in runs)
        # A title that isn't bold is still a title when it is a single line of
        # caption-sized text spanning most of a data table directly below it.
        plain = (not bold and e.kind == 'textbox' and len([p for p in e.paragraphs if p.text.strip()]) == 1
                 and not any(p.bullet for p in e.paragraphs) and all((r.size_pt or 0) >= 11 for r in runs))
        if not (bold or plain):
            continue
        targets = [t for t in elements if t.kind in {'table', 'picture', 'chart'}
                   and max(e.bbox.x, t.bbox.x) < min(e.bbox.x+e.bbox.w, t.bbox.x+t.bbox.w)
                   and -.03*914400 <= t.bbox.y-(e.bbox.y+e.bbox.h) <= .25*914400
                   and (bold or (t.kind == 'table' and t.table and t.table.n_rows >= 3 and t.table.n_cols >= 3
                                 and e.bbox.w >= .5*t.bbox.w))]
        if targets:
            target = min(targets, key=lambda t: abs(t.bbox.y-e.bbox.y-e.bbox.h))
            e.role = 'table_caption' if target.kind == 'table' else 'chart_caption'
            e.role_confidence = .8
    # A headline typed into a free text box (the title placeholder deleted) is still the headline:
    # bold, headline-sized text across the top of a content slide that has no title placeholder.
    if layout_kind == 'content' and not any(e.role == 'title' for e in elements):
        tops = [e for e in elements if e.kind == 'textbox' and e.text.strip() and e.role in {'shape', 'body', 'other'}
                and e.bbox.y + e.bbox.h <= .2*slide_height and e.bbox.w >= 4*914400
                and not any(p.bullet for p in e.paragraphs)
                and all(r.bold is True and (r.size_pt or 0) >= 20 for p in e.paragraphs for r in p.runs if r.text.strip())]
        if len(tops) == 1:
            tops[0].role, tops[0].role_confidence = 'title', .7
    labels = [e for e in elements if e.role == 'key_label']
    for e in elements:
        if e.role not in {'shape', 'table', 'other'}:
            continue
        is_swatch_table = e.table and e.table.n_cols <= 2 and e.table.n_rows <= 12 and e.bbox.w <= 3*914400
        is_small_shape = e.kind in {'textbox', 'autoshape'} and e.bbox.h <= .3*914400 and e.bbox.w <= 2*914400
        if not (is_swatch_table or is_small_shape):
            continue
        for k in labels:
            dx,dy=e.bbox.x-k.bbox.x,e.bbox.y-k.bbox.y
            horizontal = -.2*914400 <= dx <= 9*914400 and -.1*914400 <= dy <= .35*914400
            vertical = abs(dx) <= .4*914400 and 0 <= dy <= 2*914400
            if horizontal or vertical:
                e.role, e.role_confidence = 'key_item', .8 if is_swatch_table else .65
                break


NAME = re.compile(r"[A-Z][a-z'’-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'’-]+){1,2}")


def _names_only(text: str) -> bool:
    """'Jane Doe, John Roe' or one name per line: an author block without job titles."""
    parts = [p.strip() for p in re.split(r'\n|,|;|\band\b|&', text) if p.strip()]
    return 1 <= len(parts) <= 6 and all(NAME.fullmatch(p) for p in parts) and len(text) <= 160


def _is_footnote(e: Element, slide_height: int) -> bool:
    runs=[r for p in e.paragraphs for r in p.runs if r.text.strip()]
    small=bool(runs) and all(r.size_pt is not None and r.size_pt <= 10 for r in runs)
    textual_marker=bool(re.match(r'^\s*(?:\(\d+\)|[*†‡§]|\d+\.|\([a-z]\)\s)',e.text))
    superscript=any(r.superscript for p in e.paragraphs for r in p.runs[:1])
    # Auto-numbering is DrawingML metadata, so its marker is absent from plain text.
    auto_marker=any(p.bullet == 'other' and p.bullet_char is None for p in e.paragraphs)
    return small and ((e.bbox.y > .8*slide_height and (textual_marker or superscript))
                      or (e.bbox.y > .6*slide_height and auto_marker and all(r.italic for r in runs)))
