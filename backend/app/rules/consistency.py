"""Deck consistency: distinguish observed differences from authoritative rules."""
from __future__ import annotations
from collections import Counter
from statistics import median
import re

from app.model.types import Deck, Element, EMU_PER_INCH
from app.rules.common import Reporter, strict_majority, enclosing_bbox


def _term_pattern(word: str) -> re.Pattern:
    """An acronym matches exactly (with a plural s); a phrase ignores case and hyphen or space."""
    if word.isupper():
        return re.compile(rf'\b{re.escape(word)}s?\b')
    return re.compile(r'\b' + r'[\s-]+'.join(map(re.escape, re.split(r'[\s-]+', word))) + r's?\b', re.I)


def infer_client(deck: Deck) -> str | None:
    """Possessive proper nouns in headlines are a conservative client signal."""
    names=[]
    people=set()
    for slide in deck.slides:
        title=' '.join(e.text for e in slide.elements if e.role=='title')
        names.extend(set(re.findall(r'\b([A-Z][\w&-]*(?:\s+[A-Z][\w&-]*){0,2})[’\']s\b',title)))
        # "Jane Doe's salary" names a person: someone listed in an executive column is not the client.
        for e in slide.elements:
            if not e.table:continue
            cols={c.col for c in e.table.cells if c.row<e.table.header_rows
                  and re.search(r'\b(?:executive|incumbent|officer|director|employee)s?\b|^\s*name\s*$',c.text,re.I)}
            people.update(c.text.strip().casefold() for c in e.table.cells if c.col in cols and c.row>=e.table.header_rows)
    names=[n for n in names if n.casefold() not in people]
    if not names:return None
    name,count=Counter(names).most_common(1)[0]
    return name if count>=2 and count>len(names)/2 else None


def _items(slide,label: Element):
    # The role classifier owns association; this pass only compares actual items.
    labels=[e for e in slide.elements if e.role=='key_label']
    def distance(item,key):
        dx=max(0,key.bbox.x-item.bbox.x-item.bbox.w,item.bbox.x-key.bbox.x-key.bbox.w)
        dy=max(0,key.bbox.y-item.bbox.y-item.bbox.h,item.bbox.y-key.bbox.y-key.bbox.h)
        return dx*dx+dy*dy
    items=[e for e in slide.elements if e.role=='key_item' and abs(e.bbox.y-label.bbox.y)<2*EMU_PER_INCH
           and min(labels,key=lambda key:(distance(e,key),key.id)).id==label.id]
    # For text labels use a stable row-major visual order.
    return sorted(items,key=lambda e:(round(e.bbox.y/(0.05*EMU_PER_INCH)),e.bbox.x,e.id))


def _names(items):
    names=[]
    for item in items:
        if item.table:
            for row in range(item.table.n_rows):
                cs=[c for c in item.table.cells if c.row==row and not c.is_spanned]
                first=next((c.text.strip() for c in cs if c.col==0), '')
                if first:names.append(first.casefold())
        elif item.text.strip():names.append(item.text.strip().casefold())
    return tuple(names)


def _label(name: str) -> str:
    """Key entries are compared case-insensitively; show short codes as codes, words as words."""
    return name.upper() if len(name)<=4 and name.isalpha() else name[:1].upper()+name[1:]


def _spacing_boxes(items: list[Element]):
    """Compare visible entry edges, without counting a swatch and its label twice.

    Single-entry tables are a common legend representation. Use the populated
    row's cell geometry where available; a single-row table's native frame is
    still known when malformed row heights prevent cell subdivision. Multi-row
    keys with unknown geometry abstain rather than invent row positions.
    """
    swatches = [e.bbox for e in items if not e.text.strip() and e.fill
                and 0 < e.bbox.w < .5 * EMU_PER_INCH
                and 0 < e.bbox.h < .5 * EMU_PER_INCH]
    if len(swatches) >= 3:
        return swatches
    coded = any(c.col == 0 and c.text.strip() and not c.is_spanned
                for e in items if e.table for c in e.table.cells)
    boxes = []
    for e in items:
        if e.table:
            populated = sorted({c.row for c in e.table.cells
                                if c.text.strip() and not c.is_spanned})
            for row in populated:
                cells = [c for c in e.table.cells if c.row == row and not c.is_spanned]
                if coded and not any(c.col == 0 and c.text.strip() for c in cells):
                    continue  # Explanatory action swatches are a separate subgroup.
                if cells and all(c.bbox is not None for c in cells):
                    boxes.append(enclosing_bbox((c.bbox for c in cells), e.bbox))
                elif e.table.n_rows == 1:
                    boxes.append(e.bbox)
                else:
                    return []
        elif e.text.strip():
            boxes.append(e.bbox)
    return boxes


def _key_border(slide, label: Element, items: list[Element], tolerance: float, slide_area: int):
    """Associate a nearby frame with the complete key, not just its label.

    Text boxes can protrude slightly across the top stroke. Require every
    associated entry inside the frame within the configured geometric tolerance,
    and exclude broad background/content frames rather than guessing their role.
    """
    if label.line_color:
        return label
    if len(items) < 2:
        return None
    members = [label, *items]
    left = min(e.bbox.x for e in members)
    top = min(e.bbox.y for e in members)
    right = max(e.bbox.x + e.bbox.w for e in members)
    bottom = max(e.bbox.y + e.bbox.h for e in members)
    area = max(1, (right - left) * (bottom - top))

    def contains(container, item):
        c, b = container.bbox, item.bbox
        return (c.x - tolerance <= b.x and c.y - tolerance <= b.y
                and c.x + c.w + tolerance >= b.x + b.w
                and c.y + c.h + tolerance >= b.y + b.h)

    candidates = [c for c in slide.elements
                  if c.id != label.id and c.kind in {'autoshape', 'textbox'}
                  and not c.text.strip() and c.line_color
                  and 0 < c.bbox.w * c.bbox.h < slide_area / 2
                  and c.bbox.w * c.bbox.h <= 4 * area
                  and all(contains(c, member) for member in members)]
    return min(candidates, key=lambda c: (c.bbox.w * c.bbox.h, c.id)) if candidates else None


def _row_entries(items: list[Element]):
    """Swatch-plus-label boxes for a key laid out in a single row, left to right; [] otherwise."""
    swatches = sorted((i for i in items if not i.text.strip() and i.fill and 0 < i.bbox.w < .5*EMU_PER_INCH
                       and 0 < i.bbox.h < .5*EMU_PER_INCH), key=lambda i: i.bbox.x)
    labels = [i for i in items if i.text.strip() and not i.table]
    if len(swatches) < 3 or max(s.bbox.y for s in swatches)-min(s.bbox.y for s in swatches) > .1*EMU_PER_INCH:
        return []
    entries = []
    for sw in swatches:
        right = [l for l in labels if 0 <= l.bbox.x-(sw.bbox.x+sw.bbox.w) < .3*EMU_PER_INCH and abs(l.bbox.y-sw.bbox.y) < .2*EMU_PER_INCH]
        if len(right) != 1:
            return []  # without one label per swatch the entry edges are unknown
        entries.append(enclosing_bbox([sw.bbox, right[0].bbox], sw.bbox))
    return entries


def check_consistency(deck: Deck,out: Reporter):
    labels=[(s,e) for s in deck.slides for e in s.elements if e.role=='key_label']
    majority=strict_majority(e.text.strip().rstrip(':').casefold() for _,e in labels)
    if majority:
        for s,e in labels:
            if e.text.strip().rstrip(':').casefold()!=majority:
                shown=next(x.text.strip() for _,x in labels if x.text.strip().rstrip(':').casefold()==majority)
                out.add('KEY_LABEL_INCONSISTENT',s,e,shown,e.text.strip(),'The key label differs from the label used on most slides.')
    profiles=[]
    associated={}
    for s,e in labels:
        items=_items(s,e)
        associated[e.id]=items
        names=_names(items)
        if names:profiles.append((s,e,items,names))
        # A lone swatch is only a missing label when it shares a group with a key.
        swatches=[i for i in items if not i.text.strip() and i.fill and i.bbox.w<0.5*EMU_PER_INCH and i.bbox.h<0.5*EMU_PER_INCH]
        adjacent_text=[i for i in s.elements if i.text.strip() and i.role not in {'title','confidentiality','slide_number','key_label'}]
        for swatch in swatches:
            adjacent=any(abs(t.bbox.y-swatch.bbox.y)<max(t.bbox.h,swatch.bbox.h) and -0.1*EMU_PER_INCH<=t.bbox.x-(swatch.bbox.x+swatch.bbox.w)<0.3*EMU_PER_INCH for t in adjacent_text)
            if not adjacent:out.add('KEY_MISSING_LABEL',s,swatch,'Label adjacent to coloured swatch','No adjacent label','A key swatch has no adjacent text label.',confidence=0.8)
        # Single-column edge gaps, not text-box centres. A fixed 0.05-inch
        # allowance is too coarse for a small legend; use relative variation
        # with a one-point noise floor and the configured absolute ceiling.
        boxes = _spacing_boxes(items)
        if len(boxes) >= 3 and max(b.x for b in boxes)-min(b.x for b in boxes) < .2*EMU_PER_INCH:
            boxes.sort(key=lambda b: b.y)
            gaps = [b.y-a.y-a.h for a, b in zip(boxes, boxes[1:])]
            tolerance = max(out.param('KEY_SPACING', 'minimum_variation_pt', 1.0)*EMU_PER_INCH/72,
                            min(out.param('KEY_SPACING', 'tolerance_in', .05)*EMU_PER_INCH,
                                max(0, median(gaps))*out.param('KEY_SPACING', 'relative_tolerance', .25)))
            if min(gaps) >= 0 and max(gaps)-min(gaps) > tolerance:
                out.add('KEY_SPACING', s, e, 'Even vertical gaps between key entries',
                        ', '.join(f'{g/EMU_PER_INCH:.3f} in' for g in gaps),
                        'The gaps between key entries vary. Review the spacing; this is a layout suggestion, not an automatic correction.',
                        bbox=enclosing_bbox([e.bbox, *boxes], e.bbox))
        else:
            # A key laid out in one row: compare gaps between whole entries (swatch plus its label),
            # so labels of different lengths do not read as uneven spacing.
            entries = _row_entries(items)
            if len(entries) >= 3:
                gaps = [b.x-a.x-a.w for a, b in zip(entries, entries[1:])]
                tolerance = max(out.param('KEY_SPACING', 'minimum_variation_pt', 1.0)*EMU_PER_INCH/72,
                                min(out.param('KEY_SPACING', 'tolerance_in', .05)*EMU_PER_INCH,
                                    max(0, median(gaps))*out.param('KEY_SPACING', 'relative_tolerance', .25)))
                if min(gaps) >= 0 and max(gaps)-min(gaps) > tolerance:
                    out.add('KEY_SPACING', s, e, 'Even horizontal gaps between key entries',
                            ', '.join(f'{g/EMU_PER_INCH:.3f} in' for g in gaps),
                            'The gaps between key entries vary. Review the spacing; this is a layout suggestion, not an automatic correction.',
                            bbox=enclosing_bbox([e.bbox, *entries], e.bbox))
    # An earlier key is a disclosed comparison reference, not brand authority.
    # Repetition alone cannot make a later order correct. Compare shared entries
    # only, prefer the most comparable earlier key and break ties chronologically.
    ordered = sorted(profiles, key=lambda r: (r[0].index, r[1].bbox.y, r[1].bbox.x, r[1].id))
    for s, e, items, names in ordered:
        if len(names) != len(set(names)):
            continue  # Repeated labels do not define an unambiguous sequence.
        candidates = [(ref, set(names) & set(ref[3])) for ref in ordered
                      if ref[0].index < s.index and len(ref[3]) == len(set(ref[3]))]
        candidates = [(ref, shared) for ref, shared in candidates if len(shared) >= 3]
        if not candidates:
            continue
        reference, shared = min(candidates, key=lambda r: (-len(r[1]), r[0][0].index,
                                                         r[0][1].bbox.y, r[0][1].bbox.x, r[0][1].id))
        reference_names = tuple(n for n in reference[3] if n in shared)
        actual_names = tuple(n for n in names if n in shared)
        if actual_names != reference_names:
            ref_slide = reference[0].index
            out.add('KEY_ORDER_INCONSISTENT', s, e,
                    f'Reference on slide {ref_slide}: ' + ' / '.join(map(_label, reference_names)),
                    ' / '.join(map(_label, actual_names)),
                    f'This key orders the shared entries differently from slide {ref_slide}. Confirm the intended order and use it consistently; the earlier slide is a comparison reference, not a brand requirement.',
                    bbox=enclosing_bbox([e.bbox, *(i.bbox for i in items)], e.bbox))
    # Key borders can be placed on the key's own shape or its shared container.
    styles=[]
    for s,e in labels:
        border = _key_border(s, e, associated[e.id],
                             out.param('KEY_BOX_INCONSISTENT', 'tolerance_in', 0.05) * EMU_PER_INCH,
                             deck.slide_width * deck.slide_height)
        styles.append((s, e, border is not None, border))
    common=strict_majority(v for _,_,v,_ in styles)
    if common is not None:
        for s, e, style, border in styles:
            if style != common:
                out.add('KEY_BOX_INCONSISTENT', s, e,
                        'Bordered' if common else 'Unboxed',
                        'Bordered' if style else 'Unboxed',
                        'The key border differs from the style used on most slides.',
                        bbox=border.bbox if border else e.bbox)
    client=infer_client(deck)
    if client:
        for s in deck.slides:
            for e in s.elements:
                if not e.table:continue
                candidates=[c.text.strip() for c in e.table.cells if c.col==0 and c.text.strip().casefold().startswith(client.casefold()+' ')]
                # Only suffix variants that actually occur on a client-coloured row
                # or exact client-name stem; do not flag ordinary prose mentions.
                variants=[c.text.strip() for c in e.table.cells if c.col==0 and c.fill=='F1FFE1' and c.text.strip() in candidates]
                if variants:out.add('CLIENT_NAME_INCONSISTENT',s,e,client,', '.join(variants),'The client row uses an extended name while headlines use a shorter client name.',confidence=0.85)
    # Column headers that name the same thing should use the same words on every table. Each group lists
    # interchangeable wordings; the first is the display form. Units in brackets are ignored ("Base Salary ($000s)").
    groups=out.param('HEADER_TERM_INCONSISTENT','groups',None) or [['Company','Company Name']]
    preferred=str(out.param('HEADER_TERM_INCONSISTENT','preferred','') or '').casefold()
    for group in groups:
        variants={str(t).casefold():str(t) for t in group}
        headers=[]
        for s in deck.slides:
            for e in s.elements:
                if e.table and e.table.n_cols>=3 and e.table.n_rows>=3:
                    for c in e.table.cells:
                        label=re.sub(r'\s*\([^)]*\)','',' '.join(c.text.split())).strip().casefold()
                        if c.row<e.table.header_rows and label in variants:headers.append((s,e,label))
        common=preferred if preferred in variants and headers else strict_majority(v for _,_,v in headers)
        if common and len({v for _,_,v in headers})>1:
            for s,e,v in headers:
                if v!=common:out.add('HEADER_TERM_INCONSISTENT',s,e,variants[common],variants[v],f'A column header says “{variants[v]}” where most tables say “{variants[common]}”.')
    # "Consistent terminology across slides and decks": one name for the same plan throughout the
    # slide text. Each group maps a name to its wordings (acronym and full form count as one name).
    # Tables are left to HEADER_TERM_INCONSISTENT, so a "Target STI" header doesn't vote here.
    for group in out.param('TERM_INCONSISTENT','groups',None) or []:
        patterns=[(name,_term_pattern(w)) for name,words in group.items() for w in sorted(words,key=len,reverse=True)]
        uses=[]
        for sl in deck.slides:
            for e in sl.elements:
                if e.table or not e.text or e.role in {'confidentiality','slide_number'}:continue
                text=e.text
                for name,pattern in patterns:  # longest wording first, so "STI" can't match inside "STIP"
                    if pattern.search(text):
                        uses.append((sl,e,name));text=pattern.sub(' ',text)
        slides={}
        for sl,_,name in uses:slides.setdefault(name,set()).add(sl.index)
        counts=sorted(((len(v),k) for k,v in slides.items()),reverse=True)
        if len(counts)>1 and counts[0][0]>counts[1][0]:
            common=counts[0][1]
            flagged=set()
            for sl,e,name in uses:
                if name!=common and (e.id,name) not in flagged:
                    flagged.add((e.id,name))
                    out.add('TERM_INCONSISTENT',sl,e,common,name,f'This slide calls it “{name}” where the rest of the deck says “{common}”.')
