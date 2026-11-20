"""Slide-local rules. Geometry is in EMU, formatting uses resolved values only."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

import yaml

from app.model.types import BBox, Deck, Element, EMU_PER_INCH, Slide
from app.rules.common import CONFIG_DIR, Reporter, cell_runs, different, enclosing_bbox, overlap, runs

FONT_DIR = Path(__file__).resolve().parents[1] / 'assets' / 'fonts'

TERMINAL = re.compile(r'[.!?:;]$')
URL = re.compile(r'\b(?:https?://|www\.)\S+', re.I)  # a path segment like /tgt/ is not an abbreviation
UNIT = re.compile(r'(?:\b|\d)(?:m|cm|mm|km|ft|in|sq\.? ?ft)$', re.I)
DESIGNATOR = re.compile(r'\bDRAFT\b|\b(?:PRELIMINARY|WORK(?:ING)?[- ]IN[- ]PROGRESS|WIP|FOR DISCUSSION(?: PURPOSES)?(?: ONLY)?|NOT FOR DISTRIBUTION|DO NOT DISTRIBUTE)\b'
                        r'|(?<![\w.])v\d+\.\d+(?![\w.])', re.I)
PLACEHOLDER = re.compile(r'\[[^\]]*\]|\b(?:author|presenter)\s+name\b|\b(?:click|type)\s+to\s+(?:add|edit)\b', re.I)
MISSING_VALUE = re.compile(r'(?i)n/?a|n\.a\.?|not applicable|-{1,2}|—')  # cells the guideline wants as – or blank
STAT = re.compile(r'^(?:\d{1,2}(?:st|nd|rd|th)\s*(?:%ile|percentile)|median|average|mean|count|n)\s*(?:\(.*\))?\s*:?$', re.I)
COMMENT = re.compile(
    r'(?m:^\s*[A-Z]{1,4}\s+(?i:question)\s*[-:])'
    r'|\[[A-Z]{1,4}\s*:[^\]]*\]'  # bracketed reviewer initials: "[JS: check this number]"
    r'|(?i:\b(?:TODO|FIXME)\b|\b(?:internal|reviewer)\s+(?:note|comment)\s*:'
    r'|^\s*(?:Page\s+)?to be updated\b)'
)



def _pt(value):
    return f'{value:g}' if isinstance(value,(int,float)) else str(value)


def _in(value):
    """Inches for reviewers: two decimals at most, no trailing zeros."""
    return f'{value:.2f}'.rstrip('0').rstrip('.')+' in' if isinstance(value,(int,float)) else str(value)


def _describe_move(actual,target):
    """Say how a box differs from where it belongs, e.g. '0.15 in lower, 0.19 in taller'."""
    parts=[]
    for delta,more,less in ((actual.x-target.x,'right','left'),(actual.y-target.y,'lower','higher'),
                             (actual.w-target.w,'wider','narrower'),(actual.h-target.h,'taller','shorter')):
        if abs(delta)>=0.01*EMU_PER_INCH:
            parts.append(f'{_in(abs(delta)/EMU_PER_INCH)} {more if delta>0 else less}')
    return ', '.join(parts) or 'Slightly different position'


def _excerpt(text,limit=40):
    text=' '.join(text.split())
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'


def _tail(text,limit=50):
    """The end of a long line, starting at a word boundary."""
    text=' '.join(text.split())
    if len(text)<=limit:return text
    cut=text[-limit:]
    return '…'+cut[cut.find(' ')+1:] if ' ' in cut else '…'+cut


# Dotted initials whose last letter lacks its full stop: "C.R Bard", "J.P Morgan" (but not "U.S." or "Ph.D").
INITIALS=re.compile(r"\b(?:[A-Z]\.){1,3}[A-Z](?![.\w&])")
# A bare middle initial in a person's name: "John A Smith". Only where a name is expected,
# because "Plan A Results" and "Series B Funding" have the same shape.
MIDDLE_INITIAL=re.compile(r"\b([A-Z][a-z]+) ([A-Z]) ([A-Z][a-z'’-]+)\b")
ROLE_AFTER=re.compile(r"^\W{0,3}(?:\w+\s+){0,3}(?:chief|officer|president|director|chair|ceo|cfo|coo|gc|joins|joined|appointed|named|hired|retires|retired)\b",re.I)
NAME_HEADER=re.compile(r"\b(?:executive|incumbent|name|officer|director|author)s?\b",re.I)


def _bare_middle_initials(e: Element) -> list[str]:
    texts=[]
    if e.table:
        name_cols={c.col for c in e.table.cells if c.row<e.table.header_rows and NAME_HEADER.search(c.text)}
        texts=[(c.text,True) for c in e.table.cells if c.col in name_cols and c.row>=e.table.header_rows and not c.is_spanned]
    else:
        texts=[(e.text,e.role=='authors')]
    found=[]
    for text,name_context in texts:
        for m in MIDDLE_INITIAL.finditer(text):
            if name_context or ROLE_AFTER.search(text[m.end():]):
                found.append(m.group(0))
    return found


ALIGN={'l':'left-aligned','ctr':'centred','r':'right-aligned','just':'justified','dist':'distributed'}


def _font_mismatches(rs, *, font=None, size=None, bold=None, italic=None):
    """Describe only the attributes that differ, in reviewer language, one line per kind of problem."""
    groups: dict[str,list[str]] = {}
    for r in rs:
        diffs=[]
        if font and r.font and r.font.casefold()!=font.casefold():diffs.append(r.font)
        if different(r.size_pt,size,0.1):diffs.append(f'{_pt(r.size_pt)} pt')
        if different(r.bold,bold):diffs.append('bold' if r.bold else 'not bold')
        if different(r.italic,italic):diffs.append('italic' if r.italic else 'not italic')
        if diffs:groups.setdefault(', '.join(diffs),[]).append(' '.join(r.text.split()))
    bad=[]
    for problem,texts in groups.items():
        sample=texts[0] if len(texts[0])<=40 else texts[0][:38].rstrip()+'…'
        more=f' + {len(texts)-1} more' if len(texts)>1 else ''
        bad.append(f'{problem} (“{sample}”{more})')
    return bad


def _moved(a,b,tolerance):
    return any(abs(getattr(a,k)-getattr(b,k))>tolerance for k in ('x','y','w','h'))


# Words that make a headline a statement ("Pay remains competitive") rather than a label ("Pay review").
VERBS = set('''is are was were be been being has have had will would should could can may might must do does did
remain remains remained rose rise rises fell fall falls grew grow grows increased increase increases decreased decrease
decreases declined decline declines reflect reflects reflected show shows showed shown lead leads led lag lags lagged
exceed exceeds exceeded trail trails trailed sit sits sat stay stays stayed move moves moved need needs needed support
supports supported align aligns aligned outperform outperforms outperformed underperform underperforms tightened
narrowed widened expanded doubled halved'''.split())


def topic_only(text: str) -> bool:
    """'Peer group overview' or 'Plan Design': a short label with no verb and no figures, so it
    names the topic instead of stating the takeaway (the guideline's own DON'T example)."""
    words=re.findall(r"[A-Za-z][A-Za-z'’-]*",text)
    if not words or len(words)>6 or re.search(r'\d',text):
        return False
    return not any(w.lower() in VERBS or (len(w)>4 and w.lower().endswith(('ed','ing'))) for w in words)


def _ticker_cell(table, cell) -> bool:
    """A stock ticker in a ticker column ('TGT' for Target Corp) is data, not an abbreviation."""
    header=' '.join(c.text for c in table.cells if c.col==cell.col and c.row<table.header_rows).lower()
    if re.search(r'ticker|symbol',header):return True
    column=[c.text.strip() for c in table.cells if c.col==cell.col and c.row>=table.header_rows and c.text.strip()]
    return len(column)>=3 and sum(bool(re.fullmatch(r'[A-Z]{1,5}',t)) for t in column)>=0.8*len(column)


def _has_date(text: str) -> bool:
    pattern = (r'\b(?:[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\.?,?\s+\d{4}|[A-Za-z]+\.?\s+\d{4}'
               r'|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{2}-\d{2})\b')
    for match in re.findall(pattern, text):
        match = re.sub(r'(?<=[A-Za-z])\.', '', match).replace(',', ', ').replace(',  ', ', ')
        for fmt in ('%B %d, %Y','%B %d %Y','%b %d, %Y','%b %d %Y','%d %B %Y','%d %b %Y','%d %B, %Y','%d %b, %Y','%B %Y','%b %Y',
                    '%m/%d/%Y','%d/%m/%Y','%m-%d-%Y','%d-%m-%Y','%Y-%m-%d'):
            try:
                datetime.strptime(match,fmt)
                return True
            except ValueError:
                pass
    return False


def estimated_lines(e: Element) -> tuple[int,float]:
    """Estimate wrapping with font metrics; return lower confidence on fallback.

    This is advisory: font availability/rendering may differ from PowerPoint.
    Explicit line breaks remain authoritative even without metrics.
    """
    sizes=[r.size_pt for r in runs(e) if r.size_pt]
    if not sizes or e.bbox.w<=0:
        return len(e.text.splitlines()) or 1, 0.6
    size=max(sizes)
    width=e.bbox.w / EMU_PER_INCH * 96
    font=None
    try:
        from PIL import ImageFont
        # The bundled Carlito comes first, so every platform measures identically.
        for candidate in (str(FONT_DIR/'Carlito-Bold.ttf'),'Carlito-Bold.ttf','Carlito.ttf','calibrib.ttf','calibri.ttf',
                          '/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf',
                          '/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf'):
            try:
                font=ImageFont.truetype(candidate,max(1,round(size*96/72)))
                break
            except OSError:
                pass
    except ImportError:
        pass
    measure=(lambda t:font.getlength(t)) if font else (lambda t:len(t)*size*96/72*0.5)
    count=0
    for line in e.text.splitlines() or ['']:
        current=''
        count+=1
        for word in line.split():
            candidate=f'{current} {word}'.strip()
            if current and measure(candidate)>width:
                count+=1;current=word
            else:
                current=candidate
    return count, 0.85 if font else 0.6


def conform_master(deck: Deck, out: Reporter) -> Deck:
    """Trust the master as the spec only where it agrees with the guideline.

    A deck built on another template would otherwise pass its own (wrong) values.
    Deviations are reported once, and slides are then checked against the guideline.
    """
    spec=deck.master
    rule='TEMPLATE_NONCONFORMING'
    font=out.param(rule,'title_font','Calibri');size=out.param(rule,'title_size_pt',24);body=out.param(rule,'body_size_pt',16)
    gaps,update=[],{}
    if spec.title_font and spec.title_font!=font:
        gaps.append(f'title font {spec.title_font}');update['title_font']=font
    if spec.title_size_pt and different(spec.title_size_pt,size,0.5):
        gaps.append(f'title size {spec.title_size_pt:g} pt');update['title_size_pt']=size
    if spec.title_bold is False:
        gaps.append('title not bold');update['title_bold']=True
    off=[l for l in spec.body_levels if different(l.size_pt,body,0.5)]
    if off:
        gaps.append('body bullets '+', '.join(f'L{l.level+1} {l.size_pt:g} pt' for l in off))
        update['body_levels']=[l.model_copy(update={'size_pt':body}) if l in off else l for l in spec.body_levels]
    if not spec.confidentiality_text:
        gaps.append('no confidentiality statement')
        update['confidentiality_text']=out.param('CONF_MISSING','text',None)
        if not spec.confidentiality_bbox:
            # Proportions of the ACME master's footer, scaled to this slide size.
            w,h=deck.slide_width,deck.slide_height
            update['confidentiality_bbox']=BBox(x=int(w*0.094),y=int(h*0.961),w=int(w*0.811),h=int(h*0.027))
    if not gaps or not deck.slides:
        return deck
    expected=f'Title {font} bold {size:g} pt; body {body:g} pt; confidentiality statement in the footer'
    out.add(rule,deck.slides[0],None,expected,'; '.join(gaps),
            'The slide master differs from the ACME guideline, so slides are checked against the guideline instead of this template.')
    return deck.model_copy(update={'master':spec.model_copy(update=update)})


def _confidentiality(deck: Deck, slide: Slide, out: Reporter):
    spec=deck.master
    expected=spec.confidentiality_text
    elements=[e for e in slide.elements if e.role=='confidentiality']
    if not elements and expected:
        # Roles come from the master's footer; on another template, match the wording itself.
        prefix=' '.join(expected.split())[:40].lower()
        elements=[e for e in slide.elements if ' '.join(e.text.split()).lower().startswith(prefix)]
    if not elements and expected and spec.confidentiality_bbox:
        # A modified legal footer may no longer pass the prefix role heuristic.
        elements=[e for e in slide.elements if e.placeholder_type=='FOOTER' and e.text.strip()
                  and not _moved(e.bbox,spec.confidentiality_bbox,0.05*EMU_PER_INCH)]
    if not elements:
        if expected:
            out.add('CONF_MISSING',slide,None,expected,'Not present','The slide is missing the confidentiality statement.',spec.confidentiality_bbox)
        return
    for e in elements:
        if expected and ' '.join(e.text.split())!=' '.join(expected.split()):
            out.add('CONF_TEXT_MODIFIED',slide,e,expected,e.text,'The confidentiality wording differs from the master statement.')
        tol=out.param('CONF_MOVED','tolerance_in',0.05)*EMU_PER_INCH
        if spec.confidentiality_bbox and _moved(e.bbox,spec.confidentiality_bbox,tol):
            out.add('CONF_MOVED',slide,e,'Same position and size as the slide master',_describe_move(e.bbox,spec.confidentiality_bbox),'The confidentiality statement differs from its master position or size.')
        bad=_font_mismatches(runs(e),font=out.param('CONF_STYLE','font',spec.theme_minor_font or 'Calibri'),size=out.param('CONF_STYLE','size_pt',8))
        visible=[r for r in runs(e) if r.text.strip()]
        if visible and all(r.color=='FFFFFF' for r in visible) and e.fill in {None,'FFFFFF'}:
            bad.append('white text on a white slide (not visible)')
        if bad:out.add('CONF_STYLE',slide,e,'Calibri, 8 pt','; '.join(bad),'The confidentiality statement has different typography from the guideline.')
        for other in slide.elements:
            # Do not interpret transparent container geometry as obscuring text.
            if other.id!=e.id and other.role not in {'confidentiality','slide_number'} and other.text.strip() and overlap(e.bbox,other.bbox)>0:
                out.add('CONF_OVERLAP',slide,e,'Unobstructed confidentiality statement',f'Overlaps “{_excerpt(other.text)}”','Another text box overlaps the confidentiality statement’s box; inspect the slide for a visible collision.',confidence=0.75)


def _titles(deck: Deck, slide: Slide, out: Reporter):
    spec=deck.master
    if slide.layout_kind=='title':
        texts='\n'.join(e.text for e in slide.elements if e.role not in {'confidentiality','slide_number'})
        # "No DRAFT or other designator": draft labels, working-status labels and version stamps.
        for e in slide.elements:
            found=DESIGNATOR.search(e.text)
            if found and e.role not in {'confidentiality','slide_number'}:
                out.add('TITLESLIDE_DRAFT',slide,e,'No draft or other designator',e.text,f'The title slide still carries the designator “{found.group(0)}”.')
        if not _has_date(texts):out.add('TITLESLIDE_DATE',slide,None,'Valid date','No valid date found','The title slide does not contain a recognisable valid date.')
        if not any(e.role=='authors' and e.text.strip() and not PLACEHOLDER.search(e.text) for e in slide.elements):out.add('TITLESLIDE_AUTHORS',slide,None,'Identified authors','No author block found','The title slide does not contain an identified author block.',confidence=0.8)
    for e in slide.elements:
        if e.role!='title':continue
        title_slide=slide.layout_kind=='title'
        # Title and section header slides specify Calibri 24 pt, not bold.
        relaxed=slide.layout_kind in {'title','section'}
        rule='TITLESLIDE_FONT' if title_slide else 'TITLE_FONT'
        font=spec.title_font or out.param(rule,'font','Calibri')
        size=spec.title_size_pt or out.param(rule,'size_pt',24)
        bold=None if relaxed else (spec.title_bold if spec.title_bold is not None else out.param('TITLE_FONT','bold',True))
        bad=_font_mismatches(runs(e),font=font,size=size,bold=bold)
        if bad:out.add(rule,slide,e,f'{font}{" bold" if bold else ""}, {_pt(size)} pt','; '.join(bad),'The headline typography differs from the required style.')
        if slide.layout_kind!='content':continue
        if e.vertical_anchor in {'t', 'b'}:
            out.add('TITLE_VERTICAL_ALIGNMENT', slide, e,
                    'Vertically centred in the headline box',
                    'Top-aligned' if e.vertical_anchor == 't' else 'Bottom-aligned',
                    'The headline text is not vertically centred in its box.')
        target=spec.title_bbox_by_layout.get(slide.layout_name)
        if target and _moved(e.bbox,target,out.param('TITLE_POSITION','tolerance_in',0.05)*EMU_PER_INCH):
            out.add('TITLE_POSITION',slide,e,'Same position and size as the layout',_describe_move(e.bbox,target),'The headline position or size differs from its layout placeholder.')
        limit=out.param('TITLE_TOO_LONG','max_lines',3)
        count,confidence=estimated_lines(e)
        # An average-character estimate is not precise enough near a threshold.
        if count>limit and (confidence>=0.8 or len(e.text.splitlines())>limit or count>limit+1):
            out.add('TITLE_TOO_LONG',slide,e,f'At most {limit} lines',f'Approximately {count} lines','The headline appears to exceed the allowed line count.',confidence=confidence)
        if TERMINAL.search(e.text.strip()):out.add('TITLE_PUNCTUATION',slide,e,'No terminal punctuation',_tail(e.text),'The headline ends with punctuation.')
        if topic_only(e.text):out.add('TITLE_TOPIC_ONLY',slide,e,'A headline that states the takeaway',e.text.strip(),'The headline names a topic rather than stating the takeaway.',confidence=0.75)


def _bullets(deck: Deck,slide: Slide,e: Element,out: Reporter):
    if e.role!='body':return
    levels={l.level:l for l in deck.master.body_levels}
    previous=None
    for p in e.paragraphs:
        if not p.text.strip() or p.bullet is None:continue
        level=levels.get(p.level)
        size=level.size_pt if level and level.size_pt is not None else out.param('BULLET_FONT','size_pt',16)
        font=deck.master.theme_minor_font or out.param('BULLET_FONT','font','Calibri')
        bad=_font_mismatches([r for r in p.runs if r.text.strip()],font=font,size=size)
        if bad:out.add('BULLET_FONT',slide,e,f'{font}, {_pt(size)} pt','; '.join(bad),'Bulleted text differs from the required typography.')
        glyph=level.bullet if level and level.bullet else ('square','dash','arrow','circle','circle')[min(p.level,4)]
        if p.bullet=='other' and p.bullet_char is None:
            out.add('BULLET_GLYPH',slide,e,glyph,'numbered list',f'A numbered list is used where this outline level calls for a {glyph} bullet.')
        elif p.bullet!=glyph:out.add('BULLET_GLYPH',slide,e,glyph,p.bullet,'A bullet symbol does not match its outline level.')
        indent=level.indent_in if level else (None if p.level==0 else (0.4 if p.level==1 else 0.6))
        if different(p.indent_in,indent,out.param('BULLET_INDENT','tolerance_in',0.05)):
            out.add('BULLET_INDENT',slide,e,_in(indent),_in(p.indent_in),'A bullet is indented differently from the required outline level.')
        elif p.align in {'ctr','r','just','dist'}:
            out.add('BULLET_INDENT',slide,e,'Left-aligned',ALIGN[p.align],'Bulleted text is not left-aligned.')
        size_now=max([r.size_pt for r in p.runs if r.size_pt] or [size or 16])
        # Exact leading in points is single spacing only when it matches the font's natural line height.
        exact=p.line_spacing_pt is not None and abs(p.line_spacing_pt/(1.2*size_now)-1)>0.05
        before=level.space_before_pt if level and level.space_before_pt is not None else (6 if p.level==0 else 0)
        # The gap between bullets can be set as space after the previous bullet instead of space before this one.
        gap=(p.space_before_pt or 0)+(previous.space_after_pt or 0 if previous is not None else 0) if p.space_before_pt is not None else None
        if (different(p.space_before_pt,before,0.1) and different(gap,before,0.1)) or different(p.line_spacing,1.0,0.01) or exact:
            leading=f'exactly {_pt(p.line_spacing_pt)} pt' if exact else f'{_pt(round(p.line_spacing,2)) if isinstance(p.line_spacing,(int,float)) else p.line_spacing}× line spacing'
            out.add('BULLET_SPACING',slide,e,f'{_pt(before)} pt before, single line spacing',f'{_pt(p.space_before_pt)} pt before, {leading}','Bullet spacing differs from the required outline style.')
        if TERMINAL.search(p.text.strip()):out.add('BULLET_PUNCTUATION',slide,e,'No terminal punctuation',p.text,'A bullet ends with punctuation.')
        previous=p


def _tables(deck: Deck, slide: Slide,e: Element,out: Reporter,client: str | None):
    table=e.table
    if table is None or e.role=='key_item' or table.n_rows<2 or table.n_cols<2:return
    cells=[c for c in table.cells if not c.is_spanned and c.text.strip()]
    headers=[c for c in cells if c.row<table.header_rows]
    body=[c for c in cells if c.row>=table.header_rows]
    first={c.row:c for c in cells if c.col==0}
    minimum=out.param('TABLE_FONT','min_pt',10);maximum=out.param('TABLE_FONT','max_pt',12)
    font=out.param('TABLE_FONT','font','Calibri')
    bad=[r for c in cells for r in cell_runs(c) if (r.font and r.font.casefold()!=font.casefold() and not r.font.casefold().startswith(('wingdings','symbol','webdings'))) or (r.size_pt is not None and not minimum<=r.size_pt<=maximum)]
    # "Consistent across the table": one size for header and body, markers aside.
    sizes=sorted({round(r.size_pt*2)/2 for c in cells for r in cell_runs(c) if r.size_pt is not None and r.text.strip() and not r.superscript})
    if bad or len(sizes)>1:
        problems=[', '.join(sorted({f'{r.font} {_pt(r.size_pt)} pt' for r in bad}))] if bad else []
        if len(sizes)>1:problems.append('mixed sizes '+', '.join(f'{_pt(s)} pt' for s in sizes))
        out.add('TABLE_FONT',slide,e,f'{font}, {_pt(minimum)}–{_pt(maximum)} pt, one size throughout','; '.join(problems),'Table text differs from the required typography.')
    header_fill=out.param('TABLE_HEADER_STYLE','fill','006EBE')
    wrong=[c for c in headers if different(c.fill,header_fill) or different(c.anchor,'b') or any(different(r.bold,True) or different(r.color,'FFFFFF') for r in cell_runs(c))]
    if wrong:out.add('TABLE_HEADER_STYLE',slide,e,f'Bold white text, #{header_fill} fill, bottom aligned',f'{len(wrong)} header cells differ','Table header cells differ from the required style.')
    header_sizes=[r.size_pt for c in headers for r in cell_runs(c) if r.size_pt is not None]
    if header_sizes and max(header_sizes)-min(header_sizes)>0.1:
        out.add('TABLE_HEADER_SIZE_INCONSISTENT',slide,e,'Consistent header font size',f'{_pt(min(header_sizes))}–{_pt(max(header_sizes))} pt','Table headers use inconsistent font sizes.')
    for row,c in first.items():
        if row<table.header_rows:continue
        rowcells=[v for v in body if v.row==row]
        # Fills are resolved through the table style, so a cell with none recorded has no fill.
        fills=[v.fill or 'FFFFFF' for v in rowcells]
        if STAT.match(c.text.strip()):
            expected=out.param('TABLE_STATS_ROW','fill','FFFFDB')
            if any(different(f,expected) for f in fills):out.add('TABLE_STATS_ROW',slide,e,'#'+expected,c.text+': '+', '.join('#'+f for f in sorted(set(fills))),'A summary statistics row has the wrong fill colour.')
        elif client and len(rowcells)>1 and c.grid_span==1 and re.fullmatch(re.escape(client)+r'(?:\s+.*)?',c.text.strip(),re.I):
            expected=out.param('TABLE_CLIENT_ROW','fill','F1FFE1')
            if any(different(f,expected) for f in fills):out.add('TABLE_CLIENT_ROW',slide,e,'#'+expected,c.text+': '+', '.join('#'+f for f in sorted(set(fills))),'The client row has the wrong fill colour.')
    # Preserve row parity even when an unfilled/unknown cell has no colour.
    # Skipping those rows turns a correct no-fill/grey pattern into false alarms.
    ordinary=[]
    for row in range(table.header_rows,table.n_rows):
        cs=[c for c in body if c.row==row]
        name=first.get(row)
        if not cs or not name or STAT.match(name.text.strip()) or (client and name.text.strip().casefold().startswith(client.casefold())):continue
        # Section divider rows reset the pattern; inspect known neutral fills only.
        if name.grid_span>1:
            ordinary=[]
            continue
        ordinary.append((row,cs))
    if len(ordinary)>=3:
        expected_fill=out.param('TABLE_ZEBRA','fill','F2F2F2')
        # A table with no body fills at all is plainly unstriped. Where some cells carry colour
        # (a keyed highlight, a shaded label column), unfilled cells stay unknown, as before.
        plain=not any(c.fill for _,cs in ordinary for c in cs)
        wrong=[]
        for i,(row,cs) in enumerate(ordinary):
            expected='FFFFFF' if i%2==0 else expected_fill
            fills=[(c.fill or 'FFFFFF') if plain else c.fill for c in cs if c.row_span==1]
            neutral=[f for f in fills if f in {'FFFFFF',expected_fill}]
            if neutral and any(fill!=expected for fill in neutral):wrong.append(row)
        if wrong:out.add('TABLE_ZEBRA',slide,e,f'Alternating #FFFFFF / #{expected_fill}',f'Rows {[r+1 for r in wrong]}','Ordinary table rows do not follow the alternating fill pattern.')
    margin=out.param('TABLE_CELL_MARGIN','margin_in',0.03)
    bad_margin=[c for c in body if table.n_rows>=3 and table.n_cols>=3 and c.col==0 and different(c.margin_left_in,margin,out.param('TABLE_CELL_MARGIN','tolerance_in',0.01))]
    if bad_margin:out.add('TABLE_CELL_MARGIN',slide,e,f'{margin} in',f'{len(bad_margin)} first-column cells differ','First-column cell margins differ from the annotated table standard.')
    border=[c for c in cells if (c.col==0 and c.border_left) or (c.col+c.grid_span==table.n_cols and c.border_right)]
    if border:out.add('TABLE_BORDERS',slide,e,'No outer left/right borders',f'{len(border)} cells with outer borders','The table has an outer left or right border.')
    merged=[c for c in body if c.row_span>1 and c.col>0]
    if merged:out.add('TABLE_MERGED_CELL',slide,e,'Separate explanations unless merging is intentional',f'{len(merged)} vertically merged body cells','A body cell spans multiple rows and may combine distinct explanations.',confidence=0.75)
    for c in headers:
        if re.search(r'\bIncumbent\b',c.text,re.I):out.add('TERM_INCUMBENT',slide,e,'Executive',c.text,'A table header uses “Incumbent” instead of “Executive”.')
    formats=[];percent_places={}
    for col in range(table.n_cols):
        values=[c.text.strip() for c in body if c.col==col]
        nums=[re.fullmatch(r'\(?([£$€]?)[+-]?\d[\d,]*(?:\.(\d+))?(%?)\)?',v) for v in values]
        nums=[m for m in nums if m]
        if len(nums)>=3 and len({(len(m.group(2) or ''),m.group(1),m.group(3)) for m in nums})>1:
            formats.append(f'Column {col+1}: mixed number formats')
        elif len(nums)>=3 and all(m.group(3) for m in nums):
            percent_places[col]=len(nums[0].group(2) or '')
    # Percentages share one precision across the table; other units may measure different things.
    if len(set(percent_places.values()))>1:
        formats.append('Percentages: '+', '.join(f'column {c+1} {p} dp' for c,p in percent_places.items()))
    if formats:
        out.add('TABLE_NUMBER_FORMAT',slide,e,'Consistent precision within the table and units within each column','; '.join(formats),'Numbers in the table mix decimal precision or unit symbols.')
    # "Use en-dashes or blanks for not-applicable or missing values."
    missing=[c for c in body if MISSING_VALUE.fullmatch(c.text.strip())]
    if missing:out.add('TABLE_MISSING_VALUE',slide,e,'An en-dash (–) or a blank cell',f"{', '.join(sorted({'“'+c.text.strip()+'”' for c in missing}))} in {len(missing)} cell{'s' if len(missing)>1 else ''}",'Missing values are written as placeholders instead of an en-dash or a blank.')
    money_headers=[c.text for c in headers if re.search(r'\b(?:revenue|salary|compensation|market\s*cap)\b',c.text,re.I)]
    header_text=' '.join(c.text for c in headers)
    if money_headers and not re.search(r'[$£€]|\b(?:USD|GBP|EUR|MMs?|000s|billions?|millions?)\b',header_text,re.I):
        out.add('TABLE_UNITS_MISSING',slide,e,'Currency and scale in table headers, e.g. ($MM)','No currency or scale in the headers','A financial table may be missing its currency or scale.',confidence=0.75)


def _captions(slide: Slide,e: Element,out: Reporter):
    if e.role!='table_caption':return
    bad=_font_mismatches(runs(e),font='Calibri',size=out.param('TABLE_CAPTION','size_pt',14),bold=True)
    candidates=[t for t in slide.elements if t.table and t.table.n_rows>=3 and t.table.n_cols>=3 and t.bbox.y>=e.bbox.y+e.bbox.h-0.05*EMU_PER_INCH and min(t.bbox.x+t.bbox.w,e.bbox.x+e.bbox.w)>max(t.bbox.x,e.bbox.x)]
    target=min(candidates,key=lambda t:t.bbox.y-(e.bbox.y+e.bbox.h)) if candidates else None
    if target is None or e.bbox.w < 0.5*target.bbox.w:
        return
    delta=abs((e.bbox.x+e.bbox.w/2)-(target.bbox.x+target.bbox.w/2)) if target else 0
    align=[p.align for p in e.paragraphs if p.text.strip() and p.align is not None]
    if bad or any(a!='ctr' for a in align) or delta>out.param('TABLE_CAPTION','tolerance_in',0.05)*EMU_PER_INCH:
        out.add('TABLE_CAPTION',slide,e,'Calibri bold 14 pt, centred above table','; '.join(bad) or '; '.join(x for x in (', '.join(sorted({ALIGN.get(a,a) for a in align if a!='ctr'})), f'{_in(delta/EMU_PER_INCH)} off-centre' if delta>out.param('TABLE_CAPTION','tolerance_in',0.05)*EMU_PER_INCH else '') if x),'The table caption differs from the required typography or centring.')


def _footnotes(deck: Deck,slide: Slide,out: Reporter):
    entries=set()
    unresolved_numbering=False
    for e in slide.elements:
        if e.role=='footnote':
            if any(p.bullet=='other' and not p.bullet_char for p in e.paragraphs):
                unresolved_numbering=True
            entries.update(re.findall(r'(?:^|\n)\s*\(?(\d+)\)[ .]?',e.text))
            entries.update(re.findall(r'(?:^|\n)\s*(\d+)\.\s',e.text))
            if e.text.lstrip().startswith('*'):entries.add('*')
            # Symbol and letter notes may share a line: "* One-time award; † prorated; (a) interim".
            entries.update(re.findall(r'(?:^|[\n;,]|\s)([*†‡§])\s*(?=\w)',e.text))
            entries.update(re.findall(r'(?:^|[\n;,]|\s)\(([a-z])\)\s',e.text))
        if e.role in {'footnote','source_note'}:
            rule='FOOTNOTE_STYLE' if e.role=='footnote' else 'SOURCE_NOTE_STYLE'
            size=out.param(rule,'size_pt',10 if e.role=='footnote' else 9)
            bad=_font_mismatches(runs(e),font=deck.master.theme_minor_font or 'Calibri',size=size,italic=True)
            if bad:out.add(rule,slide,e,f'Calibri italic {_pt(size)} pt','; '.join(bad),'The note typography differs from its required style.')
    for e in slide.elements:
        if e.role in {'footnote','source_note','slide_number','confidentiality'}:continue
        reference_runs=runs(e)+([r for c in e.table.cells for r in cell_runs(c)] if e.table else [])
        markers={r.text.strip('() ') for i,r in enumerate(reference_runs) if r.superscript and re.fullmatch(r'\(?\d+\)?|[*†‡§]|\([a-z]\)',r.text.strip())
                 and not (r.text.strip() in {'2','3'} and i and UNIT.search(reference_runs[i-1].text))}
        # Explicit parenthesised references only; numeric values and powers are not references.
        markers.update(re.findall(r'(?<=[A-Za-z])\((\d+)\)',e.text))
        missing=sorted(m for m in markers-entries if not (unresolved_numbering and m.isdigit()))
        if missing:out.add('FOOTNOTE_UNMATCHED',slide,e,'Matching footnote on this slide',', '.join(missing),'A footnote reference has no matching entry on the same slide.',confidence=0.85)


def _colour(slide: Slide,e: Element,out: Reporter,palette: set[str]):
    # Pictures and externally rendered artwork cannot be colour-audited from the file.
    # Native charts are audited by the series and point colours they set explicitly.
    if e.kind in {'picture','group'}:return
    values=set(e.extra_colors) if e.kind=='chart' else {c for c in (e.fill,e.line_color,*e.extra_colors,*[r.color for r in runs(e)]) if c}
    if e.table:
        values.update(v for c in e.table.cells for v in (c.fill,c.border_left,c.border_right,c.border_top,c.border_bottom,*[r.color for r in cell_runs(c)]) if v)
    unknown=sorted(c for c in values if re.fullmatch('[0-9A-F]{6}',c) and c not in palette)
    if not unknown:return
    def distance(a,b):return sum((int(a[i:i+2],16)-int(b[i:i+2],16))**2 for i in (0,2,4))
    c=unknown[0];near=min(palette,key=lambda p:distance(c,p))
    out.add('COLOR_OFF_PALETTE',slide,e,'ACME palette colours',', '.join('#'+v for v in unknown),f'Colour #{c} is outside the ACME palette; its nearest palette colour is #{near}.')


def _title_overlap(slide: Slide,out: Reporter):
    """'Clear separation between slide title and body': body content must not start inside the headline box."""
    title=next((t for t in slide.elements if t.role=='title'),None)
    if slide.layout_kind!='content' or title is None:return
    slack=out.param('TITLE_BODY_OVERLAP','tolerance_in',0.25)*EMU_PER_INCH
    # The headline box is often taller than its text; measure to where the text ends.
    lines,_=estimated_lines(title)
    size=max([r.size_pt for r in runs(title) if r.size_pt] or [24])
    text_h=min(title.bbox.h,int(lines*size*1.2/72*EMU_PER_INCH))
    anchor=title.vertical_anchor or 'ctr'
    top=title.bbox.y+(0 if anchor=='t' else title.bbox.h-text_h if anchor=='b' else (title.bbox.h-text_h)//2)
    bottom=top+text_h
    for e in slide.elements:
        if e.role in {'title','slide_number','confidentiality','key_item'} or not (e.text.strip() or e.table):continue
        across=min(e.bbox.x+e.bbox.w,title.bbox.x+title.bbox.w)-max(e.bbox.x,title.bbox.x)
        if e.bbox.y>title.bbox.y and e.bbox.y<bottom-slack and e.bbox.y+e.bbox.h>bottom and across>0:
            out.add('TITLE_BODY_OVERLAP',slide,e,'Body content starts below the headline box',f'Starts {_in((bottom-e.bbox.y)/EMU_PER_INCH)} inside the headline box','Body content overlaps the headline, so the two are not clearly separated.')


def _shape_text(slide: Slide,out: Reporter,minor_font: str | None):
    """Collections of shapes use the body font. Size consistency stays a by-eye check:
    header bars and content boxes in one diagram legitimately differ."""
    # A shape is a filled or outlined box with text; plain text boxes are not part of a shape collection.
    shapes=[e for e in slide.elements if e.kind in {'autoshape','textbox'} and e.text.strip() and (e.fill or e.line_color) and e.role in {'shape','other','callout'}]
    font=minor_font or out.param('SHAPE_FONT','font','Calibri')
    for e in shapes:
        wrong=sorted({r.font for r in runs(e) if r.text.strip() and r.font and r.font.casefold()!=font.casefold()
                      and not r.font.casefold().startswith(('wingdings','symbol','webdings'))})
        if wrong:
            out.add('SHAPE_FONT',slide,e,font,', '.join(wrong),'Shape text is not in the body font.')


def check_slides(deck: Deck,out: Reporter):
    from app.rules.consistency import infer_client
    client=infer_client(deck)
    palette=set(yaml.safe_load((CONFIG_DIR/'palette.yaml').read_text())['colors'])
    deck=conform_master(deck,out)
    for slide in deck.slides:
        _confidentiality(deck,slide,out)
        _titles(deck,slide,out)
        _title_overlap(slide,out)
        _shape_text(slide,out,deck.master.theme_minor_font)
        _footnotes(deck,slide,out)
        for e in slide.elements:
            _bullets(deck,slide,e,out);_tables(deck,slide,e,out,client);_captions(slide,e,out);_colour(slide,e,out,palette)
            if e.table:
                percentile=next((c.text for c in e.table.cells if re.search(r'\bpercentile\b',c.text,re.I)),None)
                if percentile:out.add('TERM_PERCENTILE',slide,e,'%ile',percentile,'The table spells out “Percentile” instead of using “%ile”.')
            texts=[e.text]+([c.text for c in e.table.cells if not c.is_spanned] if e.table else [])
            initials=sorted({m.group(0) for t in texts for m in INITIALS.finditer(t)})
            middles=sorted(set(_bare_middle_initials(e)))
            if initials or middles:
                affected = [c for c in e.table.cells if not c.is_spanned and (INITIALS.search(c.text) or any(n in c.text for n in middles))] if e.table else []
                bounds = enclosing_bbox((c.bbox for c in affected), e.bbox) if affected and all(c.bbox is not None for c in affected) else e.bbox
                expected=[i+'.' for i in initials]+[MIDDLE_INITIAL.sub(r'\1 \2. \3',n) for n in middles]
                out.add('INITIALS_PUNCTUATION',slide,e,', '.join(expected),', '.join(initials+middles),'Initials are missing their final full stop.',bbox=bounds)
            if re.search(r'\bTGT\b',URL.sub('',e.text if not e.table else '\n'.join(c.text for c in e.table.cells if not c.is_spanned and not _ticker_cell(e.table,c))),re.I):
                out.add('TERM_TGT',slide,e,'Target',e.text[:150],'Text abbreviates “Target” as “TGT”.')
            if COMMENT.search(e.text):out.add('LEFTOVER_COMMENT',slide,e,'Client-ready text without internal comments',e.text[:200],'Text appears to contain an unresolved editorial comment.',confidence=0.9)
            title=next((t for t in slide.elements if t.role=='title'),None)
            invisible_text_container = (e.kind in {'placeholder', 'textbox'}
                                        and not e.text.strip() and not e.fill and not e.line_color)
            if slide.layout_kind == 'content' and title and not invisible_text_container and e.role not in {'title','slide_number','confidentiality'}:
                left=out.param('MARGIN_EDGE','min_left_in',1)*EMU_PER_INCH
                if e.bbox.y>title.bbox.y+title.bbox.h:
                    # Say which written edge is crossed, in the reviewer's terms.
                    # A 0.05 in tolerance keeps measurement noise from becoming a flag.
                    slack=0.05*EMU_PER_INCH
                    right=e.bbox.x+e.bbox.w-(title.bbox.x+title.bbox.w)
                    if e.bbox.x<0:
                        actual=f'runs {_in(-e.bbox.x/EMU_PER_INCH)} off the left edge of the slide'
                    elif e.bbox.x<left-slack:
                        actual=f'{_in(e.bbox.x/EMU_PER_INCH)} from the left edge (under 1 in)'
                    elif e.bbox.x<title.bbox.x-slack:
                        actual=f'starts {_in((title.bbox.x-e.bbox.x)/EMU_PER_INCH)} left of the headline'
                    elif right>slack:
                        actual=f'extends {_in(right/EMU_PER_INCH)} past the headline\'s right edge'
                    else:
                        actual=None
                    if actual:
                        out.add('MARGIN_EDGE',slide,e,'At least 1 in from the left edge and within the headline\'s edges',actual,'Content sits outside the slide margins.')
