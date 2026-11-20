"""Blank authoring placeholders are not visible slide-content violations."""
import pytest
from app.model.types import BBox, Deck, Element, MasterSpec, Slide
from app.rules import run_rules


@pytest.mark.parametrize('kind,text,fill,line,expected',[
    ('placeholder','',None,None,False),
    ('textbox','   ',None,None,False),
    ('placeholder','Visible content',None,None,True),
    ('placeholder','','FF0000',None,True),
    ('textbox','',None,'000000',True),
    ('picture','',None,None,True),
    ('chart','',None,None,True),
])
def test_margin_checks_visible_content_without_flagging_blank_placeholders(kind,text,fill,line,expected):
    from app.model.types import EMU_PER_INCH as U
    title=Element(id='s1-1',slide=1,shape_id=1,name='title',kind='placeholder',role='title',
                  bbox=BBox(x=U//2,y=U//2,w=8*U,h=U//2),text='Quarterly results improved')
    content=Element(id='s1-2',slide=1,shape_id=2,name='content',kind=kind,role='body',
                    bbox=BBox(x=3*U//4,y=2*U,w=3*U,h=2*U),text=text,fill=fill,line_color=line)
    deck=Deck(filename='visibility.pptx',slide_width=10*U,slide_height=7*U,
              master=MasterSpec(),slides=[Slide(index=1,layout_name='Custom',layout_kind='content',elements=[title,content])])
    findings=run_rules(deck,{'MARGIN_EDGE':{'enabled':True}})
    matches=[f for f in findings if f.rule_id=='MARGIN_EDGE' and f.element_id==content.id]
    assert bool(matches) is expected
