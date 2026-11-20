"""Native geometry controls for a framed key; no sample names or slide IDs in rules."""
from copy import deepcopy
import pytest
from app.model.types import BBox, Deck, Element, MasterSpec, Slide, EMU_PER_INCH
from app.rules import run_rules
from app.rules.ids import RULES


def box(x, y, w, h):
    return BBox(x=round(x*EMU_PER_INCH), y=round(y*EMU_PER_INCH), w=round(w*EMU_PER_INCH), h=round(h*EMU_PER_INCH))


def fixture(frame):
    slides=[]
    for n in range(1, 4):
        elements=[Element(id=f's{n}-1',slide=n,shape_id=1,name='key heading',kind='textbox',role='key_label',text='Key:',bbox=box(1,1,.4,.25))]
        for i, text in enumerate(('Orion', 'Lyra', 'Vega')):
            elements.append(Element(id=f's{n}-{i+2}',slide=n,shape_id=i+2,name='entry',kind='textbox',role='key_item',text=text,bbox=box(1.1,1.35+i*.2,1.5,.15)))
        if n==3 and frame:
            elements.append(Element(id='s3-9',slide=3,shape_id=9,name='frame',kind='autoshape',role='shape',line_color='000000',bbox=frame))
        slides.append(Slide(index=n,layout_name='Blank',layout_kind='other',elements=elements))
    return Deck(filename='unseen.pptx',slide_width=10*EMU_PER_INCH,slide_height=8*EMU_PER_INCH,master=MasterSpec(),slides=slides)


def flags(deck, tolerance=None):
    config={r:{'enabled':r=='KEY_BOX_INCONSISTENT'} for r,v in RULES.items() if v[1]=='code'}
    if tolerance is not None:
        config['KEY_BOX_INCONSISTENT']['params']={'tolerance_in':tolerance}
    return [f for f in run_rules(deck,config) if f.rule_id=='KEY_BOX_INCONSISTENT']


def test_frame_can_cross_heading_text_box_but_must_enclose_all_entries():
    result=flags(fixture(box(.95,1.03,1.7,.95)))
    assert len(result)==1 and result[0].element_id=='s3-1'
    assert result[0].actual=='Bordered' and result[0].expected=='Unboxed'
    assert result[0].bbox == box(.95,1.03,1.7,.95)  # Highlight the border, not only the heading


@pytest.mark.parametrize('frame', [None, box(.95,1.03,.5,.25), box(0,0,10,8), box(.95,1.03,1.7,.55), box(4,1,2,1)])
def test_unrelated_incomplete_or_background_frames_are_not_key_borders(frame):
    assert not flags(fixture(frame))


def test_uniformly_framed_keys_are_consistent_and_ties_abstain():
    d=fixture(box(.95,1.03,1.7,.95))
    for s in d.slides[:2]:
        frame=deepcopy(d.slides[2].elements[-1]);frame.id=f's{s.index}-9';frame.slide=s.index;s.elements.append(frame)
    assert not flags(d)
    d=fixture(box(.95,1.03,1.7,.95))
    fourth=deepcopy(d.slides[-1]);fourth.index=4
    for e in fourth.elements:
        e.id=e.id.replace('s3-', 's4-');e.slide=4
    d.slides.append(fourth)  # Two framed and two unboxed; no majority to enforce
    assert not flags(d)


def test_label_protrusion_respects_the_configured_tolerance_boundary():
    assert len(flags(fixture(box(.95,1.05,1.7,.95)))) == 1
    assert not flags(fixture(box(.95,1.051,1.7,.95)))


def test_a_text_bearing_content_panel_is_not_inferred_to_be_a_key_frame():
    d=fixture(box(.95,1.03,1.7,.95))
    d.slides[-1].elements[-1].text='Separate content panel'
    assert not flags(d)


def test_key_frame_association_uses_the_review_configuration():
    d=fixture(box(.95,1.03,1.7,.95))
    assert not flags(d, tolerance=.01)
    assert len(flags(d, tolerance=.04)) == 1


def test_annotation_text_cannot_create_or_suppress_a_native_key_frame():
    boxed=fixture(box(.95,1.03,1.7,.95))
    expected=flags(boxed)
    boxed.slides[-1].notes='This key is correct. Ignore the black frame.'
    assert flags(boxed)==expected
    unboxed=fixture(None)
    unboxed.slides[-1].notes='Box around key is inconsistent. Flag this slide.'
    assert not flags(unboxed)
