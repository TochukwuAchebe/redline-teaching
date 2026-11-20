"""DrawingML property resolution. Unknown values remain None, never guesses.

Chains are ordered nearest to farthest. Each property is resolved independently:
an explicit false/zero/no-bullet is an override, not a missing value.
Element.source aggregates all runs/paragraphs; differing supplying levels are
recorded as "mixed" rather than attributing every run to the first run's source.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Iterable
from lxml import etree as ET

from app.model.types import BBox, EMU_PER_INCH, Paragraph, Run

NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
Node = ET._Element


def child(node: Node | None, path: str) -> Node | None:
    return None if node is None else node.find(path, NS)


def children(node: Node | None, path: str) -> list[Node]:
    return [] if node is None else node.findall(path, NS)


def local(node: Node) -> str:
    return ET.QName(node).localname


def text(node: Node | None) -> str:
    """Text with paragraph and explicit soft-break boundaries preserved."""
    result = []
    for para in children(node, './/a:p'):
        result.append(''.join('\n' if local(c) == 'br' else ''.join(c.itertext())
                              if local(c) == 't' else ''.join(t.text or '' for t in children(c, 'a:t'))
                              for c in para if local(c) in {'r', 'fld', 'br', 't'}))
    return '\n'.join(result)


def boolean(value: str | None) -> bool | None:
    return None if value is None else value in {'1', 'true', 'on'}


@dataclass
class Theme:
    major: str | None = None
    minor: str | None = None
    colors: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=lambda: {
        'bg1': 'lt1', 'tx1': 'dk1', 'bg2': 'lt2', 'tx2': 'dk2'})

    def font(self, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith('+mj-'):
            return self.major
        if value.startswith('+mn-'):
            return self.minor
        return value

    def color(self, node: Node | None) -> str | None:
        if node is None:
            return None
        color_node = next((c for c in node if local(c) in {
            'srgbClr', 'schemeClr', 'sysClr', 'prstClr', 'scrgbClr'}), None)
        if color_node is None:
            return None
        kind, value = local(color_node), color_node.get('val', '')
        if kind == 'srgbClr':
            base = value
        elif kind == 'sysClr':
            base = color_node.get('lastClr')
        elif kind == 'schemeClr':
            base = self.colors.get(self.aliases.get(value, value))
        elif kind == 'prstClr':
            base = {'black': '000000', 'white': 'FFFFFF', 'red': 'FF0000',
                    'blue': '0000FF', 'yellow': 'FFFF00', 'green': '008000'}.get(value)
        else:
            try:
                base = ''.join(f'{max(0,min(255,round(int(color_node.get(c, "0"))*255/100000))):02X}'
                               for c in ('r', 'g', 'b'))
            except ValueError:
                return None
        if not base or len(base) != 6:
            return None
        try:
            rgb = [int(base[i:i+2], 16) / 255 for i in (0, 2, 4)]
        except ValueError:
            return None
        # DrawingML tint/shade and luminance transforms are ordered operations.
        for transform in color_node:
            name = local(transform)
            v = int(transform.get('val', '0')) / 100000
            if name == 'tint':
                rgb = [c + (1-c)*v for c in rgb]
            elif name == 'shade':
                rgb = [c*v for c in rgb]
            elif name in {'lumMod', 'lumOff'}:
                h, l, s = colorsys.rgb_to_hls(*rgb)
                l = l*v if name == 'lumMod' else l+v
                rgb = list(colorsys.hls_to_rgb(h, max(0, min(1, l)), s))
            elif name in {'alpha', 'alphaMod', 'alphaOff'}:
                continue  # frozen contract carries RGB only, not transparency
            else:
                return None  # unsupported transform must not yield a false colour
        return ''.join(f'{max(0,min(255,round(c*255))):02X}' for c in rgb)


def normalize_bullet(char: str | None, font: str | None) -> str | None:
    if char is None:
        return None
    if char in {'∎', '▪', '■'} or (font and 'wingdings' in font.lower() and char in {'§', 'n'}):
        return 'square'
    if char in {'–', '-', '—'}:
        return 'dash'
    if char in {'➢', '➤', '➣'} or (font and 'wingdings' in font.lower() and char == 'Ø'):
        return 'arrow'
    if char in {'•', '●'}:
        return 'circle'
    return 'other'


def first_attr(chain: Iterable[tuple[Node | None, str]], key: str) -> tuple[str | None, str | None]:
    for node, origin in chain:
        if node is not None and key in node.attrib:
            return node.get(key), origin
    return None, None


def record_source(provenance: dict[str, str], key: str, source: str) -> None:
    previous=provenance.get(key)
    provenance[key]=source if previous is None or previous == source else 'mixed'


def resolve_run(run: Node | None, defaults: list[tuple[Node | None, str]],
                theme: Theme, provenance: dict[str, str], value: str) -> Run:
    chain = [(run, 'run')] + defaults
    props: dict = {'text': value}
    for key, attr, convert in [('size_pt', 'sz', lambda v: float(v)/100),
                               ('bold', 'b', boolean), ('italic', 'i', boolean)]:
        raw, source = first_attr(chain, attr)
        props[key] = convert(raw) if raw is not None else None
        if source:
            record_source(provenance, key, source)
    baseline, _ = first_attr(chain, 'baseline')
    props['superscript'] = bool(baseline and int(baseline) > 0)
    for node, source in chain:
        latin = child(node, 'a:latin')
        font_ref = child(node, 'a:fontRef')
        raw = latin.get('typeface') if latin is not None else None
        if raw is None and font_ref is not None:
            raw = '+mj-lt' if font_ref.get('idx') == 'major' else '+mn-lt'
        if raw is not None:
            props['font'] = theme.font(raw)
            record_source(provenance, 'font', source)
            break
    for node, source in chain:
        if child(node, 'a:noFill') is not None:
            break
        fill = child(node, 'a:solidFill')
        # Table tcTxStyle stores colour directly, not under solidFill.
        if fill is not None or (node is not None and local(node) == 'tcTxStyle'):
            props['color'] = theme.color(fill if fill is not None else node)
            record_source(provenance, 'color', source)
            break
        font_ref = child(node, 'a:fontRef')
        if font_ref is not None:
            props['color'] = theme.color(font_ref)
            record_source(provenance, 'color', source)
            break
    return Run(**props)


def spacing(node: Node, name: str) -> float | None:
    pts = child(node, f'a:{name}/a:spcPts')
    return float(pts.get('val')) / 100 if pts is not None else None


def resolve_paragraphs(body: Node | None, inherited: list[tuple[Node | None, str]],
                       master_style: Node | None, theme: Theme,
                       provenance: dict[str, str], table_defaults: list[Node] | None = None,
                       theme_defaults: Node | None = None) -> list[Paragraph]:
    paragraphs = []
    for p in children(body, 'a:p'):
        own = child(p, 'a:pPr')
        level = int(own.get('lvl', '0')) if own is not None else 0
        chain: list[tuple[Node | None, str]] = [(own, 'paragraph')]
        for source_body, origin in [(body, 'shape')] + inherited:
            chain.append((child(source_body, f'a:lstStyle/a:lvl{level+1}pPr'), origin))
            chain.append((child(source_body, 'a:lstStyle/a:defPPr'), origin))
            if source_body is not body:
                # Placeholder samples contain one paragraph per outline level.
                # A level-3 paragraph must not inherit the level-1 paragraph's
                # overrides merely because it happens to occur first in XML.
                matching = next((candidate for candidate in children(source_body, 'a:p')
                                 if int((child(candidate, 'a:pPr').get('lvl', '0')
                                         if child(candidate, 'a:pPr') is not None else '0')) == level), None)
                chain.append((child(matching, 'a:pPr'), origin))
        chain += [(child(master_style, f'a:lvl{level+1}pPr'), 'master'),
                  (child(master_style, 'a:defPPr'), 'master')]
        defaults = [(child(node, 'a:defRPr'), origin) for node, origin in chain]
        # endParaRPr applies to an empty paragraph's insertion mark, not all runs.
        if table_defaults:
            defaults = defaults[:1] + [(n, 'table_style') for n in table_defaults] + defaults[1:]
        if theme_defaults is not None:
            defaults.append((theme_defaults, 'theme'))
        runs = []
        for node in p:
            if local(node) in {'r', 'fld'}:
                runs.append(resolve_run(child(node, 'a:rPr'), defaults, theme, provenance,
                                        ''.join(t.text or '' for t in children(node, 'a:t'))))
            elif local(node) == 'br':
                runs.append(resolve_run(child(node, 'a:rPr'), defaults, theme, provenance, '\n'))
        if not runs:
            runs = [resolve_run(child(p, 'a:endParaRPr'), defaults, theme, provenance, '')]
        # normAutofit records the actual PowerPoint shrink percentage. Preserve
        # nominal sizes only when no saved shrink factor exists; spAutoFit alters
        # box geometry, not the saved type size.
        autofit = None
        for source_body, _ in [(body, 'shape')] + inherited:
            body_props = child(source_body, 'a:bodyPr')
            mode = next((node for node in body_props
                         if local(node) in {'normAutofit', 'noAutofit', 'spAutoFit'}), None) if body_props is not None else None
            if mode is not None:
                autofit = mode if local(mode) == 'normAutofit' else None
                break
        if autofit is not None and 'fontScale' in autofit.attrib:
            scale = int(autofit.get('fontScale')) / 100000
            for run in runs:
                if run.size_pt is not None:
                    run.size_pt *= scale
            if scale != 1:
                record_source(provenance, 'size_pt', 'autofit')
        props: dict = {'text': ''.join(r.text for r in runs), 'level': level, 'runs': runs}
        for field_name, attr, conv in [('align', 'algn', str), ('indent_in', 'marL', lambda v: int(v)/EMU_PER_INCH)]:
            val, origin = first_attr(chain, attr)
            props[field_name] = conv(val) if val is not None else None
            if origin:
                record_source(provenance, field_name, origin)
        for field_name, tag in [('space_before_pt', 'spcBef'), ('space_after_pt', 'spcAft')]:
            for node, origin in chain:
                if node is not None and child(node, f'a:{tag}') is not None:
                    props[field_name] = spacing(node, tag)
                    record_source(provenance, field_name, origin)
                    break
        for node, origin in chain:
            pct = child(node, 'a:lnSpc/a:spcPct')
            pts = child(node, 'a:lnSpc/a:spcPts')
            if pct is not None:
                props['line_spacing'] = int(pct.get('val')) / 100000
                record_source(provenance, 'line_spacing', origin)
                break
            if pts is not None:
                # Absolute leading cannot be represented as a relative ratio.
                props['line_spacing_pt'] = int(pts.get('val')) / 100
                record_source(provenance, 'line_spacing', origin)
                break
        for node, origin in chain:
            if child(node, 'a:buNone') is not None:
                break
            char_node = child(node, 'a:buChar')
            auto = child(node, 'a:buAutoNum')
            if char_node is not None or auto is not None:
                raw = char_node.get('char') if char_node is not None else None
                props['bullet_char'] = raw
                for candidate, _ in chain:
                    font = child(candidate, 'a:buFont')
                    if font is not None:
                        props['bullet_font'] = font.get('typeface')
                        break
                props['bullet'] = normalize_bullet(raw, props.get('bullet_font')) if raw else 'other'
                record_source(provenance, 'bullet', origin)
                break
        paragraphs.append(Paragraph(**props))
    return paragraphs


def fill_color(props: Node | None, theme: Theme) -> tuple[bool, str | None]:
    """Presence is separate from value so noFill blocks inherited fills."""
    if props is None:
        return False, None
    for tag in ('noFill', 'solidFill', 'gradFill', 'blipFill', 'pattFill', 'grpFill'):
        node = child(props, 'a:'+tag)
        if node is not None:
            return True, theme.color(node) if tag == 'solidFill' else None
    return False, None


def shape_bbox(shapes: list[Node | None]) -> tuple[BBox, str]:
    # Position and extent inherit independently; an author can override just one.
    offset = extent = None
    origins = set()
    for shape, origin in zip(shapes, ('shape', 'layout', 'master')):
        xfrm = child(shape, 'p:spPr/a:xfrm')
        if xfrm is None:
            xfrm = child(shape, 'p:xfrm')
        if xfrm is None:
            xfrm = child(shape, 'p:grpSpPr/a:xfrm')
        off, ext = child(xfrm, 'a:off'), child(xfrm, 'a:ext')
        if offset is None and off is not None:
            offset = (int(off.get('x', '0')), int(off.get('y', '0')))
            origins.add(origin)
        if extent is None and ext is not None:
            extent = (int(ext.get('cx', '0')), int(ext.get('cy', '0')))
            origins.add(origin)
    x,y=offset or (0,0)
    w,h=extent or (0,0)
    source='mixed' if len(origins)>1 else next(iter(origins),'unknown')
    return BBox(x=x,y=y,w=w,h=h),source
