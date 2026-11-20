"""Cell-level evidence must survive merges, group transforms and old records."""
from lxml import etree

from app.ai.checks import _slide_payload, _validated_findings
from app.model.extract import extract_table, _extract_shapes
from app.model.resolve import Theme, NS
from app.model.types import BBox, Cell, Element, Slide, Table
from app.rules.common import quote_bbox


def xml(value):
    return etree.fromstring(value.encode())


def table_xml():
    return xml(f'''<a:tbl xmlns:a="{NS['a']}"><a:tblGrid>
    <a:gridCol w="100"/><a:gridCol w="300"/></a:tblGrid>
    <a:tr h="50"><a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>Header</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:tc hMerge="1"><a:tcPr/></a:tc></a:tr>
    <a:tr h="150"><a:tc><a:txBody><a:p><a:r><a:t>C.R Bard</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:tc><a:txBody><a:p><a:r><a:t>Value</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc></a:tr></a:tbl>''')


def test_nonuniform_grid_and_horizontal_merge():
    t = extract_table(table_xml(), None, Theme(), None, BBox(x=1000, y=2000, w=800, h=400))
    assert t.cells[0].bbox == BBox(x=1000, y=2000, w=800, h=100)
    assert t.cells[1].bbox is None
    assert t.cells[2].bbox == BBox(x=1000, y=2100, w=200, h=300)
    assert t.cells[3].bbox == BBox(x=1200, y=2100, w=600, h=300)


def test_rotated_scaled_translated_table_cells():
    # A 90-degree rotation plus unequal group scaling and translation.
    t = extract_table(table_xml(), None, Theme(), None, BBox(x=100, y=200, w=400, h=200),
                      (0., 2., -3., 0., 1000., 2000.))
    assert t.cells[2].bbox == BBox(x=-200, y=2200, w=450, h=200)
    assert t.cells[0].bbox == BBox(x=250, y=2200, w=150, h=800)


def test_vertical_merge_and_unknown_grid_do_not_invent_geometry():
    node = table_xml()
    cells = node.findall('.//a:tc', NS)
    cells[0].set('gridSpan', '1'); cells[0].set('rowSpan', '2')
    cells[2].set('vMerge', '1')
    t = extract_table(node, None, Theme(), None, BBox(x=10, y=20, w=400, h=200))
    assert t.cells[0].bbox == BBox(x=10, y=20, w=100, h=200)
    assert t.cells[2].bbox is None
    node.find('.//a:gridCol', NS).set('w', '0')
    t = extract_table(node, None, Theme(), None, BBox(x=10, y=20, w=400, h=200))
    assert all(c.bbox is None for c in t.cells)


def test_malformed_span_does_not_claim_a_clipped_cell_target():
    node = table_xml()
    node.find('.//a:tc', NS).set('gridSpan', '3')
    t = extract_table(node, None, Theme(), None, BBox(x=0, y=0, w=400, h=200))
    assert t.cells[0].bbox is None


def test_group_extraction_passes_cell_transform():
    tbl = etree.tostring(table_xml()).decode()
    tree = xml(f'''<p:spTree xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:grpSp>
    <p:nvGrpSpPr><p:cNvPr id="4" name="group"/></p:nvGrpSpPr>
    <p:grpSpPr><a:xfrm><a:off x="1000" y="2000"/><a:ext cx="4000" cy="6000"/><a:chOff x="100" y="200"/><a:chExt cx="2000" cy="2000"/></a:xfrm></p:grpSpPr>
    <p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="table"/></p:nvGraphicFramePr><p:xfrm><a:off x="200" y="300"/><a:ext cx="400" cy="200"/></p:xfrm>
    <a:graphic><a:graphicData>{tbl}</a:graphicData></a:graphic></p:graphicFrame></p:grpSp></p:spTree>''')
    result = []
    _extract_shapes(tree, 1, None, None, Theme(), None, result, [], (1., 1., 0., 0.))
    assert result[0].bbox == BBox(x=1200, y=2300, w=800, h=600)
    assert result[0].table.cells[2].bbox == BBox(x=1200, y=2450, w=200, h=450)


def element(cells):
    return Element(id='s1-1', slide=1, shape_id=1, name='table', kind='table', role='table',
                   bbox=BBox(x=0, y=0, w=1000, h=1000),
                   table=Table(n_rows=len(cells), n_cols=1, cells=cells))


def test_exact_quote_uses_all_matching_cells_and_old_records_fall_back():
    a = BBox(x=20, y=30, w=100, h=40)
    b = BBox(x=20, y=100, w=100, h=40)
    e = element([Cell(row=0, col=0, text='C.R Bard', bbox=a),
                 Cell(row=1, col=0, text='C.R Bard', bbox=b)])
    assert quote_bbox(e, 'C.R Bard') == BBox(x=20, y=30, w=100, h=110)
    assert quote_bbox(e, 'Absent') == e.bbox
    e.table.cells[1].bbox = None
    assert quote_bbox(e, 'C.R Bard') == e.bbox
    # Historical data has no bbox key and still validates.
    assert Cell.model_validate({'row': 0, 'col': 0, 'text': 'Old'}).bbox is None


def test_grounded_ai_finding_targets_cell_without_changing_element_identity():
    box = BBox(x=20, y=30, w=100, h=40)
    e = element([Cell(row=0, col=0, text='Meidan Revenue', bbox=box)])
    s = Slide(index=1, layout_name='Custom', layout_kind='content', elements=[e])
    raw = {'rule_id': 'TYPO', 'element_id': e.id, 'quote': 'Meidan Revenue',
           'suggested_fix': 'Median Revenue', 'message': 'Letters transposed.', 'confidence': .95}
    findings = _validated_findings([raw], s, _slide_payload(s))
    assert len(findings) == 1
    assert findings[0].bbox == box and findings[0].element_id == e.id


