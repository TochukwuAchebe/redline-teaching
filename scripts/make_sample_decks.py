"""Build the example house-style template and two demo decks from scratch.

Everything here is generated from python-pptx's blank default presentation, so the
samples contain no third-party content. The company, people and figures are fictional.

    python -I scripts/make_sample_decks.py            # writes into samples/

Outputs:
    samples/example-template.pptx    the house-style master: fonts, bullets, footer statement
    samples/demo-clean.pptx          slides that follow the example guideline
    samples/demo-with-issues.pptx    the same kind of deck with planted issues (see demo-answer-key.yaml)
"""
from __future__ import annotations

import copy
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
A = "{%s}" % NS["a"]
STATEMENT = "Confidential. Prepared for teaching purposes only; all companies and figures are fictional."
BLUE, ZEBRA, STATS, CLIENT = "006EBE", "F2F2F2", "FFFFDB", "F1FFE1"
EMU = 914400

# (bullet character, bullet font, left margin in inches, space before in pt) for levels 1-5
LEVELS = [("§", "Wingdings", 0.2, 6), ("–", "Arial", 0.4, 0), ("Ø", "Wingdings", 0.6, 0),
          ("•", "Arial", 0.8, 0), ("•", "Arial", 1.0, 0)]


def _sub(parent, tag, **attrs):
    el = etree.SubElement(parent, A + tag)
    for k, v in attrs.items():
        el.set(k, str(v))
    return el


def _style_master(prs: Presentation) -> None:
    master = prs.slide_master
    # Theme fonts: Calibri for headings and body.
    theme_part = next(r.target_part for r in master.part.rels.values() if r.reltype.endswith("/theme"))
    theme = etree.fromstring(theme_part.blob)
    for kind in ("majorFont", "minorFont"):
        latin = theme.find(f".//a:fontScheme/a:{kind}/a:latin", NS)
        latin.set("typeface", "Calibri")
    theme_part._blob = etree.tostring(theme, xml_declaration=True, encoding="UTF-8", standalone=True)

    el = master.element
    # Headline: Calibri bold 24 pt, vertically centred.
    title_lvl = el.find("p:txStyles/p:titleStyle/a:lvl1pPr", NS)
    rpr = title_lvl.find("a:defRPr", NS)
    rpr.set("sz", "2400")
    rpr.set("b", "1")
    for sp in el.findall("p:cSld/p:spTree/p:sp", NS):
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is not None and ph.get("type") == "title":
            sp.find("p:txBody/a:bodyPr", NS).set("anchor", "ctr")
    # Body: 16 pt at every level, one bullet glyph and indent per level.
    body = el.find("p:txStyles/p:bodyStyle", NS)
    for i, (char, font, margin, before) in enumerate(LEVELS, start=1):
        old = body.find(f"a:lvl{i}pPr", NS)
        new = etree.Element(A + f"lvl{i}pPr", marL=str(int(margin * EMU)), indent=str(int(-0.2 * EMU)), algn="l")
        _sub(_sub(new, "lnSpc"), "spcPct", val="100000")
        _sub(_sub(new, "spcBef"), "spcPts", val=str(before * 100))
        _sub(new, "buFont", typeface=font)
        _sub(new, "buChar", char=char)
        default = _sub(new, "defRPr", sz="1600")
        _sub(_sub(default, "solidFill"), "schemeClr", val="tx1")
        _sub(default, "latin", typeface="+mn-lt")
        body.replace(old, new)
    # Title and body boxes start 1 inch from the left edge, as the guideline asks.
    for tree in [el] + [layout.element for layout in master.slide_layouts]:
        for sp in tree.findall("p:cSld/p:spTree/p:sp", NS):
            ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
            off, ext = sp.find("p:spPr/a:xfrm/a:off", NS), sp.find("p:spPr/a:xfrm/a:ext", NS)
            if ph is None or off is None or ph.get("type") in ("dt", "ftr", "sldNum"):
                continue
            off.set("x", str(EMU)); ext.set("cx", str(8 * EMU))
    # Footer placeholder holds the confidentiality statement at 8 pt.
    for sp in el.findall("p:cSld/p:spTree/p:sp", NS):
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is not None and ph.get("type") == "ftr":
            _set_text(sp, STATEMENT, size=800)


def _set_text(sp, value: str, size: int | None = None) -> None:
    tx = sp.find("p:txBody", NS)
    for p in tx.findall("a:p", NS):
        tx.remove(p)
    p = _sub(tx, "p")
    r = _sub(p, "r")
    rpr = _sub(r, "rPr", lang="en-GB", dirty="0")
    if size:
        rpr.set("sz", str(size))
    t = _sub(r, "t")
    t.text = value


def _blank(prs: Presentation) -> Presentation:
    for sld in list(prs.slides._sldIdLst):
        prs.part.drop_rel(sld.rId)
        prs.slides._sldIdLst.remove(sld)
    return prs


def _add_statement(slide) -> None:
    """Copy the layout's footer placeholder onto the slide, with the statement, as real decks do."""
    footer = next(s for s in slide.slide_layout.placeholders if s.placeholder_format.type == 15)  # FOOTER
    sp = copy.deepcopy(footer._element)
    _set_text(sp, STATEMENT, size=800)
    slide.shapes._spTree.append(sp)


def _title_slide(prs, title: str, authors: str, date: str):
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = title
    s.placeholders[1].text = date
    if authors:
        box = s.shapes.add_textbox(Inches(1), Inches(5.6), Inches(8), Inches(0.5))
        box.text_frame.text = authors
        run = box.text_frame.paragraphs[0].runs[0]
        run.font.size, run.font.name = Pt(16), "Calibri"
    return s


def _content(prs, headline: str, bullets: list[tuple[int, str]] | None = None, statement: bool = True):
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = headline
    body = s.placeholders[1]
    if bullets:
        tf = body.text_frame
        tf.text = bullets[0][1]
        tf.paragraphs[0].level = bullets[0][0]
        for level, text in bullets[1:]:
            p = tf.add_paragraph()
            p.text, p.level = text, level
    else:
        body._element.getparent().remove(body._element)
    if statement:
        _add_statement(s)
    return s


def _fill(cell, hex_colour: str | None) -> None:
    if hex_colour is None:
        cell.fill.background()
    else:
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(hex_colour)


def _table(slide, caption: str, header: list[str], rows: list[list[str]], *, header_fill=BLUE,
           stats=(), client_row=None, size=11):
    box = slide.shapes.add_textbox(Inches(1), Inches(1.6), Inches(8), Inches(0.4))
    p = box.text_frame.paragraphs[0]
    p.text = caption
    p.alignment = 2  # centre
    p.runs[0].font.size, p.runs[0].font.bold, p.runs[0].font.name = Pt(14), True, "Calibri"
    n = len(rows) + 1
    shape = slide.shapes.add_table(n, len(header), Inches(1), Inches(2.1), Inches(8), Inches(0.3 * n))
    table = shape.table
    for c, label in enumerate(header):
        cell = table.cell(0, c)
        cell.text = label
        _fill(cell, header_fill)
        run = cell.text_frame.paragraphs[0].runs[0]
        run.font.bold, run.font.size, run.font.name = True, Pt(size), "Calibri"
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for r, values in enumerate(rows, start=1):
        if client_row is not None and r - 1 == client_row:
            colour = CLIENT
        elif r - 1 in stats:
            colour = STATS
        else:
            colour = None if (r - 1) % 2 == 0 else ZEBRA
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            cell.text = value
            cell.margin_left = Inches(0.03) if c == 0 else cell.margin_left
            _fill(cell, colour)
            run = cell.text_frame.paragraphs[0].runs[0]
            run.font.size, run.font.name = Pt(size), "Calibri"
            run.font.color.rgb = RGBColor(0, 0, 0)
    return shape


PEERS = [["Alder Robotics", "$1,240", "8.1%"], ["Birchway Foods", "$980", "6.4%"],
         ["Cedar Logistics", "$1,515", "9.2%"], ["Delmont Health", "$1,105", "7.0%"],
         ["Northwind Traders", "$1,320", "8.8%"]]
STATS_ROWS = [["25th %ile", "$1,105", "7.0%"], ["Median", "$1,240", "8.1%"], ["75th %ile", "$1,320", "8.8%"]]


def build_template() -> Presentation:
    prs = _blank(Presentation())
    _style_master(prs)
    return prs


def build_clean(path: Path) -> None:
    prs = build_template()
    s = _title_slide(prs, "Northwind Traders executive pay review", "Prepared by Ada Osei and Sam Lee", "20 November 2026")
    _add_statement(s)
    _content(prs, "Northwind Traders' base salaries sit at the peer median, so no market adjustment is needed",
             [(0, "Base salaries were benchmarked against five peers of similar revenue"),
              (1, "Peers were selected on industry, size and talent market"),
              (1, "Data reflects the most recent public filings"),
              (0, "Salaries for all five executives fall within 5% of the median"),
              (0, "Annual incentive targets remain in line with peer practice")])
    s = _content(prs, "Northwind Traders' revenue growth is above the peer median")
    _table(s, "Revenue and growth of the peer group", ["Company", "Revenue ($MM)", "Growth"],
           PEERS[:4] + STATS_ROWS + [PEERS[4]], stats=(4, 5, 6), client_row=7)
    prs.save(str(path))


def build_with_issues(path: Path) -> None:
    prs = build_template()
    # 1. Title slide still marked DRAFT and has no authors.
    s = _title_slide(prs, "DRAFT Northwind Traders executive pay review", "", "20 November 2026")
    _add_statement(s)
    # 2. Headline ends in a full stop; one bullet is set at 12 pt; one bullet ends in punctuation.
    s = _content(prs, "Northwind Traders' base salaries sit at the peer median.",
                 [(0, "Base salaries were benchmarked against five peers of similar revenue"),
                  (0, "Salaries for all five executives fall within 5% of the median."),
                  (0, "Annual incentive targets remain in line with peer practice")])
    s.placeholders[1].text_frame.paragraphs[2].runs[0].font.size = Pt(12)
    # 3. Table: off-brand header fill, "Percentile" instead of "%ile", "N/A" for a missing value, "TGT".
    s = _content(prs, "Northwind Traders' revenue growth is above the peer median")
    rows = [list(r) for r in PEERS[:4]] + [["25th Percentile", "$1,105", "7.0%"], ["Median", "$1,240", "8.1%"],
                                           ["75th Percentile", "$1,320", "8.8%"], ["Northwind Traders", "$1,320", "N/A"]]
    _table(s, "Revenue and TGT growth of the peer group", ["Company", "Revenue ($MM)", "Growth"], rows,
           header_fill="1F4E79", stats=(4, 5, 6), client_row=7)
    # 4. Confidentiality statement missing.
    _content(prs, "Long-term incentives are weighted toward performance shares, in line with peers",
             [(0, "Performance shares make up 60% of the long-term award"),
              (0, "Restricted stock makes up the remaining 40%")], statement=False)
    # 5. Off-palette colour and a reviewer comment left in the deck.
    s = _content(prs, "Three changes are recommended for the 2027 programme",
                 [(0, "Raise the CEO's target bonus from 100% to 110% of salary"),
                  (0, "Add a relative TSR modifier to performance shares"),
                  (0, "Align the peer group review with the annual budget cycle")])
    note = s.shapes.add_textbox(Inches(1), Inches(6.2), Inches(6), Inches(0.4))
    note.text_frame.text = "TODO: check the bonus figures with finance"
    run = note.text_frame.paragraphs[0].runs[0]
    run.font.size, run.font.name = Pt(12), "Calibri"
    run.font.color.rgb = RGBColor.from_string("FF6600")
    # 6. Topic-only headline and bullets that aren't parallel (judgement calls for the AI review).
    _content(prs, "Peer group overview",
             [(0, "Reviewed the peer group against industry and size"),
              (0, "The talent market was also something that the committee considered in detail"),
              (0, "To confirm the data with the latest filings")])
    prs.save(str(path))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    build_template().save(str(OUT / "example-template.pptx"))
    build_clean(OUT / "demo-clean.pptx")
    build_with_issues(OUT / "demo-with-issues.pptx")
    print("Wrote", ", ".join(p.name for p in sorted(OUT.glob("*.pptx"))))
