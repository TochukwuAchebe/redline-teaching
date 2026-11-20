"""Redesign a whole slide: the agent chooses, code writes every word and checks it.

The agent reads the slide (headline, its sentences, the data table and key) with the ACME
guideline and design principles, and chooses a layout, which figures to chart and which of
the slide's own sentences to show. It writes no text of its own. Every headline is the
slide's own headline, one of its sentences verbatim, or a comparison code computes from the
table (rank, or above or below the peer median, with the entity, measure and unit bound to
the data). Every bullet is a whole clause copied from the slide, so negation and
qualifications survive. Code renders the design into fixed ACME layouts and offers it only
when the redesigned slide has fewer guideline issues than the original. Meaning still needs
a person: the reviewer accepts or keeps the original.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from app.fix import layout
from app.model.types import Deck, Element, EMU_PER_INCH, Slide
from app.upgrade import (BLUE, DARK, GREY, LABEL, BAND, RED, MIN_ROWS, NotUpgradable, _font, _format, _set_text,
                         _shape, describe, facts, freeze_slide_numbers, keep_only, parse_number, series,
                         stated_stats)

log = logging.getLogger("redline.redesign")
IN = EMU_PER_INCH
DEFAULT_MODEL = "claude-opus-5-5"
ENGINE = 5  # raise when candidates or proposals change shape, so stored ones are recomputed
KEEP = {"title", "confidentiality", "slide_number"}
VISUAL = {"table", "picture", "chart", "graphic_frame", "group"}
NOTES = {"footnote", "source_note"}
CODE = re.compile(r"^[A-Z][A-Z0-9&]{0,5}$")

# Each design element and the ACME guideline line it serves; shown to the reviewer.
BASIS = [
    ("Headline states the takeaway", "Slide title: titles communicate the takeaway, not just the topic; declarative, insight-driven headlines."),
    ("One focal point, clear hierarchy", "Overall: clear separation between the slide title and body content; tables favor clarity over density."),
    ("Key beneath what it explains", "Charts and visuals: axis labels and legends are clearly labeled."),
    ("Palette colours only", "Colour usage: blue accent for the key data point, grey for the rest; all colours from the ACME palette."),
    ("Bullets quoted from the slide", "Bulleted lists: Calibri 16 pt, no ending punctuation; each bullet is the slide's own wording, unchanged."),
    ("Every figure kept", "Tables are PowerPoint native tables: the full table moves unchanged to an appendix slide."),
]

SYSTEM = """You are the Redline agent, a presentation designer for ACME, an executive-compensation firm. You suggest a redesign of ONE slide. A person reviews it and decides whether to accept it.

You do not write any slide text. You choose from what the slide already says and from comparisons that code has computed, and code writes the words exactly. This keeps every statement true to the slide.

ACME guideline (the parts that govern a slide's design):
- Headline: states the takeaway, not just the topic; declarative; at most 3 lines.
- Clear separation between headline and body; tables favor clarity over density; adequate white space; no overcrowding.
- Bullets: Calibri 16 pt; no ending punctuation; board-ready.
- Charts: blue accent for the key data point, grey for the rest; legends clearly labeled; red boxes only for information the headline names.

Design principles: one focal point per slide; generous white space; strong hierarchy (takeaway, then evidence, then detail); short, scannable text.

You choose:
- layout "chart_panels": a bar chart of one numeric table column (left) with up to 2 text panels (right). Use it when the slide has a table whose figures carry the message.
- layout "panels": 1 or 2 text panels side by side. Use it for text-heavy slides.
- headline_id: "H0" for the slide's own headline, a clause id ("C3") whose sentence states the slide's takeaway, or a template id ("T2") from headline_templates. A template may be used only with layout chart_panels and its own column as the value_column.
- chart: label_column and value_column are 0-based column indexes; value_column must be numeric. category_column is the column whose short codes the slide's key explains, or -1. highlight: up to 3 row labels exactly as written, usually the client.
- panels: each has a title chosen from title_options and 1 to 4 clause_ids, most important first. Use each clause at most once, and prefer short clauses.
Call propose_design exactly once."""

TOOL = {
    "name": "propose_design",
    "description": "Return the slide redesign as choices from the slide's own content.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["layout", "headline_id", "chart", "panels"],
        "properties": {
            "layout": {"type": "string", "enum": ["chart_panels", "panels"]},
            "headline_id": {"type": "string"},
            "chart": {"type": ["object", "null"], "additionalProperties": False,
                      "required": ["label_column", "value_column", "category_column", "highlight"],
                      "properties": {"label_column": {"type": "integer"}, "value_column": {"type": "integer"},
                                     "category_column": {"type": "integer"},
                                     "highlight": {"type": "array", "items": {"type": "string"}}}},
            "panels": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["title", "clause_ids"],
                                                  "properties": {"title": {"type": "string"},
                                                                 "clause_ids": {"type": "array", "items": {"type": "string"}}}}},
        },
    },
}

# Neutral panel labels that make no claim; the slide's own headings are offered as well.
TITLES = ["Overview", "Key findings", "Findings", "Context", "Approach", "Changes", "Criteria", "Next steps",
          "Open questions", "Key questions", "Notes", "Status", "Summary", "Results", "Considerations", "Caveats"]
TEXT_ROLES = {"title", "body", "shape", "callout", "other"}
LEAD = re.compile(r"^(?:however|but|and|also)\b[,\s]*", re.I)
# A clause ending on one of these is a fragment of a longer list item ("strategic thrusts into").
ENUM = re.compile(r"^\(\d+\)\s*")  # "(2) require financial viability" -> "require financial viability"
FRAGMENT = re.compile(r"(?:is|are|was|were|has|had|requires|remains|includes)\b", re.I)
DANGLING = re.compile(r"\b(?:given|into|on|of|to|for|with|and|or|the|a|an|in|by|from|as|that|than)$", re.I)


# --- Reading the slide ------------------------------------------------------------------

def key_entries(slide: Slide) -> list[dict]:
    """The slide's key as entries: a code and what it means ("HCE", "Health Care Equipment"),
    or a colour swatch and what it means."""
    out = []
    for e in sorted((e for e in slide.elements if e.role == "key_item"), key=lambda e: (e.bbox.y, e.bbox.x)):
        if e.table:
            rows: dict[int, list] = {}
            for c in e.table.cells:
                if not c.is_spanned:
                    rows.setdefault(c.row, []).append(c)
            for cells in rows.values():
                cells.sort(key=lambda c: c.col)
                texts = [" ".join(c.text.split()) for c in cells]
                code = next((t for t in texts if CODE.match(t)), "")
                meaning = next((t for t in texts if t and t != code and not CODE.match(t)), "")
                if meaning:
                    swatch = next((c.fill for c in cells if c.fill and not c.text.strip()), None) if not code else None
                    out.append({"code": code, "meaning": meaning, "swatch": swatch})
        else:
            parts = [p.strip() for p in e.text.split("\n") if p.strip()]
            if len(parts) == 2 and CODE.match(parts[0]):
                out.append({"code": parts[0], "meaning": parts[1], "swatch": None})
    seen, unique = set(), []
    for k in out:
        if (k["code"], k["meaning"]) not in seen:
            seen.add((k["code"], k["meaning"]))
            unique.append(k)
    return unique


def panels_of(slide: Slide) -> list[dict]:
    """Text blocks with their headings: a short line sitting directly above a block of text."""
    texts = [e for e in slide.elements if e.role in {"body", "shape", "callout", "other"} and e.text.strip() and not e.table]
    blocks = [e for e in texts if len(e.text.split()) >= 8]
    out = []
    for b in sorted(blocks, key=lambda e: (e.bbox.x, e.bbox.y)):
        heading = next((h for h in texts if h is not b and len(h.text.split()) <= 6
                        and 0 <= b.bbox.y - (h.bbox.y + h.bbox.h) <= 0.35 * IN
                        and h.bbox.x < b.bbox.x + b.bbox.w and b.bbox.x < h.bbox.x + h.bbox.w), None)
        out.append({"title": heading.text.strip() if heading else "", "text": b.text.strip()})
    return out


def candidates(deck: Deck) -> list[dict]:
    """Content slides with something to design: a chartable table, or dense text."""
    out = []
    for slide in deck.slides:
        if slide.hidden or slide.layout_kind in {"title", "section"}:
            continue
        tables = [d for e in slide.elements if e.table and (d := describe(e))]
        words = sum(len(p["text"].split()) for p in panels_of(slide))
        # Pictures can't move to an appendix slide with the table, so those slides are left alone.
        if tables and any(e.kind == "picture" and e.role not in KEEP for e in slide.elements):
            continue
        # A text redesign replaces everything on the slide, so it is offered only where the slide
        # is text alone: no table, picture or chart that would be lost.
        visual = any(e.role not in KEEP and (e.table or e.kind in VISUAL) for e in slide.elements)
        if tables or (words >= 60 and not visual):
            out.append({"slide": slide.index, "element_id": tables[0]["element_id"] if tables else "",
                        "kind": "chart" if tables else "text", "rows": len(tables[0]["rows"]) if tables else 0})
    return out


# --- The agent ------------------------------------------------------------------------------

def claude_designer(payload: dict, feedback: str = "") -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise NotUpgradable("Design suggestions are switched off on this server.")
    import anthropic

    client = anthropic.Anthropic(timeout=180, max_retries=2)
    model = os.environ.get("UPGRADE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    content = json.dumps(payload, ensure_ascii=False)
    if feedback:
        content += "\n\nYour previous design could not be used: " + feedback + " Propose a corrected design."
    try:
        response = client.beta.messages.create(
            model=model, max_tokens=16000, system=SYSTEM, tools=[TOOL], tool_choice={"type": "auto"},
            output_config={"effort": "high"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as exc:
        log.warning("redesign request failed: %s", exc)
        raise NotUpgradable("The design service didn't respond. Try again shortly.")
    if response.stop_reason == "refusal":
        raise NotUpgradable("No design suggestion for this slide.")
    for block in response.content:
        if block.type == "tool_use" and block.name == TOOL["name"]:
            return {**dict(block.input), "model": response.model}
    raise NotUpgradable("No design suggestion for this slide.")


def clauses(slide: Slide) -> list[dict]:
    """The slide's own sentences and semicolon clauses, verbatim apart from trailing punctuation,
    a leading "however," and a capital first letter. Labels such as "Executive Summary:" are split off."""
    from app.rules.checks import COMMENT

    # Never offered: reviewer comments (they are findings, not content), the key's own labels,
    # and continuation fragments with no subject ("Is in poor financial condition").
    key_labels = {k["meaning"].casefold() for k in key_entries(slide)}
    out, seen = [], set()
    for e in slide.elements:
        if (e.table or e.role not in TEXT_ROLES or not e.text.strip() or COMMENT.search(e.text)
                or (e.role != "title" and _beside_swatch(e, slide))):
            continue
        for para in e.text.split("\n"):
            parts = re.split(r"(?<=[.!?])\s+|;\s+" + (r"|:\s+" if e.role == "title" else ""), " ".join(para.split()))
            for part in parts:
                question = part.strip().endswith("?")
                text = re.sub(r"[.;:!?]+$", "", LEAD.sub("", ENUM.sub("", part.strip())))
                text = text[:1].upper() + text[1:]
                if ((FRAGMENT.match(text) and not question) or (e.role != "title" and _label_case(text))
                        or text.startswith("(") or len(re.findall(r"[A-Za-z]{2,}", text)) < 3):
                    continue  # also parenthetical asides and lines such as "N = 10,911"
                if (len(text.split()) >= 3 and not DANGLING.search(text) and text.casefold() not in seen
                        and text.casefold() not in key_labels):
                    seen.add(text.casefold())
                    out.append({"id": f"C{len(out) + 1}", "text": text})
    return out


def _chart_title(slide: Slide, header: str, unit: dict | None = None) -> str:
    """The column header with its monetary scale; a one-word header ("Count") is paired with the
    slide's table caption."""
    measure = _measure_label(header, unit or {})
    caption = " ".join(re.sub(r"\(\d+\)", "", _caption(slide)).split())
    if len(measure.split()) <= 1 and caption:
        return f"{caption} · {measure}"[:70]
    return measure[:60]


def _beside_swatch(e: Element, slide: Slide) -> bool:
    """A key label: a text box whose left neighbour is a key swatch or a small filled box."""
    for x in slide.elements:
        small = x.role == "key_item" or (x.fill and not x.text.strip() and x.bbox.w < 0.5 * IN and x.bbox.h < 0.5 * IN)
        if (x is not e and small and -0.15 * IN <= e.bbox.x - (x.bbox.x + x.bbox.w) < 0.4 * IN
                and x.bbox.y < e.bbox.y + e.bbox.h and e.bbox.y < x.bbox.y + x.bbox.h):
            return True
    return False


def _label_case(text: str) -> bool:
    """Title Case labels such as chart and table titles ("ISS Peer Group 2025 Changes"), not sentences."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", text) if len(w) >= 4]
    return len(words) >= 2 and sum(w[0].isupper() for w in words) >= 0.7 * len(words)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _measure(header: str) -> str:
    return " ".join(re.sub(r"\([^)]*\)", "", header).split()) or "value"


# A monetary scale written in brackets: "($MM)", "($MMs)", "($000s)", "(000s)", "($B)", "(£m)".
SCALE = re.compile(r"\(\s*([$£€])?\s*(MMs?|Mn|M|m|000s?|K|k|Bn|Bs?|bn|millions?|thousands?|billions?)\s*\)", re.I)


def _scale(text: str) -> tuple[str, str] | None:
    """('$', 'MM') for '($MMs)'; None when the text names no monetary scale."""
    m = SCALE.search(text or "")
    if not m:
        return None
    word = m.group(2).lower()
    scale = "000s" if word.startswith(("000", "k", "thousand")) else "B" if word.startswith(("b",)) else "MM"
    return m.group(1) or "", scale


def _caption(slide: Slide) -> str:
    return next((e.text for e in slide.elements if e.role == "table_caption" and e.text.strip()), "")


def column_units(table: dict, caption: str = "") -> dict[int, dict]:
    """Units per numeric column, resolved from three separate sources: a table-wide marker (in a
    non-numeric header such as "($MM) Company", or the caption), the column's own header, and the
    cells' symbols. A table-wide scale applies only to columns whose cells carry a currency symbol,
    so ratios, percentages and counts never inherit it. Conflicting sources make a column ambiguous."""
    wide = {_scale(h) for i, h in enumerate(table["headers"]) if i not in table["numeric"]} | {_scale(caption)}
    wide.discard(None)
    out = {}
    for col in table["numeric"]:
        symbols = {parse_number(r[col])[1] for r in table["rows"] if r[col].strip() and parse_number(r[col])}
        own = _scale(table["headers"][col])
        unit = {"kind": "number", "label": "", "ambiguous": False}
        if len(symbols - {""}) > 1:
            unit["ambiguous"] = True  # mixed symbols in one column
        elif symbols == {"%"}:
            unit["kind"] = "percent"
        elif symbols == {"x"}:
            unit["kind"] = "multiple"
        elif "$" in symbols or own:
            unit["kind"] = "money"
            sources = ({own} if own else set()) | (wide if "$" in symbols else set())
            if len({sc for _, sc in sources}) > 1:
                unit["ambiguous"] = True  # e.g. "($000s)" for the table but "($MM)" on the column
            elif sources:
                cur, scale = next(iter(sources))
                unit["label"] = (cur or ("$" if "$" in symbols else "")) + scale
        out[col] = unit
    return out


def _measure_label(header: str, unit: dict) -> str:
    """'FYE Revenue ($MM)': the measure with its monetary scale, so the redesigned slide keeps it."""
    measure = _measure(header)
    return f"{measure} ({unit['label']})" if unit.get("kind") == "money" and unit.get("label") else measure


def _column_stats(table: dict, label_col: int, value_col: int, highlight: list[str]) -> tuple[list, dict]:
    points = series(table, label_col, value_col)
    peers = [p for p in points if p[0] not in highlight]
    stats = facts(peers if len(peers) >= MIN_ROWS else points)
    stats.update(stated_stats(table, value_col))
    return points, stats


def templates(table: dict | None, client: str | None, caption: str = "") -> list[dict]:
    """Comparisons computed from the table for the client's row: its rank, and whether it is above
    or below the peer median. Entity, measure, value and unit all come from the same row and column."""
    if not table or not client:
        return []
    out, label_col, units = [], table["labels"][0], column_units(table, caption)
    for col in table["numeric"]:
        if units[col]["ambiguous"]:
            continue  # never guess a scale: no comparison is offered for this column
        rows = [p for p in series(table, label_col, col) if p[0].casefold().startswith(client.casefold())]
        if len(rows) != 1:
            continue
        label, value, raw = rows[0]
        points, stats = _column_stats(table, label_col, col, [label])
        if len(points) < MIN_ROWS:
            continue
        measure, median = _measure_label(table["headers"][col], units[col]), stats["median"]
        rank = 1 + sum(v > value for _, v, _ in points)
        out.append({"text": f"{label} ranks {_ordinal(rank)} of {len(points)} on {measure}", "column": col, "label": label})
        side = "above" if value > median else "below" if value < median else "in line with"
        owner = label + ("’" if label.endswith("s") else "’s")
        out.append({"text": f"{owner} {measure} of {raw} is {side} the peer median of {_format(median, raw)}",
                    "column": col, "label": label})
    for n, t in enumerate(out, 1):
        t["id"] = f"T{n}"
    return out


def _headings(slide: Slide) -> list[str]:
    """The slide's own panel headings: a short line above a text block, or a short bold or filled header box."""
    found = [p["title"] for p in panels_of(slide) if p["title"]]
    for e in slide.elements:
        text = " ".join(e.text.split())
        bold = any(r.bold for para in e.paragraphs for r in para.runs)
        if not e.table and e.role in TEXT_ROLES - {"title"} and text and (bold or e.fill):
            found.append(text)
    out = []
    for t in found:
        if len(t.split()) <= 6 and not re.search(r"\d", t) and t not in out:
            out.append(t)
    return out


def _payload(slide: Slide, table: dict | None, keys: list[dict], found: list[dict], computed: list[dict]) -> dict:
    payload = {
        "headline": {"id": "H0", "text": next((e.text for e in slide.elements if e.role == "title"), "")},
        "clauses": found,
        "headline_templates": [{"id": t["id"], "text": t["text"], "column": t["column"]} for t in computed],
        "title_options": _headings(slide) + TITLES,
        "key": [{"code": k["code"], "meaning": k["meaning"]} for k in keys],
    }
    if table:
        payload["table"] = {
            "columns": [{"index": i, "header": h, "numeric": i in table["numeric"]} for i, h in enumerate(table["headers"])],
            "rows": table["rows"][:60],
        }
    return payload


def validate(spec: dict, slide: Slide, table: dict | None, found: list[dict], computed: list[dict]) -> dict:
    """Resolve the agent's choices into text code writes; raise NotUpgradable with the reason.
    Nothing the agent writes reaches the slide: unknown ids, titles or columns are refused."""
    by_id = {c["id"]: c["text"] for c in found}
    by_template = {t["id"]: t for t in computed}
    layout_kind = spec.get("layout")
    if layout_kind not in {"chart_panels", "panels"}:
        raise NotUpgradable("The layout wasn't one of the two offered.")
    hid = str(spec.get("headline_id", "")).strip()
    template = by_template.get(hid)
    if hid == "H0":
        headline = re.sub(r"[.:;!?]+$", "", " ".join(next((e.text for e in slide.elements if e.role == "title"), "").split()))
    elif hid in by_id:
        headline = by_id[hid]
    elif template:
        headline = template["text"]
    else:
        raise NotUpgradable(f"The headline id “{hid[:20]}” isn't one of the slide's sentences or computed comparisons.")
    title_box = next((e.bbox for e in slide.elements if e.role == "title"), None)
    lines = layout._lines(headline, 24, title_box.w - 2 * 91440) if title_box else (1 if len(headline) <= 130 else 4)
    if not headline or lines > 3:  # the guideline's limit: at most 3 lines at 24 pt
        raise NotUpgradable("That headline runs past 3 lines; choose a shorter sentence or comparison.")
    design = {"layout": layout_kind, "headline": headline, "headline_source": "template" if template else "slide",
              "panels": [], "chart": None}
    if layout_kind == "chart_panels":
        chart = spec.get("chart")
        if not table or not chart:
            raise NotUpgradable("A chart layout needs a chartable table.")
        label_col, value_col = int(chart.get("label_column", -1)), int(chart.get("value_column", -1))
        if value_col not in table["numeric"] or not 0 <= label_col < len(table["headers"]) or label_col == value_col:
            raise NotUpgradable("The chosen chart columns don't match the table.")
        unit = column_units(table, _caption(slide))[value_col]
        if unit["ambiguous"]:
            raise NotUpgradable("That column's units are ambiguous (the table and column give different scales); choose another column.")
        if template and (template["column"] != value_col or label_col != table["labels"][0]):
            raise NotUpgradable("A computed comparison can only head a chart of its own column.")
        labels = {p[0] for p in series(table, label_col, value_col)}
        highlight = [h for h in chart.get("highlight", []) if h in labels][:3]
        if template and template["label"] not in highlight:
            highlight = [template["label"]] + highlight[:2]
        points, stats = _column_stats(table, label_col, value_col, highlight)
        if len(points) < MIN_ROWS:
            raise NotUpgradable("Too few rows to chart.")
        cat = int(chart.get("category_column", -1))
        categories = {}
        if 0 <= cat < len(table["headers"]) and cat not in (label_col, value_col):
            for r in table["rows"]:
                if r[label_col].strip() and r[cat].strip():
                    categories[r[label_col].strip()] = r[cat].strip()
        design["chart"] = {
            "label_column": label_col, "value_column": value_col, "highlight": highlight,
            "title": _chart_title(slide, table["headers"][value_col], unit),
            "points": [[l, v, raw, categories.get(l, "")] for l, v, raw in points],
            "stats": {k: round(v, 4) for k, v in stats.items()},
            "red_box": [h for h in highlight if _named(h, headline)],
        }
    elif template:
        raise NotUpgradable("A computed comparison needs the chart layout.")
    options = {t.casefold(): t for t in _headings(slide) + TITLES}
    used = set()
    for panel in (spec.get("panels") or [])[:2]:
        title = options.get(" ".join(str(panel.get("title", "")).split()).casefold())
        if title is None:
            raise NotUpgradable(f"The panel title “{str(panel.get('title', ''))[:30]}” isn't one of the offered titles.")
        ids = [str(i).strip() for i in panel.get("clause_ids", [])][:4]
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise NotUpgradable(f"Clause id {unknown[0][:12]} isn't on this slide.")
        bullets = [by_id[i] for i in ids if i not in used and by_id[i] != headline]
        used.update(ids)
        if bullets:
            design["panels"].append({"title": title, "bullets": bullets})
    if layout_kind == "panels" and not design["panels"]:
        raise NotUpgradable("No text panels were proposed.")
    return design


def _named(label: str, headline: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", label) if len(w) > 2]
    return bool(words) and words[0].lower() in headline.lower()


def propose(deck: Deck, slide_no: int, designer: Callable[..., dict] | None = None) -> dict:
    from app.rules.consistency import infer_client

    designer = designer or claude_designer
    slide = deck.slides[slide_no - 1]
    tables = [d for e in slide.elements if e.table and (d := describe(e))]
    by_id = {e.id: e for e in slide.elements}
    table = max(tables, key=lambda d: by_id[d["element_id"]].bbox.w * by_id[d["element_id"]].bbox.h) if tables else None
    keys = key_entries(slide)
    found, computed = clauses(slide), templates(table, infer_client(deck), _caption(slide))
    payload = _payload(slide, table, keys, found, computed)
    feedback, design, last, spec = "", None, None, {}
    for _ in range(3):  # retries with the reason, then give up honestly
        try:
            spec = designer(payload, feedback) if feedback else designer(payload)
            design = _fit(deck, slide_no, validate(spec, slide, table, found, computed), keys)
            break
        except NotUpgradable as exc:
            last, feedback, design = exc, str(exc), None
    if design is None:
        raise last or NotUpgradable("No design suggestion for this slide.")
    caption = next((e.text for e in slide.elements if e.role == "table_caption"), "").strip()
    return {
        "slide": slide_no, "engine": ENGINE, "element_id": table["element_id"] if table else "",
        "kind": "chart" if table else "text", **design, "keys": keys,
        "original_headline": next((e.text for e in slide.elements if e.role == "title"), ""),
        "appendix_title": "Appendix: " + (caption or (design["chart"] or {}).get("title") or "detail"),
        "chart_title": (design["chart"] or {}).get("title", ""),
        "basis": [{"change": c, "guideline": g} for c, g in BASIS
                  if table or c not in {"Every figure kept", "Key beneath what it explains"}],
        "model": spec.get("model", ""),
    }


# --- Geometry -----------------------------------------------------------------------------------

def frame(deck: Deck, slide: Slide) -> dict:
    """The content area: inside the headline's left and right edges, at least 1 in from the slide's
    left edge, between the headline and the confidentiality line."""
    title = next((e for e in slide.elements if e.role == "title"), None)
    conf = next((e for e in slide.elements if e.role == "confidentiality"), None)
    tb = title.bbox if title else deck.master.title_bbox_by_layout.get(slide.layout_name)
    left = max(tb.x if tb else IN, IN)
    right = (tb.x + tb.w) if tb else deck.slide_width - int(0.5 * IN)
    top = (tb.y + tb.h if tb else int(1.4 * IN)) + int(0.18 * IN)
    bottom = (conf.bbox.y if conf else deck.slide_height - int(0.4 * IN)) - int(0.15 * IN)
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


HEADER_H = int(0.34 * IN)
CAPTION_H = int(0.32 * IN)
SOURCE_H = int(0.22 * IN)
GAP = int(0.22 * IN)
STACK_GAP = int(0.36 * IN)  # between stacked panels; covers the line-height estimate running short
CARD = "F2F2F2"


def _bullet_height(bullets: list[str], width: int) -> int:
    # 16 pt bullets with 6 pt between them, inside 0.08 in insets, after a 0.36 in hanging indent.
    total = int(0.16 * IN)
    for i, b in enumerate(bullets):
        lines = layout._lines(b, 16, width - int(0.16 * IN) - int(0.25 * IN))
        total += int((lines * 16 * 1.2 + (6 if i else 0)) * layout.PT)
    return total


def _legend_height(keys: list[dict], width: int) -> int:
    if not keys:
        return 0
    text = "    ".join(f"{k['code']} {k['meaning']}" for k in keys if k["code"])
    lines = layout._lines("Key  " + text, 10, width) if text else 0
    swatches = sum(1 for k in keys if not k["code"])
    return int(((lines + swatches) * 10 * 1.35 + 8) * layout.PT)


def fit_check(deck: Deck, slide_no: int, design: dict, keys: list[dict]) -> None:
    """Every panel must fit at 16 pt; otherwise ask for a shorter design."""
    f = frame(deck, deck.slides[slide_no - 1])
    if design["layout"] == "chart_panels":
        col = f["w"] - int(f["w"] * 0.58) - GAP
        need = sum(HEADER_H + _bullet_height(p["bullets"], col) + STACK_GAP for p in design["panels"]) - STACK_GAP
        legend = _legend_height([k for k in keys if k["code"]], int(f["w"] * 0.58))
        if f["h"] - CAPTION_H - SOURCE_H - legend < int(2.2 * IN):
            raise NotUpgradable("The key is too long to sit beside the chart.")
    else:
        n = max(1, len(design["panels"]))
        col = (f["w"] - GAP * (n - 1)) // n - int(0.2 * IN)  # inside the card's padding
        need = max(HEADER_H + _bullet_height(p["bullets"], col) for p in design["panels"])
    if need > f["h"]:
        over = need - f["h"]
        raise NotUpgradable(f"The panels are too long for the slide at 16 pt (about {round(over / IN, 1)} in too tall); use fewer, shorter bullets.")


def _fit(deck: Deck, slide_no: int, design: dict, keys: list[dict]) -> dict:
    """Drop trailing bullets (keeping two per panel) until the panels fit; the last bullets are
    the least important by the prompt's ordering. Raises when even that doesn't fit."""
    while True:
        try:
            fit_check(deck, slide_no, design, keys)
            return design
        except NotUpgradable:
            longest = max(design["panels"], key=lambda p: len(p["bullets"]), default=None)
            if not longest or len(longest["bullets"]) <= 2:
                raise
            longest["bullets"] = longest["bullets"][:-1]


# --- Drawing --------------------------------------------------------------------------------------

def draw_chart(chart: dict, path: Path, aspect: float) -> None:
    """Horizontal bars, largest first; the client in ACME Blue, peers grey; median and
    25th-75th %ile marked; each bar's category code beside its label when the key explains it."""
    points = sorted(chart["points"], key=lambda p: -p[1])
    stats, highlight = chart["stats"], set(chart["highlight"])
    W = 2400
    H = max(800, int(W / aspect))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    n = len(points)
    foot = 84
    row = (H - foot - 16) / n
    size = int(max(24, min(46, row * 0.6)))
    f, fb, small = _font(size), _font(size, True), _font(int(size * 0.88))
    cat_w = max((d.textlength(p[3], font=small) for p in points if p[3]), default=0)
    label_w = max(d.textlength(p[0], font=fb if p[0] in highlight else f) for p in points) + (cat_w + 26 if cat_w else 0) + 28
    value_w = max(d.textlength(p[2], font=fb) for p in points) + 30
    x0, x1, top = label_w, W - value_w, 8
    scale = (x1 - x0) / (max(p[1] for p in points) or 1)
    bottom = top + row * n
    if "p25" in stats and "p75" in stats:
        d.rectangle([x0 + stats["p25"] * scale, top, x0 + stats["p75"] * scale, bottom], fill=BAND)
    mx = x0 + stats["median"] * scale
    for y in range(int(top), int(bottom), 18):
        d.line([mx, y, mx, min(y + 10, bottom)], fill=LABEL, width=3)
    for i, (label, value, raw, cat) in enumerate(points):
        y = top + i * row
        hi = label in highlight
        mid = y + row / 2
        bar = (y + row * 0.2, y + row * 0.8)
        if cat:
            d.text((x0 - 16, mid), cat, font=small, fill="#808080", anchor="rm")
        d.text((x0 - 16 - (cat_w + 22 if cat_w else 0), mid), label, font=fb if hi else f,
               fill="#000000" if hi else LABEL, anchor="rm")
        d.rectangle([x0, bar[0], x0 + max(2, value * scale), bar[1]], fill=BLUE if hi else GREY)
        tx = x0 + value * scale + 12
        box = d.textbbox((tx, mid), raw, font=fb if hi else small, anchor="lm")
        d.rectangle([box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2], fill="white")
        d.text((tx, mid), raw, font=fb if hi else small, fill=BLUE if hi else DARK, anchor="lm")
        if label in set(chart.get("red_box", [])):
            d.rectangle([4, y + 2, min(W - 4, x0 + value * scale + value_w), y + row - 2], outline=RED, width=5)
    sample = points[0][2]
    median = f"Median {_format(stats['median'], sample)}"
    d.text((mx, bottom + 16), median, font=small, fill=LABEL, anchor="mt")
    if "p25" in stats and "p75" in stats:
        band = f"Shaded: 25th–75th %ile ({_format(stats['p25'], sample)}–{_format(stats['p75'], sample)})"
        m = d.textbbox((mx, bottom + 16), median, font=small, anchor="mt")
        right = d.textbbox((W - 8, bottom + 16), band, font=small, anchor="rt")
        left = d.textbbox((x0, bottom + 16), band, font=small, anchor="lt")
        # Put the band note wherever it clears the median label; drop it if neither side does.
        for box, anchor, x in ((right, "rt", W - 8), (left, "lt", x0)):
            if box[0] > m[2] + 20 or box[2] < m[0] - 20:
                d.text((x, bottom + 16), band, font=small, fill="#808080", anchor=anchor)
                break
    img.save(path, "PNG", optimize=True)


def _box(slide, x, y, w, h):
    box = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Emu(int(0.08 * IN))
    tf.margin_top = tf.margin_bottom = Emu(int(0.04 * IN))
    return box, tf


def _run(paragraph, text, size, bold=False, italic=False, color=None):
    r = paragraph.add_run()
    r.text = text
    r.font.name, r.font.size, r.font.bold, r.font.italic = "Calibri", Pt(size), bold, italic
    if color:
        r.font.color.rgb = RGBColor.from_string(color)
    return r


def _bullets(paragraph):
    """ACME level-1 bullet: Wingdings square, hanging indent, 6 pt before."""
    ppr = paragraph._p.get_or_add_pPr()
    ppr.set("marL", str(int(0.25 * IN)))
    ppr.set("indent", str(-int(0.25 * IN)))
    for tag, child in (("a:lnSpc", ("a:spcPct", "100000")), ("a:spcBef", ("a:spcPts", "600"))):
        el = etree.SubElement(ppr, qn(tag))
        etree.SubElement(el, qn(child[0])).set("val", child[1])
    etree.SubElement(ppr, qn("a:buFont")).set("typeface", "Wingdings")
    etree.SubElement(ppr, qn("a:buChar")).set("char", "§")


def _rect(slide, x, y, w, h, fill):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(x), Emu(y), Emu(w), Emu(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    shape.line.fill.background()
    style = shape._element.find(qn("p:style"))  # the default theme style adds a shadow and outline
    if style is not None:
        shape._element.remove(style)
    return shape


def _panel(slide, x, y, w, title, bullets, card_h: int | None = None) -> int:
    """An ACME-blue heading bar over concise 16 pt bullets, on a light grey card of
    `card_h` when given (so side-by-side panels end level); returns the height used."""
    used = 0
    if card_h:
        _rect(slide, x, y, w, card_h, CARD)
    if title:
        bar = _rect(slide, x, y, w, HEADER_H, "006EBE")
        tf = bar.text_frame
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _run(p, title, 14, bold=True, color="FFFFFF")
        used += HEADER_H
    h = _bullet_height(bullets, w)
    _, tf = _box(slide, x + (int(0.1 * IN) if card_h else 0), y + used + (int(0.06 * IN) if card_h else 0),
                 w - (int(0.2 * IN) if card_h else 0), h)
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _bullets(p)
        _run(p, b, 16)
    return used + h


def _legend(slide, x, y, w, keys) -> int:
    """The key as a distinct legend under the chart: each code in ACME Blue, then its meaning."""
    if not keys:
        return 0
    h = _legend_height(keys, w)
    _, tf = _box(slide, x, y, w, h)
    p = tf.paragraphs[0]
    _run(p, "Key  ", 10, bold=True, color="404040")
    coded = [k for k in keys if k["code"]]
    for i, k in enumerate(coded):
        # Non-breaking spaces keep each entry on one line; entries wrap between each other.
        _run(p, k["code"] + "\u00a0", 10, bold=True, color="006EBE")
        _run(p, k["meaning"].replace(" ", "\u00a0") + ("     " if i < len(coded) - 1 else ""), 10, color="404040")
    for k in keys:
        if not k["code"]:
            q = tf.add_paragraph()
            _run(q, "■ ", 10, color=(k["swatch"] or "808080"))
            _run(q, k["meaning"], 10, color="404040")
    return h


def apply(pptx: Path, deck: Deck, proposal: dict, workdir: Path) -> int | None:
    """Rewrite `pptx` with the redesign; returns the appendix slide number, or None when
    the slide had no table to keep."""
    prs = Presentation(str(pptx))
    n = proposal["slide"]
    slide, model = prs.slides[n - 1], deck.slides[n - 1]
    f = frame(deck, model)
    # Footnotes and source notes are disclosures: they stay on the redesigned slide (and are copied
    # to the appendix with the table), and the new layout sits above them.
    notes = [e for e in model.elements if e.role in NOTES and e.text.strip()]
    table_moves = bool(proposal.get("element_id"))
    # With an appendix, the source note is restated in the chart's data line and the footnotes
    # describe the original table: they stay on the slide under a label that binds them to it.
    kept_notes = [e for e in notes if not (table_moves and e.role == "source_note")]
    keep = set(KEEP) | {e.role for e in kept_notes}
    label_h = int(0.2 * IN) if table_moves and kept_notes else 0
    if kept_notes:
        f["h"] = min(f["h"], min(e.bbox.y for e in kept_notes) - GAP - label_h - f["y"])
    movers = [e for e in model.elements if e.role not in keep or (e.role in NOTES and e not in kept_notes)]
    nodes = [_shape(slide, e.shape_id) for e in movers]
    if proposal.get("element_id") and any(node.xpath(".//@r:embed | .//@r:id | .//@r:link") for node in nodes):
        raise NotUpgradable("Linked pictures sit on this slide; it is left as it is.")
    if not proposal.get("element_id") and any(e.table or e.kind in VISUAL for e in movers):
        # Without an appendix nothing but text may be replaced: never lose a table, picture or chart.
        raise NotUpgradable("This slide has a table, picture or chart that a text redesign would lose; it is left as it is.")
    appendix = None
    if proposal.get("element_id"):
        # The original content, table first, goes unchanged to an appendix slide.
        page = prs.slides.add_slide(slide.slide_layout)
        for ph in list(page.placeholders):
            if ph.placeholder_format.type is not None and "TITLE" in str(ph.placeholder_format.type):
                ph.text_frame.text = proposal["appendix_title"]
            else:
                ph._element.getparent().remove(ph._element)
        tree = page.shapes._spTree
        for e in [x for x in model.elements if x.role in {"confidentiality", "slide_number"}]:
            tree.append(copy.deepcopy(_shape(slide, e.shape_id)))
        for node in nodes + [_shape(slide, e.shape_id) for e in kept_notes]:
            tree.append(copy.deepcopy(node))
        appendix = len(prs.slides)
    for node in nodes:
        node.getparent().remove(node)
    title = next((e for e in model.elements if e.role == "title"), None)
    if title is not None:
        _set_text(_shape(slide, title.shape_id), proposal["headline"])

    # Under the chart, only the codes its bars carry; colour swatches describe the table's
    # cells and stay with the table on the appendix slide.
    keys = [k for k in proposal.get("keys") or [] if k["code"]]
    if proposal["layout"] == "chart_panels":
        # Without panels the chart takes the width, so the slide doesn't sit half empty.
        chart_w = int(f["w"] * 0.58) if proposal["panels"] else f["w"]
        col_x, col_w = f["x"] + chart_w + GAP, f["w"] - chart_w - GAP
        y = f["y"]
        _, tf = _box(slide, f["x"], y, chart_w, CAPTION_H)
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        _run(tf.paragraphs[0], proposal["chart"]["title"], 14, bold=True)
        legend_h = _legend_height(keys, chart_w)
        chart_h = f["h"] - CAPTION_H - legend_h - SOURCE_H - int(0.06 * IN)
        png = workdir / f"chart-{n}.png"
        draw_chart(proposal["chart"], png, chart_w / chart_h)
        slide.shapes.add_picture(str(png), Emu(f["x"]), Emu(y + CAPTION_H), Emu(chart_w), Emu(chart_h))
        y2 = y + CAPTION_H + chart_h
        _legend(slide, f["x"], y2, chart_w, keys)
        source = next((e.text.strip().rstrip(".") for e in model.elements if e.role == "source_note" and e.text.strip()), "")
        _, tf = _box(slide, f["x"], y2 + legend_h, chart_w, SOURCE_H)
        _run(tf.paragraphs[0], (source or "Data: " + proposal["appendix_title"].removeprefix("Appendix: "))
             + f"; chart sorted by {proposal['chart']['title']}, highest first"
             + (f"; full table and its notes on slide {appendix}" if appendix else ""), 9, italic=True, color="404040")
        if label_h:
            top = min(e.bbox.y for e in kept_notes)
            left = min(e.bbox.x for e in kept_notes)
            _, tf = _box(slide, left, top - label_h, f["x"] + f["w"] - left, label_h)
            _run(tf.paragraphs[0], f"Original table notes (full table on slide {appendix}):", 10, italic=True, color="404040")
        py = f["y"]
        for panel in proposal["panels"]:
            py += _panel(slide, col_x, py, col_w, panel["title"], panel["bullets"]) + STACK_GAP
    else:
        count = max(1, len(proposal["panels"]))
        col_w = (f["w"] - GAP * (count - 1)) // count
        y = f["y"]
        # Cards end level and fill most of the content area, so short panels don't leave the
        # bottom of the slide empty.
        room = f["y"] + f["h"] - y
        tallest = max(HEADER_H + _bullet_height(p["bullets"], col_w - int(0.2 * IN)) for p in proposal["panels"])
        card_h = min(room, max(tallest + int(0.25 * IN), int(room * 0.85)))
        for i, panel in enumerate(proposal["panels"]):
            _panel(slide, f["x"] + i * (col_w + GAP), y, col_w, panel["title"], panel["bullets"], card_h)
    prs.save(str(pptx))
    return appendix


# --- Preview, gate and apply ----------------------------------------------------------------------

def _issues(path: Path, slide_no: int) -> list:
    from app.pipeline import analyze_sync

    return [x for x in analyze_sync(str(path), use_ai=False).findings
            if x.slide == slide_no and x.severity in {"error", "warning"}]


def preview(original: Path, deck: Deck, proposal: dict, out_dir: Path, render) -> dict:
    """Render the redesign and its appendix, and check it is better: returns before/after issue counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = proposal["slide"]
    with tempfile.TemporaryDirectory(prefix="redline-redesign-") as tmp:
        tmp = Path(tmp)
        work = tmp / "deck.pptx"
        shutil.copyfile(original, work)
        appendix = apply(work, deck, proposal, tmp)
        before = len(_issues(original, n))
        keep_only(work, {n} | ({appendix} if appendix else set()))
        after = len(_issues(work, 1))
        # Never more issues than the original, and fewer whenever the original had any.
        if after > before or (before > 0 and after >= before):
            raise NotUpgradable(f"The redesign wasn't clearly better ({before} issues before, {after} after).")
        freeze_slide_numbers(work, sorted({n} | ({appendix} if appendix else set())))
        pages = tmp / "pages"
        render(work, pages)
        shutil.copyfile(pages / "1.png", out_dir / f"{n}.png")
        if appendix:
            shutil.copyfile(pages / "2.png", out_dir / f"{n}-appendix.png")
    return {"issues_before": before, "issues_after": after}


def apply_chosen(fixed: Path, upgrades: dict, workdir: Path) -> dict:
    from app.model.extract import extract_deck

    results = {}
    for key, item in sorted(upgrades.items(), key=lambda kv: int(kv[0])):
        proposal = item.get("proposal")
        if item.get("choice") != "use" or not proposal:
            continue
        try:
            appendix = apply(fixed, extract_deck(str(fixed)), proposal, workdir)
            results[key] = {"status": "applied", **({"appendix_slide": appendix} if appendix else {})}
        except NotUpgradable as exc:
            results[key] = {"status": "failed", "note": str(exc)}
        except Exception:
            log.exception("redesign for slide %s failed", key)
            results[key] = {"status": "failed", "note": "This redesign couldn't be applied; the slide is unchanged."}
    return results
