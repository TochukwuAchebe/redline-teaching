"""Design upgrades: an optional, on-brand redesign of a data slide.

Guideline fixes make a slide correct; an upgrade makes it clearer. The first upgrade
turns a long data table into a bar chart with a headline that states the takeaway:

- Claude chooses what to chart (label column, value column, rows to highlight) and
  writes the headline. It never supplies figures: the chart is drawn here from the
  table itself, and every number in the headline must be traceable to the slide.
- The chart follows the guideline's chart rules: palette colours only (ACME Blue for
  the highlighted rows, grey for the rest), minimal ink, a 14 pt bold centred title
  and a 9 pt italic source note directly below.
- The original table moves, unchanged, to a new appendix slide, so no figure leaves
  the deck.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import statistics
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from app.model.types import Deck, Element

log = logging.getLogger("redline.upgrade")

DEFAULT_MODEL = "claude-opus-5-5"
MIN_ROWS = 5
BLUE, GREY, LABEL, DARK, BAND, RED = "#006EBE", "#BFBFBF", "#404040", "#595959", "#F2F2F2", "#E80000"
STAT_ROW = re.compile(r"^(?:\d{1,2}(?:st|nd|rd|th)\s*(?:%ile|percentile)|median|average|mean|count|total|min(?:imum)?|max(?:imum)?|n)\s*(?:\(.*\))?\s*:?$", re.I)
NUMBER = re.compile(r"^\(?([-+]?)\$?\s*(\d[\d,]*(?:\.\d+)?)\s*\)?\s*([%xX]?)$")
TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
FONT_DIRS = ["/usr/share/fonts/truetype/crosextra", "/usr/share/fonts", "/Library/Fonts",
             "/System/Library/Fonts/Supplemental", str(Path.home() / "Library/Fonts")]
FONT_NAMES = {False: ["Carlito-Regular.ttf", "Calibri.ttf", "Arial.ttf", "DejaVuSans.ttf"],
              True: ["Carlito-Bold.ttf", "Calibri Bold.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]}

RULES = [
    "Calibri throughout",
    "Headline: bold 24 pt, no end punctuation",
    "Chart title: bold 14 pt, centred above the chart",
    "Only palette colours: ACME Blue for the highlight, grey for the rest",
    "Source note: 9 pt italic, directly below the chart",
    "Confidentiality statement unchanged",
]


class NotUpgradable(Exception):
    """This slide has no safe, grounded redesign."""


# --- Reading the table -----------------------------------------------------------------

def parse_number(text: str) -> tuple[float, str] | None:
    """'$10,065' -> (10065, '$'); '17.8X' -> (17.8, 'x'); '5.6%' -> (5.6, '%')."""
    m = NUMBER.match(text.strip())
    if not m:
        return None
    value = float(m.group(2).replace(",", ""))
    if m.group(1) == "-" or text.strip().startswith("("):
        value = -value
    unit = "$" if "$" in text else m.group(3).lower()
    return value, unit


def decimals(text: str) -> int:
    m = re.search(r"\.(\d+)", text)
    return len(m.group(1)) if m else 0


def grid(el: Element) -> tuple[list[str], list[list[str]]]:
    """Header labels per column, and the body rows as text."""
    t = el.table
    cells = [["" for _ in range(t.n_cols)] for _ in range(t.n_rows)]
    for c in t.cells:
        if not c.is_spanned and c.row < t.n_rows and c.col < t.n_cols:
            cells[c.row][c.col] = " ".join(c.text.split())
    head = max(1, t.header_rows)
    headers = [" ".join(x for x in (cells[r][col] for r in range(head)) if x) for col in range(t.n_cols)]
    return headers, cells[head:]


def describe(el: Element) -> dict | None:
    """Columns that could be charted, or None when the table isn't a chartable list."""
    if not el.table or el.table.n_cols < 2:
        return None
    headers, rows = grid(el)
    data = [r for r in rows if any(r) and not STAT_ROW.match((r[0] or "").strip())]
    if len(data) < MIN_ROWS:
        return None
    numeric, labels = [], []
    for col in range(el.table.n_cols):
        values = [parse_number(r[col]) for r in data if r[col]]
        parsed = [v for v in values if v]
        if len(parsed) >= MIN_ROWS and len(parsed) >= 0.8 * len(data) and all(v[0] >= 0 for v in parsed):
            numeric.append(col)
        elif sum(1 for r in data if r[col] and not parse_number(r[col])) >= MIN_ROWS:
            labels.append(col)
    if not numeric or not labels:
        return None
    return {"element_id": el.id, "headers": headers, "rows": rows, "numeric": numeric, "labels": labels}


def candidates(deck: Deck) -> list[dict]:
    """One chartable table per slide: the largest."""
    out = []
    for slide in deck.slides:
        if slide.hidden or slide.layout_kind in {"title", "section"}:
            continue
        found = [(d, e) for e in slide.elements if e.table and (d := describe(e))]
        if found:
            d, e = max(found, key=lambda x: x[1].bbox.w * x[1].bbox.h)
            out.append({"slide": slide.index, "element_id": e.id, "rows": len(d["rows"])})
    return out


def series(table: dict, label_col: int, value_col: int) -> list[tuple[str, float, str]]:
    out = []
    for r in table["rows"]:
        label, raw = r[label_col].strip(), r[value_col].strip()
        if not label or not raw or STAT_ROW.match((r[0] or "").strip()) or STAT_ROW.match(label):
            continue
        parsed = parse_number(raw)
        if parsed:
            out.append((label, parsed[0], raw))
    return out


def facts(points: list[tuple[str, float, str]]) -> dict:
    values = [v for _, v, _ in points]
    q = statistics.quantiles(values, n=4, method="inclusive")
    return {"median": statistics.median(values), "p25": q[0], "p75": q[2], "max": max(values), "min": min(values)}


def stated_stats(table: dict, value_col: int) -> dict:
    """Median and 25th/75th %ile rows the table already states, so the chart agrees with it."""
    found = {}
    for r in table["rows"]:
        label, parsed = (r[0] or "").lower(), parse_number(r[value_col]) if r[value_col] else None
        if not parsed:
            continue
        if "median" in label:
            found["median"] = parsed[0]
        elif re.search(r"\b25(th)?\b", label) and re.search(r"%ile|percentile", label):
            found["p25"] = parsed[0]
        elif re.search(r"\b75(th)?\b", label) and re.search(r"%ile|percentile", label):
            found["p75"] = parsed[0]
    return found


# --- Grounding the headline -------------------------------------------------------------

def grounded(headline: str, slide_text: str, points: list[tuple[str, float, str]], stats: dict, highlight: list[str] | None = None) -> bool:
    """Every number in the headline must be on the slide, a summary statistic of the
    charted column, or a highlighted value divided by one of those statistics."""
    known = {float(t.replace(",", "")) for t in TOKEN.findall(slide_text)}
    known |= set(stats.values())
    known |= {v / s for label, v, _ in points if label in (highlight or []) for s in (stats["median"], stats["p25"], stats["p75"]) if s}
    for token in TOKEN.findall(headline):
        value, places = float(token.replace(",", "")), decimals(token)
        # Rounding is allowed only at the precision written ("2.5x" for 2.54x); a whole number
        # must be (almost) exact, so "2x" can't stand for 1.8x.
        tolerance = 0.5 * 10 ** -places if places else 0.05
        if not any(abs(k - value) <= tolerance + 1e-9 for k in known):
            return False
    return True


# --- Asking Claude for the design --------------------------------------------------------

# The guideline lines a redesign serves. The AI is given these and nothing else to work from,
# and the review panel shows the reviewer which line each change follows.
GUIDELINE_BASIS = [
    ("Headline states the takeaway", "Slide Title: titles communicate the takeaway, not just the topic. Declarative, insight-driven headlines."),
    ("Chart where a table hides the point", "Tables favor clarity over density. Charts: picture from Excel or another tool; all colours from the ACME palette."),
    ("Blue for the key data point, grey for the rest", "Colour usage: blue accent used for the key data point. Chart design: blue primary series, grey secondary series, minimal gridlines."),
    ("Red box only for what the headline names", "Emphasis: red boxes attract attention; information in the box is referenced in the headline."),
    ("Chart title and source note", "Chart title: Calibri Bold 14 pt, centred above the chart. Chart source notes: Calibri italic 9 pt, directly below the chart."),
    ("Table kept as a native table", "Tables are PowerPoint native tables, not pasted pictures: the full table moves unchanged to an appendix slide."),
]

SYSTEM = """You suggest a redesign of one data slide from an ACME deck. A person reviews your suggestion and decides whether to accept it.
Work only from the ACME guideline lines below and the slide content you are given. Do not invent standards, facts or figures.
ACME guideline lines that apply:
- Slide title: titles communicate the takeaway, not just the topic; declarative, insight-driven headlines; sentence case; no ending punctuation; no more than 3 lines.
- Writing: board-ready, professional tone; minimal jargon; established compensation terminology; focus on implications, not narration.
- Tables favor clarity over density.
- Charts: blue accent for the key data point, grey for the rest; red boxes only for information the headline references.
The slide has a long table. Suggest what to show as a horizontal bar chart and write the new headline.
Rules:
- label_column and value_column are 0-based indexes from the columns given; value_column must be one of the numeric columns.
- Choose the comparison that carries the slide's message. Highlight at most 3 rows, using their exact labels, usually the subject of the slide.
- headline: one sentence stating the takeaway, at most 110 characters, sentence case, no ending punctuation. Use only numbers that appear on the slide or in the computed facts provided (you may round them). Never invent figures, causes or claims.
- chart_title: a short noun phrase naming the measure, at most 60 characters, no ending punctuation.
Call propose_design exactly once."""

TOOL = {
    "name": "propose_design",
    "description": "Return the redesign for this slide.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["label_column", "value_column", "highlight", "headline", "chart_title"],
        "properties": {
            "label_column": {"type": "integer"},
            "value_column": {"type": "integer"},
            "highlight": {"type": "array", "items": {"type": "string"}},
            "headline": {"type": "string"},
            "chart_title": {"type": "string"},
        },
    },
}


def claude_chooser(payload: dict) -> dict:
    """Ask Claude for the design choices; raises NotUpgradable when unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise NotUpgradable("Design suggestions are switched off on this server.")
    import anthropic

    client = anthropic.Anthropic(timeout=120, max_retries=2)
    model = os.environ.get("UPGRADE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "auto"},  # forced tool choice isn't accepted on this model
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.APIError as exc:
        log.warning("upgrade request failed: %s", exc)
        raise NotUpgradable("The design service didn't respond. Try again shortly.")
    if response.stop_reason == "refusal":
        raise NotUpgradable("No design suggestion for this slide.")
    for block in response.content:
        if block.type == "tool_use" and block.name == TOOL["name"]:
            return {**dict(block.input), "model": response.model}
    raise NotUpgradable("No design suggestion for this slide.")


def propose(deck: Deck, slide_no: int, chooser: Callable[[dict], dict] | None = None) -> dict:
    chooser = chooser or claude_chooser
    slide = deck.slides[slide_no - 1]
    found = [d for e in slide.elements if e.table and (d := describe(e))]
    if not found:
        raise NotUpgradable("This slide has no table that reads better as a chart.")
    el_by_id = {e.id: e for e in slide.elements}
    table = max(found, key=lambda d: el_by_id[d["element_id"]].bbox.w * el_by_id[d["element_id"]].bbox.h)
    title = next((e.text for e in slide.elements if e.role == "title"), "")
    slide_text = "\n".join(e.text for e in slide.elements if e.text) + "\n" + "\n".join(
        " | ".join(r) for r in table["rows"])
    stats_by_col = {}
    for col in table["numeric"]:
        for lab in table["labels"][:1]:
            pts = series(table, lab, col)
            if len(pts) >= MIN_ROWS:
                stats_by_col[col] = {k: round(v, 2) for k, v in facts(pts).items()}
    payload = {
        "headline": title,
        "other_text": [e.text for e in slide.elements if e.text and e.role not in {"title", "confidentiality", "slide_number"}][:12],
        "columns": [{"index": i, "header": h, "numeric": i in table["numeric"]} for i, h in enumerate(table["headers"])],
        "rows": table["rows"][:60],
        "computed_facts": {table["headers"][c] or f"column {c}": s for c, s in stats_by_col.items()},
    }
    choice = chooser(payload)
    label_col, value_col = int(choice.get("label_column", -1)), int(choice.get("value_column", -1))
    if value_col not in table["numeric"] or not 0 <= label_col < len(table["headers"]) or label_col == value_col:
        raise NotUpgradable("The suggested chart didn't match the table.")
    points = series(table, label_col, value_col)
    if len(points) < MIN_ROWS:
        raise NotUpgradable("The suggested chart didn't have enough rows.")
    labels = {p[0] for p in points}
    highlight = [h for h in choice.get("highlight", []) if h in labels][:3]
    peers = [p for p in points if p[0] not in highlight]
    stats = facts(peers if len(peers) >= MIN_ROWS else points)
    stats.update(stated_stats(table, value_col))  # the table's own median and %iles win
    headline = re.sub(r"[.:;!?]+$", "", " ".join(str(choice.get("headline", "")).split()))
    if not headline or len(headline) > 120 or not grounded(headline, slide_text, points, stats, highlight):
        raise NotUpgradable("The suggested headline used figures that aren't on the slide.")
    chart_title = re.sub(r"[.:;!?]+$", "", " ".join(str(choice.get("chart_title", "")).split()))[:60] or table["headers"][value_col]
    caption = next((e.text for e in slide.elements if e.role == "table_caption"), "").strip()
    return {
        "slide": slide_no,
        "element_id": table["element_id"],
        "label_column": label_col,
        "value_column": value_col,
        "highlight": highlight,
        "headline": headline,
        "original_headline": title,
        "chart_title": chart_title,
        "appendix_title": "Appendix: " + (caption or chart_title),
        "points": [[label, value, raw] for label, value, raw in points],
        "stats": {k: round(v, 4) for k, v in stats.items()},
        "model": choice.get("model", ""),
        # The guideline: red boxes only for information the headline references.
        "red_box": [h for h in highlight if _named(h, headline)],
        "basis": [{"change": c, "guideline": g} for c, g in GUIDELINE_BASIS],
    }


def _named(label: str, headline: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", label) if len(w) > 2]
    return bool(words) and words[0].lower() in headline.lower()


# --- Drawing the chart --------------------------------------------------------------------

def _font(size: int, bold: bool = False):
    for folder in FONT_DIRS:
        for name in FONT_NAMES[bold]:
            path = Path(folder) / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
            for found in Path(folder).rglob(name) if Path(folder).is_dir() and folder.startswith("/usr") else []:
                return ImageFont.truetype(str(found), size)
    return ImageFont.load_default(size)


def _format(value: float, sample: str) -> str:
    unit = parse_number(sample)[1]
    places = decimals(sample)
    body = f"{value:,.{places}f}"
    return "$" + body if unit == "$" else body + ("%" if unit == "%" else "x" if unit == "x" else "")


def draw_chart(proposal: dict, path: Path, aspect: float) -> None:
    """Horizontal bars, largest first: highlight in ACME Blue, others grey, median and
    the 25th-75th %ile range marked. Drawn at 3x for crisp text when projected."""
    points = sorted(proposal["points"], key=lambda p: -p[1])
    stats, highlight = proposal["stats"], set(proposal["highlight"])
    W = 2400
    H = max(900, int(W / aspect))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    n = len(points)
    foot = 90
    row = (H - foot - 20) / n
    size = int(max(24, min(46, row * 0.62)))
    f, fb, small = _font(size), _font(size, True), _font(int(size * 0.9))
    label_w = max(d.textlength(p[0], font=fb if p[0] in highlight else f) for p in points) + 30
    value_w = max(d.textlength(p[2], font=fb) for p in points) + 30
    x0, x1 = label_w, W - value_w
    top = 10
    scale = (x1 - x0) / (max(p[1] for p in points) or 1)
    bottom = top + row * n
    d.rectangle([x0 + stats["p25"] * scale, top, x0 + stats["p75"] * scale, bottom], fill=BAND)
    mx = x0 + stats["median"] * scale
    for y in range(int(top), int(bottom), 18):
        d.line([mx, y, mx, min(y + 10, bottom)], fill=LABEL, width=3)
    for i, (label, value, raw) in enumerate(points):
        y = top + i * row
        hi = label in highlight
        bar = (y + row * 0.18, y + row * 0.82)
        d.text((x0 - 18, (bar[0] + bar[1]) / 2), label, font=fb if hi else f, fill="#000000" if hi else LABEL, anchor="rm")
        d.rectangle([x0, bar[0], x0 + max(2, value * scale), bar[1]], fill=BLUE if hi else GREY)
        tx, ty = x0 + value * scale + 12, (bar[0] + bar[1]) / 2
        box = d.textbbox((tx, ty), raw, font=fb if hi else small, anchor="lm")
        d.rectangle([box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2], fill="white")  # keep the median line off the figure
        d.text((tx, ty), raw, font=fb if hi else small, fill=BLUE if hi else DARK, anchor="lm")
        if label in set(proposal.get("red_box", proposal["highlight"])):
            d.rectangle([6, y + 2, min(W - 6, x0 + value * scale + value_w), y + row - 2], outline=RED, width=5)
    sample = points[0][2]
    d.text((mx, bottom + 18), f"Median {_format(stats['median'], sample)}", font=small, fill=LABEL, anchor="mt")
    d.text((W - 10, bottom + 18), f"Shaded: 25th–75th %ile ({_format(stats['p25'], sample)}–{_format(stats['p75'], sample)})",
           font=small, fill="#808080", anchor="rt")
    img.save(path, "PNG", optimize=True)


# --- Writing it into the deck ----------------------------------------------------------------

PROTECTED = {"title", "confidentiality", "slide_number"}


def _set_text(shape_el, text: str) -> None:
    body = shape_el.find(".//" + qn("p:txBody"))
    paragraphs = body.findall(qn("a:p"))
    runs = paragraphs[0].findall(qn("a:r"))
    if runs:
        runs[0].find(qn("a:t")).text = text
        for extra in runs[1:]:
            paragraphs[0].remove(extra)
    else:
        r = paragraphs[0].makeelement(qn("a:r"), {})
        t = r.makeelement(qn("a:t"), {})
        t.text = text
        r.append(t)
        paragraphs[0].append(r)
    for extra in paragraphs[1:]:
        body.remove(extra)


def _textbox(slide, x, y, w, h, text, size, bold=False, italic=False, center=False):
    box = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
    tf = box.text_frame
    tf.word_wrap = True
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, 0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER if center else PP_ALIGN.LEFT
    run = p.add_run()
    run.text = text
    run.font.name, run.font.size, run.font.bold, run.font.italic = "Calibri", Pt(size), bold, italic
    return box


def _shape(slide, shape_id: int):
    for nv in slide.shapes._spTree.iter(qn("p:cNvPr")):
        if nv.get("id") == str(shape_id):
            return nv.getparent().getparent()
    raise NotUpgradable("The table is no longer on the slide.")


def apply(pptx: Path, deck: Deck, proposal: dict, chart_png: Path) -> int:
    """Rewrite `pptx` in place with the upgrade; returns the new appendix slide number."""
    prs = Presentation(str(pptx))
    n = proposal["slide"]
    slide, model = prs.slides[n - 1], deck.slides[n - 1]
    elements = {e.id: e for e in model.elements}
    table = elements.get(proposal["element_id"])
    if table is None:
        raise NotUpgradable("The table is no longer on the slide.")
    caption = next((e for e in model.elements if e.role == "table_caption"
                    and e.bbox.y + e.bbox.h <= table.bbox.y + table.bbox.h * 0.1
                    and e.bbox.x < table.bbox.x + table.bbox.w and e.bbox.x + e.bbox.w > table.bbox.x), None)
    conf = next((e for e in model.elements if e.role == "confidentiality"), None)
    top = caption.bbox.y if caption else table.bbox.y
    floor = conf.bbox.y if conf else deck.slide_height
    left, right = table.bbox.x - 0.02 * deck.slide_width, table.bbox.x + table.bbox.w

    def belongs(e: Element) -> bool:  # the table and what annotates it: caption, key, notes beneath
        cx, cy = e.bbox.x + e.bbox.w / 2, e.bbox.y + e.bbox.h / 2
        return e.id == table.id or (e.role not in PROTECTED and not e.group_path
                                    and left <= cx <= right and top <= cy <= floor)

    movers = [e for e in model.elements if belongs(e)]
    nodes = [_shape(slide, e.shape_id) for e in movers]
    if any(node.xpath(".//@r:embed | .//@r:id | .//@r:link") for node in nodes):
        raise NotUpgradable("Linked pictures sit inside the table area; this slide is left as it is.")

    appendix = prs.slides.add_slide(slide.slide_layout)
    for ph in list(appendix.placeholders):
        if ph.placeholder_format.type is not None and "TITLE" in str(ph.placeholder_format.type):
            ph.text_frame.text = proposal["appendix_title"]
        else:
            ph._element.getparent().remove(ph._element)
    tree = appendix.shapes._spTree
    for e in [x for x in model.elements if x.role in {"confidentiality", "slide_number"}]:
        tree.append(copy.deepcopy(_shape(slide, e.shape_id)))
    copies = {}
    for e, node in zip(movers, nodes):
        copies[e.id] = copy.deepcopy(node)
        tree.append(copies[e.id])
        node.getparent().remove(node)
    _clear_of_table(table, [e for e in movers if e.id != table.id and e is not caption], copies, deck)

    title = next((e for e in model.elements if e.role == "title"), None)
    if title is not None:
        _set_text(_shape(slide, title.shape_id), proposal["headline"])

    region_w, gap = right - table.bbox.x, Emu(Pt(4))
    caption_h, source_h = Emu(Pt(22)), Emu(Pt(14))
    _textbox(slide, table.bbox.x, top, region_w, caption_h, proposal["chart_title"], 14, bold=True, center=True)
    chart_top = top + caption_h + gap
    chart_h = max(Emu(Pt(72)), floor - chart_top - source_h - 2 * gap)
    slide.shapes.add_picture(str(chart_png), Emu(table.bbox.x), Emu(chart_top), Emu(region_w), Emu(chart_h))
    # Keep the slide's own source line when it has one; otherwise cite the table the chart is drawn from.
    stated = next((e.text.strip() for e in movers if e.role == "source_note" and e.text.strip()), "")
    origin = stated.rstrip(".") if stated else f"Data: {proposal['appendix_title'].removeprefix('Appendix: ')}"
    source = f"{origin}; full table in the appendix (slide {len(prs.slides)})"
    _textbox(slide, table.bbox.x, chart_top + chart_h + gap, region_w, source_h, source, 9, italic=True)
    prs.save(str(pptx))
    return len(prs.slides)


def _overlaps(a, b) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def _clear_of_table(table: Element, others: list[Element], copies: dict, deck: Deck) -> None:
    """A key or note laid over the table hides its rows. On the appendix slide, move what
    covers the table into clear space: beside it when there is room, otherwise below it."""
    covering = [e for e in others if e.role in {"key_label", "key_item"} or _overlaps(e.bbox, table.bbox)]
    if not covering:
        return
    # A rectangle drawn around the key travels with it.
    ux0, uy0 = min(e.bbox.x for e in covering), min(e.bbox.y for e in covering)
    ux1, uy1 = max(e.bbox.x + e.bbox.w for e in covering), max(e.bbox.y + e.bbox.h for e in covering)
    for e in others:
        b = e.bbox
        inside = max(0, min(b.x + b.w, ux1) - max(b.x, ux0)) * max(0, min(b.y + b.h, uy1) - max(b.y, uy0))
        if e not in covering and inside >= 0.9 * (ux1 - ux0) * (uy1 - uy0):
            covering.append(e)
    left = min(e.bbox.x for e in covering)
    top = min(e.bbox.y for e in covering)
    width = max(e.bbox.x + e.bbox.w for e in covering) - left
    height = max(e.bbox.y + e.bbox.h for e in covering) - top
    gap, margin = int(Emu(Pt(14))), int(deck.slide_width * 0.04)
    if table.bbox.x + table.bbox.w + gap + width <= deck.slide_width - margin:
        dx, dy = table.bbox.x + table.bbox.w + gap - left, table.bbox.y - top
    else:
        dx, dy = 0, table.bbox.y + table.bbox.h + gap - top
    for e in covering:
        off = copies[e.id].find(".//" + qn("a:off"))
        if off is not None:
            off.set("x", str(int(off.get("x")) + int(dx)))
            off.set("y", str(int(off.get("y")) + int(dy)))


def chart_aspect(deck: Deck, proposal: dict) -> float:
    """Width / height of the space the chart gets, so the picture is never stretched."""
    model = deck.slides[proposal["slide"] - 1]
    table = next(e for e in model.elements if e.id == proposal["element_id"])
    caption = next((e for e in model.elements if e.role == "table_caption"
                    and e.bbox.y + e.bbox.h <= table.bbox.y + table.bbox.h * 0.1), None)
    conf = next((e for e in model.elements if e.role == "confidentiality"), None)
    top = (caption.bbox.y if caption else table.bbox.y) + Pt(30)
    floor = (conf.bbox.y if conf else deck.slide_height) - Pt(22)
    return (table.bbox.w + 0.02 * deck.slide_width) / max(1, floor - top)


def keep_only(pptx: Path, keep: set[int]) -> None:
    """Drop every slide not in `keep` (1-based), for a fast preview render."""
    prs = Presentation(str(pptx))
    ids = prs.slides._sldIdLst
    for i, sld in reversed(list(enumerate(list(ids), 1))):
        if i not in keep:
            prs.part.drop_rel(sld.rId)
            ids.remove(sld)
    prs.save(str(pptx))


def _freeze_number(el, text: str) -> None:
    for fld in [f for f in el.iter(qn("a:fld")) if f.get("type") == "slidenum"]:
        run = fld.makeelement(qn("a:r"), {})
        run.extend(copy.deepcopy(c) for c in fld if c.tag == qn("a:rPr"))
        run.append(fld.makeelement(qn("a:t"), {}))
        run[-1].text = text
        fld.addprevious(run)
        fld.getparent().remove(fld)


def freeze_slide_numbers(pptx: Path, numbers: list[int]) -> None:
    """After keep_only, show each kept slide's real number (numbers[k] for slide k+1) instead of a
    live field that would count from 1 in the cut-down preview deck. Render-only: run after checking."""
    prs = Presentation(str(pptx))
    slides = list(prs.slides)
    for slide, real in zip(slides, numbers):
        _freeze_number(slide._element, str(real))
    for master in prs.slide_masters:
        for sh in master.shapes:
            el = sh._element
            if sh.is_placeholder or not any(f.get("type") == "slidenum" for f in el.iter(qn("a:fld"))):
                continue
            for slide, real in zip(slides, numbers):
                if slide.slide_layout.slide_master is master:
                    shown = copy.deepcopy(el)
                    _freeze_number(shown, str(real))
                    next(shown.iter(qn("p:cNvPr"))).set("id", str(max(s.shape_id for s in slide.shapes) + 1))
                    slide.shapes._spTree.append(shown)
            _freeze_number(el, "")
    prs.save(str(pptx))


def preview(original: Path, deck: Deck, proposal: dict, out_dir: Path, render) -> None:
    """Render the upgraded slide and its appendix to out_dir/{slide}.png and {slide}-appendix.png."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="redline-upgrade-") as tmp:
        work = Path(tmp) / "deck.pptx"
        shutil.copyfile(original, work)
        chart = Path(tmp) / "chart.png"
        draw_chart(proposal, chart, chart_aspect(deck, proposal))
        appendix = apply(work, deck, proposal, chart)
        keep_only(work, {proposal["slide"], appendix})
        freeze_slide_numbers(work, sorted({proposal["slide"], appendix}))
        pages = Path(tmp) / "pages"
        render(work, pages)
        shutil.copyfile(pages / "1.png", out_dir / f"{proposal['slide']}.png")
        shutil.copyfile(pages / "2.png", out_dir / f"{proposal['slide']}-appendix.png")


def apply_chosen(fixed: Path, upgrades: dict, workdir: Path) -> dict:
    """Apply every upgrade the reviewer chose to the corrected deck. Per slide: applied or failed."""
    from app.model.extract import extract_deck

    results = {}
    for key, item in sorted(upgrades.items(), key=lambda kv: int(kv[0])):
        proposal = item.get("proposal")
        if item.get("choice") != "use" or not proposal:
            continue
        try:
            deck = extract_deck(str(fixed))  # the corrected deck: fixes already in the table
            chart = workdir / f"chart-{key}.png"
            draw_chart(proposal, chart, chart_aspect(deck, proposal))
            appendix = apply(fixed, deck, proposal, chart)
            results[key] = {"status": "applied", "appendix_slide": appendix}
        except NotUpgradable as exc:
            results[key] = {"status": "failed", "note": str(exc)}
        except Exception:
            log.exception("upgrade for slide %s failed", key)
            results[key] = {"status": "failed", "note": "This redesign couldn't be applied; the slide is unchanged."}
    return results
