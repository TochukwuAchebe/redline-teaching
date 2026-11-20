"""Behavioural rule tests use synthetic contracts, never sample-specific values."""
from copy import deepcopy

import pytest

from app.model.types import (BBox, BodyLevelSpec, Cell, Deck, Element, MasterSpec,
                            Paragraph, Run, Slide, Table, EMU_PER_INCH)
from app.rules import run_rules
from app.rules.ids import RULES


def element(role='body',text='Text',**kwargs):
    defaults=dict(id='s1-1',slide=1,shape_id=1,name='shape',kind='textbox',role=role,
                  bbox=BBox(x=EMU_PER_INCH,y=EMU_PER_INCH,w=8*EMU_PER_INCH,h=EMU_PER_INCH//2),text=text,
                  paragraphs=[Paragraph(text=text,runs=[Run(text=text,font='Calibri',size_pt=16)])])
    defaults.update(kwargs)
    return Element(**defaults)


def deck(elements=None,kind='content',master=None):
    return Deck(filename='synthetic.pptx',slide_width=10*EMU_PER_INCH,slide_height=8*EMU_PER_INCH,
                master=master or MasterSpec(),slides=[Slide(index=1,layout_name='Content',layout_kind=kind,elements=elements or [])])


def check(d,rule):
    cfg={r:{'enabled':r==rule} for r,v in RULES.items() if v[1]=='code'}
    return run_rules(d,cfg)


def styled_run(text='Text',size=11,**kw):
    return Run(text=text,font='Calibri',size_pt=size,bold=False,italic=False,color='000000',**kw)


def data_table():
    cs=[]
    for row in range(4):
        for col in range(3):
            t=('Company','Revenue ($MM)','Ratio')[col] if row==0 else (f'Firm {row}' if col==0 else str(row))
            rs=Run(text=t,font='Calibri',size_pt=11,bold=row==0,color='FFFFFF' if row==0 else '000000')
            cs.append(Cell(row=row,col=col,text=t,paragraphs=[Paragraph(text=t,runs=[rs])],
                           fill='006EBE' if row==0 else ('FFFFFF' if row%2 else 'F2F2F2'),
                           anchor='b' if row==0 else 't',margin_left_in=0.03))
    return element('table','',kind='table',table=Table(n_rows=4,n_cols=3,cells=cs),paragraphs=[])


def case(rule,bad):
    # Each pair differs in the observed property relevant to the rule.
    e=element();d=deck([e])
    if rule=='TITLE_BODY_OVERLAP':
        title=element('title','Headline',id='s1-2',vertical_anchor='t',bbox=BBox(x=EMU_PER_INCH,y=0,w=8*EMU_PER_INCH,h=EMU_PER_INCH),
                      paragraphs=[Paragraph(text='Headline',runs=[Run(text='Headline',font='Calibri',size_pt=24,bold=True)])])
        e.bbox.y=int(0.05*EMU_PER_INCH) if bad else EMU_PER_INCH
        d.slides[0].elements.append(title)
        return d
    if rule=='INITIALS_PUNCTUATION':
        e.text='Acquired by C.R Bard in 2017' if bad else 'Acquired by C.R. Bard; U.S. sales'
        return d
    if rule=='TEMPLATE_NONCONFORMING':
        m=d.master;m.title_font='Calibri';m.title_size_pt=44 if bad else 24;m.title_bold=True
        m.confidentiality_text='Confidential exact text'
        return d
    if rule.startswith('CONF_'):
        e.role='confidentiality';e.text='Confidential exact text';e.paragraphs=[Paragraph(text=e.text,runs=[Run(text=e.text,font='Calibri',size_pt=8)])]
        d.master.confidentiality_text=e.text;d.master.confidentiality_bbox=e.bbox.model_copy()
        if rule=='CONF_MISSING' and bad:d.slides[0].elements=[]
        if rule=='CONF_TEXT_MODIFIED' and bad:e.text+=' altered'
        if rule=='CONF_MOVED' and bad:e.bbox.x+=EMU_PER_INCH
        if rule=='CONF_STYLE' and bad:e.paragraphs[0].runs[0].size_pt=12
        if rule=='CONF_OVERLAP':
            other=element(text='Source text',id='s1-2');other.bbox.y=e.bbox.y if bad else e.bbox.y+e.bbox.h
            d.slides[0].elements.append(other)
    elif rule.startswith('TITLESLIDE_'):
        d.slides[0].layout_kind='title';e.role='title';e.text='Annual review';e.paragraphs[0].runs[0].size_pt=24
        d.slides[0].elements.extend([element('subtitle','May 11, 2026',id='s1-2'),element('authors','Alex, Partner',id='s1-3')])
        if bad:
            if rule=='TITLESLIDE_DRAFT':d.slides[0].elements[1].text+=' DRAFT'
            if rule=='TITLESLIDE_DATE':d.slides[0].elements[1].text='February 31, 2026'
            if rule=='TITLESLIDE_AUTHORS':d.slides[0].elements.pop()
            if rule=='TITLESLIDE_FONT':e.paragraphs[0].runs[0].font='Arial'
    elif rule.startswith('TITLE_'):
        e.role='title';e.text='Revenue improved';e.paragraphs[0].runs[0].size_pt=24;e.paragraphs[0].runs[0].bold=True
        e.vertical_anchor='ctr'
        d.master.title_bbox_by_layout['Content']=e.bbox.model_copy()
        if bad:
            if rule=='TITLE_FONT':e.paragraphs[0].runs[0].font='Arial'
            if rule=='TITLE_VERTICAL_ALIGNMENT':e.vertical_anchor='b'
            if rule=='TITLE_POSITION':e.bbox.x+=EMU_PER_INCH
            if rule=='TITLE_TOO_LONG':e.text='First\nSecond\nThird\nFourth'
            if rule=='TITLE_PUNCTUATION':e.text+=':'
            if rule=='TITLE_TOPIC_ONLY':e.text='Revenue overview'
    elif rule.startswith('BULLET_'):
        p=e.paragraphs[0];p.bullet='square';p.indent_in=0;p.space_before_pt=6;p.line_spacing=1
        d.master.body_levels=[BodyLevelSpec(level=0,bullet='square',bullet_char='▪',size_pt=16,indent_in=0,space_before_pt=6)]
        if bad:
            if rule=='BULLET_FONT':p.runs[0].size_pt=12
            if rule=='BULLET_GLYPH':p.bullet='circle'
            if rule=='BULLET_INDENT':p.indent_in=0.5
            if rule=='BULLET_SPACING':p.line_spacing=1.5
            if rule=='BULLET_PUNCTUATION':p.text+='.'
    elif rule.startswith('TABLE_') or rule in {'TERM_PERCENTILE','TERM_INCUMBENT'}:
        e=data_table();d=deck([e]);cs=e.table.cells
        if rule=='TABLE_STATS_ROW':
            cs[3].text='Median'
            for c in cs[3:6]:c.fill='F1FFE1' if bad else 'FFFFDB'
        elif rule=='TABLE_CLIENT_ROW':
            cs[3].text='Orion'
            for c in cs[3:6]:c.fill='FFFFDB' if bad else 'F1FFE1'
            for i in (2,3):
                s=deepcopy(d.slides[0]);s.index=i;s.elements=[element('title',"Orion’s revenue increased",id=f's{i}-2',slide=i)];d.slides.append(s)
        elif rule=='TABLE_CAPTION':
            caption=element('table_caption','Data table',id='s1-2',bbox=BBox(x=e.bbox.x,y=0,w=e.bbox.w,h=EMU_PER_INCH//2))
            caption.paragraphs=[Paragraph(text=caption.text,align='l' if bad else 'ctr',runs=[Run(text=caption.text,font='Calibri',size_pt=14,bold=True)])]
            d.slides[0].elements.append(caption)
        elif bad:
            if rule=='TABLE_FONT':cs[3].paragraphs[0].runs[0].size_pt=8
            if rule=='TABLE_HEADER_STYLE':cs[0].fill='FF3737'
            if rule=='TABLE_HEADER_SIZE_INCONSISTENT':cs[0].paragraphs[0].runs[0].size_pt=8
            if rule=='TABLE_ZEBRA':
                for c in cs[6:9]:c.fill='FFFFFF'
            if rule=='TABLE_CELL_MARGIN':cs[3].margin_left_in=0.1
            if rule=='TABLE_BORDERS':cs[3].border_left='000000'
            if rule=='TABLE_NUMBER_FORMAT':cs[4].text='1.00'
            if rule=='TABLE_MISSING_VALUE':cs[4].text='N/A'
            if rule=='TABLE_UNITS_MISSING':cs[1].text='Revenue'
            if rule=='TABLE_MERGED_CELL':cs[4].row_span=2
            if rule=='TERM_PERCENTILE':cs[3].text='75th Percentile'
            if rule=='TERM_INCUMBENT':cs[0].text='Incumbent'
    elif rule=='TERM_TGT':e.text='TGT bonus' if bad else 'Target bonus'
    elif rule=='LEFTOVER_COMMENT':e.text='AB question: confirm this' if bad else 'The analysis is complete'
    elif rule in {'FOOTNOTE_STYLE','SOURCE_NOTE_STYLE'}:
        e.role='footnote' if rule=='FOOTNOTE_STYLE' else 'source_note';size=10 if e.role=='footnote' else 9
        e.paragraphs[0].runs[0].size_pt=size+2 if bad else size;e.paragraphs[0].runs[0].italic=True
    elif rule=='FOOTNOTE_UNMATCHED':
        e.text='Growth(1)';e.paragraphs[0].runs=[Run(text='Growth'),Run(text='1',superscript=True)]
        if not bad:d.slides[0].elements.append(element('footnote','(1) Source',id='s1-2'))
    elif rule=='COLOR_OFF_PALETTE':e.fill='006CBD' if bad else '006EBE'
    elif rule=='MARGIN_EDGE':
        e.bbox.y=2*EMU_PER_INCH;e.bbox.x=0 if bad else EMU_PER_INCH
        d.slides[0].elements.append(element('title','Headline',id='s1-2'))
    elif rule=='SHAPE_FONT':
        e.role='shape';e.fill='006EBE';e.paragraphs[0].runs[0].font='Arial' if bad else 'Calibri'
    else:
        return consistency_case(rule,bad)
    return d


def consistency_case(rule,bad):
    slides=[]
    for i in range(1,4):
        label=element('key_label','Key:',id=f's{i}-1',slide=i)
        items=[element('key_item',t,id=f's{i}-{j+2}',slide=i,
                       bbox=BBox(x=EMU_PER_INCH,y=EMU_PER_INCH+(j+1)*EMU_PER_INCH//3,w=EMU_PER_INCH,h=EMU_PER_INCH//6)) for j,t in enumerate(['A','B','C'])]
        slides.append(Slide(index=i,layout_name='Content',layout_kind='content',elements=[label]+items))
    d=deck();d.slides=slides;target=slides[-1]
    if rule=='KEY_LABEL_INCONSISTENT' and bad:target.elements[0].text='Legend:'
    if rule=='KEY_ORDER_INCONSISTENT' and bad:target.elements[1].text,target.elements[2].text=target.elements[2].text,target.elements[1].text
    if rule=='KEY_SPACING' and bad:target.elements[-1].bbox.y+=EMU_PER_INCH//3
    if rule=='KEY_BOX_INCONSISTENT' and bad:target.elements[0].line_color='000000'
    if rule=='KEY_MISSING_LABEL':
        target.elements=[target.elements[0],element('key_item','',id='s3-9',slide=3,fill='FF3737',bbox=BBox(x=EMU_PER_INCH,y=EMU_PER_INCH,w=EMU_PER_INCH//5,h=EMU_PER_INCH//5))]
        if not bad:target.elements.append(element('key_item','Removal',id='s3-10',slide=3,bbox=BBox(x=int(1.25*EMU_PER_INCH),y=EMU_PER_INCH,w=EMU_PER_INCH,h=EMU_PER_INCH//5)))
    if rule=='CLIENT_NAME_INCONSISTENT':
        for s in slides:s.elements=[element('title',"Orion’s growth continues",id=f's{s.index}-1',slide=s.index)]
        t=data_table();t.table.cells[3].text='Orion Laboratories' if bad else 'Orion'
        t.table.cells[3].fill='F1FFE1';target.elements.append(t)
    if rule=='HEADER_TERM_INCONSISTENT':
        for s in slides:
            t=data_table();t.id=f's{s.index}-1';s.elements=[t]
        if bad:target.elements[0].table.cells[0].text='Company Name'
    if rule=='TERM_INCONSISTENT':
        for s in slides:s.elements=[element('body','Payouts under the annual incentive plan (AIP) are on target',id=f's{s.index}-1',slide=s.index)]
        if bad:target.elements[0].text='STIP payouts are on target'
    return d


CODE_RULES=[r for r,v in RULES.items() if v[1]=='code']


@pytest.mark.parametrize('rule',CODE_RULES)
def test_rule_positive_and_negative(rule):
    assert not check(case(rule,False),rule),f'{rule}: valid evidence flagged'
    result=check(case(rule,True),rule)
    assert result,f'{rule}: invalid evidence missed'
    assert all(f.expected and f.actual and f.message and f.rule_ref and f.origin=='code' for f in result)
    assert [f.id for f in result]==[f.id for f in check(case(rule,True),rule)]


def test_notes_cannot_affect_rules():
    d=case('TITLE_FONT',False)
    before=run_rules(d)
    d.slides[0].notes='DRAFT TODO question: Percentile TGT font Arial 8pt'
    assert run_rules(d)==before


def test_unknown_typography_is_not_noncompliance():
    d=case('TITLE_FONT',True)
    d.slides[0].elements[0].paragraphs[0].runs=[Run(text='Unknown')]
    assert not check(d,'TITLE_FONT')


def test_majority_tie_abstains():
    d=consistency_case('KEY_LABEL_INCONSISTENT',True)
    d.slides=d.slides[1:]
    assert not check(d,'KEY_LABEL_INCONSISTENT')


def test_config_disables_and_overrides_severity():
    d=case('TITLE_FONT',True)
    assert not check(d,'TERM_TGT')
    found=run_rules(d,{'TITLE_FONT':{'severity':'suggestion'}})
    assert next(f for f in found if f.rule_id=='TITLE_FONT').severity=='suggestion'


def test_margin_rule_is_on_and_advisory_by_default():
    found=[f for f in run_rules(case('MARGIN_EDGE',True)) if f.rule_id=='MARGIN_EDGE']
    assert found and all(f.severity=='suggestion' for f in found)
    assert 'from the left edge' in found[0].actual
    assert not any(f.rule_id=='MARGIN_EDGE' for f in run_rules(case('MARGIN_EDGE',False)))


def test_conforming_master_is_the_spec():
    d=case('TITLE_FONT',False);d.master.title_size_pt=24;d.master.confidentiality_text='Confidential exact text'
    assert not check(d,'TITLE_FONT') and not check(d,'TEMPLATE_NONCONFORMING')


def test_nonconforming_master_falls_back_to_guideline():
    # A deck on another template must not pass its own (wrong) master values.
    d=case('TITLE_FONT',False);d.master.title_size_pt=30
    d.slides[0].elements[0].paragraphs[0].runs[0].size_pt=30
    assert check(d,'TEMPLATE_NONCONFORMING')
    assert check(d,'TITLE_FONT')


def test_missing_master_statement_uses_guideline_wording():
    d=case('TITLE_FONT',False);d.master.confidentiality_text=None;d.master.confidentiality_bbox=None
    missing=check(d,'CONF_MISSING')
    assert missing and missing[0].bbox is not None
    text='These materials are confidential and to be used only by the client to whom they are addressed'
    footer=element(text=text,id='s1-9');footer.paragraphs=[Paragraph(text=text,runs=[Run(text=text,font='Calibri',size_pt=8)])]
    d.slides[0].elements.append(footer)
    assert not check(d,'CONF_MISSING')


def test_config_invalid_severity_rejected():
    with pytest.raises(ValueError):run_rules(deck(),{'TITLE_FONT':{'severity':'critical'}})


def test_no_fill_rows_keep_zebra_parity():
    d=case('TABLE_ZEBRA',False)
    for c in d.slides[0].elements[0].table.cells:
        if c.fill=='FFFFFF':c.fill=None
    assert not check(d,'TABLE_ZEBRA')


def test_symbol_font_does_not_make_table_font_invalid():
    d=case('TABLE_FONT',False)
    r=d.slides[0].elements[0].table.cells[3].paragraphs[0].runs[0]
    r.font='Wingdings 2';r.text='P'
    assert not check(d,'TABLE_FONT')


def test_parenthesised_industry_count_is_not_footnote_reference():
    d=deck([element(text='Research Services (4)')])
    assert not check(d,'FOOTNOTE_UNMATCHED')


def test_unknown_auto_numbering_abstains_from_unmatched_numeric_reference():
    d=case('FOOTNOTE_UNMATCHED',True)
    d.slides[0].elements.append(element('footnote','A numbered source',id='s1-2',paragraphs=[Paragraph(text='A numbered source',bullet='other')]))
    assert not check(d,'FOOTNOTE_UNMATCHED')


def test_table_cell_text_colours_are_audited():
    t=data_table();t.table.cells[3].paragraphs[0].runs[0].color='006CBD'
    found=check(deck([t]),'COLOR_OFF_PALETTE')
    assert found and '006CBD' in found[0].actual


def test_two_column_data_table_is_checked_but_margin_standard_is_not():
    t=data_table();t.table.n_cols=2;t.table.cells=[c for c in t.table.cells if c.col<2]
    t.table.cells[2].paragraphs[0].runs[0].size_pt=8
    t.table.cells[2].margin_left_in=0.5
    assert check(deck([t]),'TABLE_FONT')
    assert not check(deck([t]),'TABLE_CELL_MARGIN')


def test_table_cell_superscript_reference_requires_a_footnote():
    t=data_table();t.table.cells[3].paragraphs[0].runs.append(Run(text='9',superscript=True))
    found=check(deck([t]),'FOOTNOTE_UNMATCHED')
    assert found and found[0].actual=='9'


def test_bold_config_applies_when_master_unknown():
    d=case('TITLE_FONT',False);d.slides[0].elements[0].paragraphs[0].runs[0].bold=False
    cfg={r:{'enabled':r=='TITLE_FONT'} for r,v in RULES.items() if v[1]=='code'}
    cfg['TITLE_FONT']['params']={'bold':False}
    assert not run_rules(d,cfg)


def test_side_by_side_legends_own_their_nearest_items():
    from app.rules.consistency import _items
    a=element('key_label','Key',id='s1-1')
    b=element('key_label','Legend',id='s1-2');b.bbox.x+=5*EMU_PER_INCH;b.bbox.w=EMU_PER_INCH
    a.bbox.w=EMU_PER_INCH
    x=element('key_item','Alpha',id='s1-3');x.bbox.y+=EMU_PER_INCH//2;x.bbox.w=EMU_PER_INCH
    y=element('key_item','Beta',id='s1-4');y.bbox.x=b.bbox.x;y.bbox.y=x.bbox.y;y.bbox.w=EMU_PER_INCH
    s=deck([a,b,x,y]).slides[0]
    assert [e.id for e in _items(s,a)]==[x.id]
    assert [e.id for e in _items(s,b)]==[y.id]


@pytest.mark.parametrize('text',['Key question: should we expand?','Research question: what changed?','The question: what matters?'])
def test_legitimate_question_heading_is_not_an_editorial_comment(text):
    assert not check(deck([element(text=text)]),'LEFTOVER_COMMENT')


def test_modified_footer_prefix_is_reported_as_changed_not_missing():
    d=case('CONF_TEXT_MODIFIED',False);e=d.slides[0].elements[0]
    e.role='other';e.placeholder_type='FOOTER';e.text='Private materials for the intended recipient'
    assert check(d,'CONF_TEXT_MODIFIED')
    assert not check(d,'CONF_MISSING')


def test_legend_label_box_can_slightly_overlap_its_swatch():
    d=consistency_case('KEY_MISSING_LABEL',False)
    d.slides[-1].elements[-1].bbox.x=int(1.17*EMU_PER_INCH)
    assert not check(d,'KEY_MISSING_LABEL')


def test_rule_analysis_does_not_mutate_input_deck():
    d=case('TITLE_FONT',True);before=d.model_dump()
    run_rules(d)
    assert d.model_dump()==before


def test_spanning_client_named_section_is_not_a_client_data_row():
    d=case('TABLE_CLIENT_ROW',True)
    c=d.slides[0].elements[0].table.cells[3]
    c.text='Orion peers';c.grid_span=3
    assert not check(d,'TABLE_CLIENT_ROW')


def test_vertical_merged_cell_does_not_break_valid_zebra_rows():
    d=case('TABLE_ZEBRA',False)
    cells=d.slides[0].elements[0].table.cells
    cells[4].row_span=2;cells[4].fill='F2F2F2'
    assert not check(d,'TABLE_ZEBRA')


def test_unmerged_single_label_section_is_not_a_client_data_row():
    d=case('TABLE_CLIENT_ROW',True)
    for c in d.slides[0].elements[0].table.cells:
        if c.row==1 and c.col>0:c.text=''
    assert not check(d,'TABLE_CLIENT_ROW')


@pytest.mark.parametrize('kind,flagged', [('section', False), ('title', False), ('content', True)])
def test_only_content_headlines_must_be_bold(kind, flagged):
    """Title and section header slides specify Calibri 24 pt; content headlines add bold."""
    title=element('title','Annual review',paragraphs=[Paragraph(text='Annual review',runs=[Run(text='Annual review',font='Calibri',size_pt=24,bold=False)])])
    found=check(deck([title],kind=kind,master=MasterSpec(title_bold=True)),'TITLE_FONT')+check(deck([title],kind=kind,master=MasterSpec(title_bold=True)),'TITLESLIDE_FONT')
    assert bool(found)==flagged
