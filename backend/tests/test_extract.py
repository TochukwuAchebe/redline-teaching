"""Extraction contract, real-package regressions, and synthetic inheritance tests."""
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree as ET

from app.model.extract import Package, extract_deck, extract_table, _extract_shapes
from app.model.resolve import NS, Theme, normalize_bullet, resolve_paragraphs, shape_bbox

ROOT = Path(__file__).resolve().parents[2]


def xml(value):
    return ET.fromstring(value.encode())


def body(inner):
    return xml(f'<a:txBody xmlns:a="{NS["a"]}">{inner}</a:txBody>')


@pytest.mark.parametrize('char,font,expected', [('§','Wingdings','square'),('n','Wingdings','square'),
 ('n','Arial','other'),('Ø','Wingdings','arrow'),('Ø','Arial','other'),('∎',None,'square'),
 ('▪',None,'square'),('■',None,'square'),('–',None,'dash'),('➤',None,'arrow'),('●',None,'circle')])
def test_bullet_uses_font_and_character(char,font,expected):
    assert normalize_bullet(char,font)==expected


def test_properties_resolve_independently_with_false_overrides():
    b=body('<a:lstStyle><a:lvl1pPr><a:defRPr sz="1800" b="1"/></a:lvl1pPr></a:lstStyle>'
           '<a:p><a:pPr><a:defRPr i="1"/></a:pPr><a:r><a:rPr b="0"/><a:t>Visible</a:t></a:r></a:p>')
    master=xml(f'<p:bodyStyle xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><a:lvl1pPr><a:defRPr sz="1200"><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl1pPr></p:bodyStyle>')
    source={}
    run=resolve_paragraphs(b,[],master,Theme(minor='Calibri'),source)[0].runs[0]
    assert (run.size_pt,run.bold,run.italic,run.font)==(18.,False,True,'Calibri')
    assert source['size_pt']=='shape' and source['bold']=='run' and source['italic']=='paragraph'


def test_layout_properties_precede_master_and_explicit_no_bullet_stops_inheritance():
    b=body('<a:lstStyle/><a:p><a:pPr><a:buNone/></a:pPr><a:r><a:t>Test</a:t></a:r></a:p>')
    layout=body('<a:lstStyle><a:lvl1pPr marL="0"><a:buChar char="▪"/><a:defRPr sz="2200"/></a:lvl1pPr></a:lstStyle>')
    master=xml(f'<p:bodyStyle xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><a:lvl1pPr><a:defRPr sz="1600"/></a:lvl1pPr></p:bodyStyle>')
    source={};p=resolve_paragraphs(b,[(layout,'layout')],master,Theme(),source)[0]
    assert p.runs[0].size_pt==22 and p.bullet is None and p.indent_in==0
    assert source['size_pt']=='layout'


def test_missing_properties_stay_unknown_and_soft_break_survives():
    b=body('<a:p><a:r><a:t>One</a:t></a:r><a:br/><a:r><a:t>Two</a:t></a:r></a:p>')
    p=resolve_paragraphs(b,[],None,Theme(),{})[0]
    assert p.text=='One\nTwo'
    assert p.runs[0].font is None and p.runs[0].size_pt is None


def test_scheme_colour_transform_and_unknown_transform():
    theme=Theme(colors={'lt1':'FFFFFF'})
    assert theme.color(xml(f'<a:solidFill xmlns:a="{NS["a"]}"><a:schemeClr val="bg1"><a:lumMod val="95000"/></a:schemeClr></a:solidFill>'))=='F2F2F2'
    assert theme.color(xml(f'<a:solidFill xmlns:a="{NS["a"]}"><a:srgbClr val="FFFFFF"><a:unsupported/></a:srgbClr></a:solidFill>')) is None


def test_default_cell_margin_and_merged_cells():
    table=xml(f'<a:tbl xmlns:a="{NS["a"]}"><a:tblGrid><a:gridCol w="100"/><a:gridCol w="100"/></a:tblGrid><a:tr h="100"><a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>A</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:tc hMerge="1"><a:txBody><a:p/></a:txBody><a:tcPr/></a:tc></a:tr></a:tbl>')
    t=extract_table(table,None,Theme(),None)
    assert t.cells[0].margin_left_in==.1 and t.cells[0].grid_span==2
    assert t.cells[1].is_spanned


def test_group_children_use_absolute_coordinates():
    tree=xml(f'''<p:spTree xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:grpSp>
    <p:nvGrpSpPr><p:cNvPr id="4" name="group"/></p:nvGrpSpPr>
    <p:grpSpPr><a:xfrm><a:off x="1000" y="2000"/><a:ext cx="4000" cy="6000"/><a:chOff x="100" y="200"/><a:chExt cx="2000" cy="2000"/></a:xfrm></p:grpSpPr>
    <p:sp><p:nvSpPr><p:cNvPr id="5" name="child"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="200" y="300"/><a:ext cx="400" cy="500"/></a:xfrm></p:spPr></p:sp></p:grpSp></p:spTree>''')
    result=[];_extract_shapes(tree,1,None,None,Theme(),None,result,[],(1.,1.,0.,0.))
    assert result[0].bbox.model_dump()==dict(x=1200,y=2300,w=800,h=1500)
    assert result[0].group_path==[4]


def test_package_ignores_external_relationships(tmp_path):
    path=tmp_path/'external.pptx'
    with ZipFile(path,'w') as z:
        z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="x" Type="slide" Target="https://example.invalid" TargetMode="External"/></Relationships>')
    p=Package(str(path))
    try: assert p.rels('ppt/presentation.xml')=={}
    finally: p.zip.close()


def test_invalid_package_clear_error(tmp_path):
    path=tmp_path/'empty.pptx'
    with ZipFile(path,'w'):pass
    with pytest.raises(ValueError,match='presentation.xml missing'):
        extract_deck(str(path))


def test_rotated_and_flipped_shape_bbox():
    from app.model.extract import _mapped_bbox, _oriented
    from app.model.types import BBox
    shape=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:spPr><a:xfrm rot="5400000" flipH="1"/></p:spPr></p:sp>')
    box=BBox(x=100,y=200,w=400,h=200)
    assert _mapped_bbox(box,_oriented(shape,box)).model_dump()==dict(x=200,y=100,w=200,h=400)


def test_element_provenance_marks_mixed_run_sources():
    b=body('<a:lstStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></a:lstStyle>'
           '<a:p><a:r><a:rPr sz="1200"/><a:t>Explicit</a:t></a:r><a:r><a:t>Inherited</a:t></a:r></a:p>')
    provenance={}
    ps=resolve_paragraphs(b,[],None,Theme(),provenance)
    assert [r.size_pt for r in ps[0].runs]==[12.,18.]
    assert provenance['size_pt']=='mixed'


def test_inherited_paragraph_overrides_match_outline_level():
    own=body('<a:p><a:pPr lvl="1"/><a:r><a:t>Second level</a:t></a:r></a:p>')
    layout=body('<a:p><a:pPr lvl="0"><a:defRPr sz="3000"/></a:pPr></a:p>'
                '<a:p><a:pPr lvl="1"><a:defRPr sz="1400"/></a:pPr></a:p>')
    source={}
    paragraph=resolve_paragraphs(own,[(layout,'layout')],None,Theme(),source)[0]
    assert paragraph.level==1
    assert paragraph.runs[0].size_pt==14
    assert source['size_pt']=='layout'


def test_saved_autofit_scale_changes_effective_font_size():
    own=body('<a:bodyPr><a:normAutofit fontScale="75000"/></a:bodyPr>'
             '<a:p><a:r><a:rPr sz="2000"/><a:t>Shrunken</a:t></a:r></a:p>')
    source={};paragraph=resolve_paragraphs(own,[],None,Theme(),source)[0]
    assert paragraph.runs[0].size_pt==15
    assert source['size_pt']=='mixed'


def test_explicit_no_autofit_blocks_inherited_shrink():
    own=body('<a:bodyPr><a:noAutofit/></a:bodyPr>'
             '<a:p><a:r><a:rPr sz="2000"/><a:t>Normal</a:t></a:r></a:p>')
    layout=body('<a:bodyPr><a:normAutofit fontScale="75000"/></a:bodyPr>')
    assert resolve_paragraphs(own,[(layout,'layout')],None,Theme(),{})[0].runs[0].size_pt==20


def test_theme_style_reference_supplies_font_and_colour_when_unspecified():
    own=body('<a:p><a:r><a:t>Themed</a:t></a:r></a:p>')
    style=xml(f'<p:style xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><a:fontRef idx="major"><a:srgbClr val="123456"/></a:fontRef></p:style>')
    source={}
    run=resolve_paragraphs(own,[],None,Theme(major='Georgia'),source,theme_defaults=style)[0].runs[0]
    assert run.font=='Georgia' and run.color=='123456'
    assert source['font']=='theme' and source['color']=='theme'


def test_row_banding_starts_after_enabled_header():
    from app.model.extract import table_style_parts
    tbl=xml(f'<a:tbl xmlns:a="{NS["a"]}"><a:tblPr firstRow="1" bandRow="1"/></a:tbl>')
    style=xml(f'<a:tblStyle xmlns:a="{NS["a"]}"><a:wholeTbl/><a:band1H/><a:band2H/><a:firstRow/></a:tblStyle>')
    assert ET.QName(table_style_parts(tbl,style,1,0,4,3)[0]).localname=='band1H'
    assert ET.QName(table_style_parts(tbl,style,2,0,4,3)[0]).localname=='band2H'


def test_new_unseen_deck_preserves_non_sample_fonts_and_table_values(tmp_path):
    # python-pptx is an independent package writer here, never production extraction.
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs=Presentation()
    slide=prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text='Orion operating margins increased'
    title_run=slide.shapes.title.text_frame.paragraphs[0].runs[0]
    title_run.font.name='Georgia';title_run.font.size=Pt(23);title_run.font.bold=False
    table=slide.shapes.add_table(3,2, Inches(1), Inches(3), Inches(5), Inches(2)).table
    table.cell(1,0).text='Orion';table.cell(1,0).margin_left=Inches(.17)
    table.cell(1,1).text='17.5%'
    path=tmp_path/'previously-unseen.pptx';prs.save(path)
    result=extract_deck(str(path))
    title=next(e for e in result.slides[0].elements if e.role=='title')
    assert title.text=='Orion operating margins increased'
    assert {(r.font,r.size_pt,r.bold) for p in title.paragraphs for r in p.runs}=={('Georgia',23.,False)}
    output_table=next(e.table for e in result.slides[0].elements if e.table)
    assert output_table.n_cols==2
    assert next(c for c in output_table.cells if c.row==1 and c.col==0).margin_left_in==.17
    assert next(c for c in output_table.cells if c.row==1 and c.col==1).text=='17.5%'
    assert result.master.confidentiality_text is None


def test_shape_position_and_extent_inherit_independently():
    shape=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:spPr><a:xfrm><a:off x="200" y="300"/></a:xfrm></p:spPr></p:sp>')
    layout=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="400" cy="500"/></a:xfrm></p:spPr></p:sp>')
    bbox,source=shape_bbox([shape,layout])
    assert bbox.model_dump()==dict(x=200,y=300,w=400,h=500)
    assert source=='mixed'


def test_layout_theme_override_changes_only_defined_theme_components(tmp_path):
    from app.model.extract import read_theme
    path=tmp_path/'overridden.pptx'
    a=NS['a']
    with ZipFile(path,'w') as z:
        z.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels','<Relationships><Relationship Id="t" Type="theme" Target="../theme/theme1.xml"/></Relationships>')
        z.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels','<Relationships><Relationship Id="t" Type="themeOverride" Target="../theme/themeOverride1.xml"/></Relationships>')
        z.writestr('ppt/theme/theme1.xml',f'<a:theme xmlns:a="{a}"><a:themeElements><a:clrScheme><a:accent1><a:srgbClr val="123456"/></a:accent1><a:accent2><a:srgbClr val="ABCDEF"/></a:accent2></a:clrScheme><a:fontScheme><a:majorFont><a:latin typeface="Georgia"/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/></a:minorFont></a:fontScheme></a:themeElements></a:theme>')
        z.writestr('ppt/theme/themeOverride1.xml',f'<a:themeOverride xmlns:a="{a}"><a:clrScheme><a:accent1><a:srgbClr val="006EBE"/></a:accent1></a:clrScheme></a:themeOverride>')
    pkg=Package(str(path))
    try:
        theme=read_theme(pkg,'ppt/slideMasters/slideMaster1.xml',None,override_paths=['ppt/slideLayouts/slideLayout1.xml'])
        assert theme.colors=={'accent1':'006EBE','accent2':'ABCDEF'}
        assert theme.major=='Georgia' and theme.minor=='Calibri'
    finally:pkg.zip.close()
