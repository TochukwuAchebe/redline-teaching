"""Read PPTX package structure and effective DrawingML formatting.

No sample-specific values or annotations participate in extraction decisions.
Only slide-owned shapes are emitted: deleted footer placeholders must stay
missing, even when an equivalent placeholder still exists in the master.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import posixpath
import math
from collections import Counter
from zipfile import ZipFile
from lxml import etree as ET

from app.model.types import BBox, BodyLevelSpec, Cell, Deck, Element, MasterSpec, Slide, Table, EMU_PER_INCH
from app.model.resolve import (NS, Node, Theme, boolean, child, children, fill_color,
                              local, normalize_bullet, resolve_paragraphs, shape_bbox, text)
from app.model.roles import classify_roles

PH_NAMES = {'title': 'TITLE', 'ctrTitle': 'CENTER_TITLE', 'subTitle': 'SUBTITLE',
            'body': 'BODY', 'obj': 'OBJECT', 'ftr': 'FOOTER', 'sldNum': 'SLIDE_NUMBER',
            'dt': 'DATE'}


class Package:
    """Bounded XML-only ZIP reader; never extracts paths or follows external rels."""
    def __init__(self, path: str):
        self.zip = ZipFile(path)
        info = self.zip.infolist()
        if len(info) > 20000 or sum(i.file_size for i in info) > 512*1024*1024:
            self.zip.close()
            raise ValueError('PowerPoint package exceeds extraction limits')
        self.cache: dict[str, Node | None] = {}
        self.rel_cache: dict[str, dict[str, tuple[str, str]]] = {}

    def xml(self, path: str | None) -> Node | None:
        if not path:
            return None
        if path not in self.cache:
            try:
                data = self.zip.read(path)
            except KeyError:
                self.cache[path] = None
            else:
                self.cache[path] = ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))
        return self.cache[path]

    def rels(self, path: str) -> dict[str, tuple[str, str]]:
        if path not in self.rel_cache:
            rel_path = str(PurePosixPath(path).parent / '_rels' / (PurePosixPath(path).name+'.rels'))
            root = self.xml(rel_path)
            rels = {}
            if root is not None:
                for r in root:
                    if r.get('TargetMode') == 'External':
                        continue
                    target = r.get('Target', '')
                    target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                    if target.startswith('../'):
                        continue
                    rels[r.get('Id', '')] = (target, r.get('Type', '').rsplit('/', 1)[-1])
            self.rel_cache[path] = rels
        return self.rel_cache[path]

    def related(self, path: str, kind: str) -> str | None:
        return next((p for p, k in self.rels(path).values() if k == kind), None)


def placeholder(shape: Node | None) -> Node | None:
    return child(shape, './/p:nvPr/p:ph')


def match_placeholder(shape: Node | None, parent: Node | None, master: bool = False) -> Node | None:
    ph = placeholder(shape)
    if ph is None:
        return None
    candidates = children(parent, 'p:cSld/p:spTree/p:sp')
    typ, idx = ph.get('type', 'obj'), ph.get('idx', '0')
    if not master:
        matched = [s for s in candidates if placeholder(s) is not None and placeholder(s).get('idx', '0') == idx]
        if matched:
            return matched[0]
    # Master inheritance is by placeholder type, not the layout-specific idx.
    equivalent = {'ctrTitle': 'title', 'subTitle': 'body', 'obj': 'body'}
    typ = equivalent.get(typ, typ)
    return next((s for s in candidates if placeholder(s) is not None and
                 equivalent.get(placeholder(s).get('type', 'obj'), placeholder(s).get('type', 'obj')) == typ), None)


def read_theme(pkg: Package, master_path: str | None, master: Node | None,
               layout: Node | None = None, slide: Node | None = None,
               override_paths: list[str | None] | None = None) -> Theme:
    theme = pkg.xml(pkg.related(master_path, 'theme')) if master_path else None
    result = Theme()
    theme_roots=[theme]
    for path in override_paths or []:
        if path:
            override = pkg.xml(pkg.related(path, 'themeOverride'))
            if override is not None:
                theme_roots.append(override)
    for root in theme_roots:
        elements=child(root,'a:themeElements')
        if elements is None:
            elements=root  # themeOverride supplies fontScheme/clrScheme directly
        major=child(elements,'a:fontScheme/a:majorFont/a:latin')
        minor=child(elements,'a:fontScheme/a:minorFont/a:latin')
        if major is not None:
            result.major=major.get('typeface')
        if minor is not None:
            result.minor=minor.get('typeface')
        scheme=child(elements,'a:clrScheme')
        if scheme is not None:
            for item in scheme:
                value=result.color(item)
                if value:
                    result.colors[local(item)]=value
    clr_map = child(master, 'p:clrMap')
    if clr_map is not None:
        result.aliases.update(clr_map.attrib)
    for root in (layout, slide):
        override = child(root, 'p:clrMapOvr/a:overrideClrMapping')
        if override is not None:
            result.aliases.update(override.attrib)
    return result


def shape_style(shape: Node | None, layout: Node | None, master_shape: Node | None,
                master: Node | None) -> Node | None:
    ph = placeholder(shape)
    if ph is not None:
        typ = ph.get('type')
        if typ is None and placeholder(layout) is not None:
            typ = placeholder(layout).get('type', 'obj')
        style = 'titleStyle' if typ in {'title', 'ctrTitle'} else 'bodyStyle' if typ in {'body', 'obj', 'subTitle'} else 'otherStyle'
    else:
        style = 'otherStyle'
    return child(master, 'p:txStyles/p:'+style)


def body_chain(shape: Node, layout: Node | None, master_shape: Node | None) -> list[tuple[Node | None, str]]:
    return [(child(layout, 'p:txBody'), 'layout'), (child(master_shape, 'p:txBody'), 'master')]


def shape_anchor(shape: Node, layout: Node | None, master_shape: Node | None) -> tuple[str | None, str | None]:
    for candidate, origin in ((shape, 'shape'), (layout, 'layout'), (master_shape, 'master')):
        body = child(candidate, 'p:txBody/a:bodyPr')
        if body is not None and 'anchor' in body.attrib:
            return body.get('anchor'), origin
    return None, None


def table_style_parts(tbl: Node, style: Node | None, row: int, col: int, rows: int, cols: int) -> list[Node]:
    props = child(tbl, 'a:tblPr')
    names = ['wholeTbl']
    if props is not None:
        if boolean(props.get('bandRow')):
            offset = 1 if boolean(props.get('firstRow')) else 0
            names.append('band1H' if (row-offset) % 2 == 0 else 'band2H')
        if boolean(props.get('bandCol')):
            offset = 1 if boolean(props.get('firstCol')) else 0
            names.append('band1V' if (col-offset) % 2 == 0 else 'band2V')
        for attr, match, name in [('firstCol', col == 0, 'firstCol'), ('lastCol', col == cols-1, 'lastCol'),
                                  ('firstRow', row == 0, 'firstRow'), ('lastRow', row == rows-1, 'lastRow')]:
            if match and boolean(props.get(attr)):
                names.append(name)
    return [n for name in reversed(names) if (n := child(style, 'a:'+name)) is not None]


def extract_table(tbl: Node, styles: Node | None, theme: Theme, master_style: Node | None,
                  bbox: BBox | None = None, transform: tuple = (1., 0., 0., 1., 0., 0.)) -> Table:
    rows = children(tbl, 'a:tr')
    columns = children(tbl, 'a:tblGrid/a:gridCol')
    n_cols = len(columns)
    widths = [int(c.get('w', '0')) for c in columns]
    heights = [int(r.get('h', '0')) for r in rows]
    geometry_known = (bbox is not None and bbox.w > 0 and bbox.h > 0
                      and bool(widths) and bool(heights)
                      and all(w > 0 for w in widths) and all(h > 0 for h in heights))
    xs, ys = [0], [0]
    for width in widths:
        xs.append(xs[-1] + width)
    for height in heights:
        ys.append(ys[-1] + height)
    sid = child(tbl, 'a:tblPr/a:tableStyleId')
    style_id = sid.text if sid is not None else styles.get('def') if styles is not None else None
    style = next((s for s in styles if s.get('styleId') == style_id), None) if styles is not None else None
    cells = []
    for ri, row in enumerate(rows):
        for ci, tc in enumerate(children(row, 'a:tc')):
            props = child(tc, 'a:tcPr')
            parts = table_style_parts(tbl, style, ri, ci, len(rows), n_cols)
            present, fill = fill_color(props, theme)
            if not present:
                for part in parts:
                    present, fill = fill_color(child(part, 'a:tcStyle/a:fill'), theme)
                    if present:
                        break
            defaults = [n for part in parts if (n := child(part, 'a:tcTxStyle')) is not None]
            paragraphs = resolve_paragraphs(child(tc, 'a:txBody'), [], master_style, theme, {}, defaults)
            borders = {}
            for edge, tag in [('left', 'lnL'), ('right', 'lnR'), ('top', 'lnT'), ('bottom', 'lnB')]:
                ln = child(props, 'a:'+tag)
                if ln is None:
                    for part in parts:
                        ln = child(part, f'a:tcStyle/a:tcBdr/a:{edge}/a:ln')
                        if ln is not None:
                            break
                borders['border_'+edge] = theme.color(child(ln, 'a:solidFill'))
            grid_span, row_span = int(tc.get('gridSpan', '1')), int(tc.get('rowSpan', '1'))
            is_spanned = boolean(tc.get('hMerge')) is True or boolean(tc.get('vMerge')) is True
            cell_bbox = None
            if (geometry_known and not is_spanned and 0 <= ci < n_cols
                    and 0 < grid_span <= n_cols-ci and 0 < row_span <= len(rows)-ri):
                # The frame can be scaled, rotated, flipped or nested in groups.
                # Map the cell before taking its axis-aligned enclosing rectangle.
                left = bbox.x + bbox.w * xs[ci] / xs[-1]
                top = bbox.y + bbox.h * ys[ri] / ys[-1]
                right = bbox.x + bbox.w * xs[ci + grid_span] / xs[-1]
                bottom = bbox.y + bbox.h * ys[ri + row_span] / ys[-1]
                cell_bbox = _mapped_bbox(BBox(x=round(left), y=round(top),
                                              w=round(right-left), h=round(bottom-top)), transform)
            cells.append(Cell(row=ri, col=ci, text='\n'.join(p.text for p in paragraphs), paragraphs=paragraphs, bbox=cell_bbox,
                              fill=fill, margin_left_in=int(props.get('marL', '91440'))/EMU_PER_INCH if props is not None else .1,
                              anchor=props.get('anchor') if props is not None else None,
                              grid_span=grid_span, row_span=row_span,
                              is_spanned=is_spanned, **borders))
    # Header continuation needs positive structural evidence, not boldness
    # alone: bold section dividers and numeric threshold rows are body content.
    first_fills=[c.fill for c in cells if c.row == 0 and c.fill]
    header_fill=Counter(first_fills).most_common(1)[0][0] if first_fills else None
    header_rows = 1
    for ri in range(1, len(rows)):
        row_cells = [c for c in cells if c.row == ri and c.text.strip() and not c.is_spanned]
        if not row_cells or not header_fill or (len(row_cells) == 1 and row_cells[0].grid_span > 1):
            break
        if any(c.fill != header_fill for c in row_cells):
            break
        if not all(all(r.bold is True for p in c.paragraphs for r in p.runs if r.text.strip()) for c in row_cells):
            break
        header_rows += 1
    return Table(n_rows=len(rows), n_cols=n_cols, cells=cells, header_rows=min(header_rows, max(1,len(rows)-1)))



def master_spec(pkg: Package, master_path: str, layouts: list[Node]) -> MasterSpec:
    master = pkg.xml(master_path)
    theme = read_theme(pkg, master_path, master)
    spec = MasterSpec(theme_major_font=theme.major, theme_minor_font=theme.minor)
    shapes = children(master, 'p:cSld/p:spTree/p:sp')
    title = next((s for s in shapes if placeholder(s) is not None and placeholder(s).get('type') in {'title', 'ctrTitle'}), None)
    if title is not None:
        ps = resolve_paragraphs(child(title, 'p:txBody'), [], child(master, 'p:txStyles/p:titleStyle'), theme, {})
        run = next((r for p in ps for r in p.runs), None)
        if run:
            spec.title_font, spec.title_size_pt, spec.title_bold = run.font, run.size_pt, run.bold
        spec.title_anchor, _ = shape_anchor(title, None, None)
    footer = next((s for s in shapes if placeholder(s) is not None and placeholder(s).get('type') == 'ftr' and text(s).strip()), None)
    if footer is not None:
        spec.confidentiality_text = text(footer).strip()
        spec.confidentiality_bbox, _ = shape_bbox([footer])
    body = child(master, 'p:txStyles/p:bodyStyle')
    for level in range(5):
        prop = child(body, f'a:lvl{level+1}pPr')
        char = child(prop, 'a:buChar')
        font = child(prop, 'a:buFont')
        default = child(prop, 'a:defRPr')
        before = child(prop, 'a:spcBef/a:spcPts')
        raw = char.get('char') if char is not None else None
        spec.body_levels.append(BodyLevelSpec(level=level, bullet=normalize_bullet(raw, font.get('typeface') if font is not None else None),
                                              bullet_char=raw, size_pt=float(default.get('sz'))/100 if default is not None and default.get('sz') else None,
                                              indent_in=int(prop.get('marL'))/EMU_PER_INCH if prop is not None and prop.get('marL') else None,
                                              space_before_pt=float(before.get('val'))/100 if before is not None else None))
    for layout in layouts:
        cslide = child(layout, 'p:cSld')
        name = cslide.get('name', '') if cslide is not None else ''
        t = next((s for s in children(layout, 'p:cSld/p:spTree/p:sp') if placeholder(s) is not None and placeholder(s).get('type') in {'title', 'ctrTitle'}), None)
        if t is not None:
            spec.title_bbox_by_layout[name], _ = shape_bbox([t, None, title])
    return spec


def extract_deck(path: str) -> Deck:
    """Parse in presentation order. ValueError describes invalid package structure."""
    pkg = Package(path)
    try:
        presentation = pkg.xml('ppt/presentation.xml')
        if presentation is None:
            raise ValueError('Not a PowerPoint presentation: presentation.xml missing')
        size = child(presentation, 'p:sldSz')
        if size is None:
            raise ValueError('PowerPoint slide dimensions missing')
        master_paths = [p for p, kind in pkg.rels('ppt/presentation.xml').values() if kind == 'slideMaster']
        if not master_paths:
            raise ValueError('PowerPoint slide master missing')
        primary = master_paths[0]
        layouts = [pkg.xml(p) for p, k in pkg.rels(primary).values() if k == 'slideLayout']
        spec = master_spec(pkg, primary, [l for l in layouts if l is not None])
        # Frozen contract has one MasterSpec. All runs still resolve their actual per-slide master.
        styles = pkg.xml('ppt/tableStyles.xml')
        slides = []
        rels = pkg.rels('ppt/presentation.xml')
        for index, sid in enumerate(children(presentation, 'p:sldIdLst/p:sldId'), 1):
            rid = sid.get('{'+NS['r']+'}id')
            if rid not in rels:
                raise ValueError(f'Slide relationship missing for position {index}')
            slide_path = rels[rid][0]
            root = pkg.xml(slide_path)
            if root is None:
                raise ValueError(f'Slide XML missing for position {index}')
            layout_path = pkg.related(slide_path, 'slideLayout')
            layout = pkg.xml(layout_path)
            master_path = pkg.related(layout_path, 'slideMaster') if layout_path else primary
            master = pkg.xml(master_path)
            theme = read_theme(pkg, master_path, master, layout, root, [layout_path, slide_path])
            cslide = child(layout, 'p:cSld')
            name = cslide.get('name', '') if cslide is not None else ''
            layout_shapes = children(layout, 'p:cSld/p:spTree/p:sp')
            kind = 'title' if 'title slide' in name.lower() else 'section' if 'section header' in name.lower() else 'content' if any(placeholder(s) is not None and placeholder(s).get('type') in {'title', 'ctrTitle'} for s in layout_shapes) else 'other'
            elements: list[Element] = []
            charts = {rid: pkg.xml(path) for rid, (path, rel) in pkg.rels(slide_path).items() if rel == 'chart'}
            _extract_shapes(child(root, 'p:cSld/p:spTree'), index, layout, master, theme, styles,
                            elements, [], (1., 1., 0., 0.), charts)
            classify_roles(elements, spec, int(size.get('cy')), kind)
            notes_root = pkg.xml(pkg.related(slide_path, 'notesSlide'))
            notes = []
            for s in children(notes_root, 'p:cSld/p:spTree/p:sp'):
                ph = placeholder(s)
                if ph is None or ph.get('type') == 'body':
                    notes.append(text(s))
            slides.append(Slide(index=index, layout_name=name, layout_kind=kind,
                                hidden=root.get('show') == '0', elements=elements, notes='\n'.join(notes)))
        return Deck(filename=Path(path).name, slide_width=int(size.get('cx')), slide_height=int(size.get('cy')), master=spec, slides=slides)
    finally:
        pkg.zip.close()


def _compose(outer: tuple, inner: tuple) -> tuple:
    """Compose 2D affine transforms (a,b,c,d,tx,ty), inner first."""
    a,b,c,d,x,y=outer; A,B,C,D,X,Y=inner
    return (a*A+c*B,b*A+d*B,a*C+c*D,b*C+d*D,a*X+c*Y+x,b*X+d*Y+y)


def _oriented(shape: Node, box: BBox) -> tuple:
    xfrm = child(shape, 'p:spPr/a:xfrm')
    if xfrm is None:
        xfrm = child(shape, 'p:grpSpPr/a:xfrm')
    if xfrm is None:
        xfrm = child(shape, 'p:xfrm')
    if xfrm is None:
        return (1.,0.,0.,1.,0.,0.)
    angle=math.radians(int(xfrm.get('rot','0'))/60000)
    cos,sin=math.cos(angle),math.sin(angle)
    fx=-1 if boolean(xfrm.get('flipH')) else 1
    fy=-1 if boolean(xfrm.get('flipV')) else 1
    cx,cy=box.x+box.w/2,box.y+box.h/2
    a,b,c,d=cos*fx,sin*fx,-sin*fy,cos*fy
    return (a,b,c,d,cx-a*cx-c*cy,cy-b*cx-d*cy)


def _mapped_bbox(box: BBox, transform: tuple) -> BBox:
    a,b,c,d,tx,ty=transform
    points=[(a*x+c*y+tx,b*x+d*y+ty) for x,y in (
        (box.x,box.y),(box.x+box.w,box.y),(box.x,box.y+box.h),(box.x+box.w,box.y+box.h))]
    left,top=min(p[0] for p in points),min(p[1] for p in points)
    right,bottom=max(p[0] for p in points),max(p[1] for p in points)
    return BBox(x=round(left),y=round(top),w=round(right-left),h=round(bottom-top))


def _extract_shapes(tree: Node | None, index: int, layout: Node | None, master: Node | None,
                    theme: Theme, styles: Node | None, elements: list[Element],
                    group_path: list[int], transform: tuple, charts: dict | None = None) -> None:
    if tree is None:
        return
    if len(transform)==4:
        sx,sy,tx,ty=transform
        transform=(sx,0.,0.,sy,tx,ty)
    for shape in tree:
        tag = local(shape)
        if tag not in {'sp', 'pic', 'graphicFrame', 'grpSp', 'cxnSp'}:
            continue
        nv = child(shape, './/p:cNvPr')
        shape_id = int(nv.get('id', '0')) if nv is not None else 0
        inherited_layout = match_placeholder(shape, layout)
        inherited_master = match_placeholder(inherited_layout if inherited_layout is not None else shape, master, True)
        candidates = [shape, inherited_layout, inherited_master]
        bbox, origin = shape_bbox(candidates)
        orientation = _compose(transform,_oriented(shape,bbox))
        absolute = _mapped_bbox(bbox,orientation)
        if tag == 'grpSp':
            xfrm = child(shape, 'p:grpSpPr/a:xfrm')
            ch_off, ch_ext = child(xfrm, 'a:chOff'), child(xfrm, 'a:chExt')
            gx = bbox.w/int(ch_ext.get('cx')) if ch_ext is not None and int(ch_ext.get('cx', '0')) else 1.
            gy = bbox.h/int(ch_ext.get('cy')) if ch_ext is not None and int(ch_ext.get('cy', '0')) else 1.
            ox, oy = (int(ch_off.get('x', '0')), int(ch_off.get('y', '0'))) if ch_off is not None else (0, 0)
            child_map=(gx,0.,0.,gy,bbox.x-gx*ox,bbox.y-gy*oy)
            _extract_shapes(shape, index, layout, master, theme, styles, elements, group_path+[shape_id],
                            _compose(orientation,child_map), charts)
            continue
        provenance = {'bbox': origin}
        style = shape_style(shape, inherited_layout, inherited_master, master)
        ps = resolve_paragraphs(child(shape, 'p:txBody'), body_chain(shape, inherited_layout, inherited_master), style, theme, provenance, theme_defaults=child(shape, 'p:style'))
        ph = placeholder(shape)
        ph_type = ph.get('type') if ph is not None else None
        if ph is not None and ph_type is None:
            lp = placeholder(inherited_layout)
            ph_type = lp.get('type', 'obj') if lp is not None else 'obj'
        tbl = child(shape, './/a:tbl')
        chart = child(shape, './/{http://schemas.openxmlformats.org/drawingml/2006/chart}chart')
        kind = 'table' if tbl is not None else 'chart' if chart is not None else 'picture' if tag == 'pic' else 'line' if tag == 'cxnSp' else 'placeholder' if ph is not None else 'textbox' if child(shape, 'p:txBody') is not None else 'autoshape'
        fill = None
        for candidate in candidates:
            present, value = fill_color(child(candidate, 'p:spPr'), theme)
            if present:
                fill = value
                break
        line_color = theme.color(child(shape, 'p:spPr/a:ln/a:solidFill'))
        extra_colors = [c for c in (theme.color(stop) for stop in children(shape, 'p:spPr/a:gradFill/a:gsLst/a:gs')) if c]
        if chart is not None and charts:
            part = charts.get(chart.get('{'+NS['r']+'}id'))
            c_ns = '{http://schemas.openxmlformats.org/drawingml/2006/chart}'
            for props in (part.iter(c_ns+'spPr') if part is not None else []):
                parent = local(props.getparent())
                if parent in {'ser', 'dPt'}:
                    value = theme.color(child(props, 'a:solidFill'))
                    if value:
                        extra_colors.append(value)
        anchor, anchor_source = shape_anchor(shape, inherited_layout, inherited_master)
        if anchor_source:
            provenance['vertical_anchor'] = anchor_source
        table = extract_table(tbl, styles, theme, child(master, 'p:txStyles/p:otherStyle'), bbox, orientation) if tbl is not None else None
        value = '\n'.join(p.text for p in ps)
        if table:
            value = '\n'.join(c.text for c in table.cells if not c.is_spanned)
        elements.append(Element(id=f's{index}-{shape_id}', slide=index, shape_id=shape_id,
                                name=nv.get('name', '') if nv is not None else '', kind=kind,
                                placeholder_type=PH_NAMES.get(ph_type, ph_type.upper() if ph_type else None),
                                bbox=absolute, paragraphs=ps, text=value, table=table,
                                fill=fill, line_color=line_color, extra_colors=sorted(set(extra_colors)), vertical_anchor=anchor,
                                source=provenance, group_path=group_path.copy()))
