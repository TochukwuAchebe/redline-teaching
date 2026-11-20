"""Apply accepted findings to a copy of the deck, so 'Accept' changes the presentation.

Each fixer is deterministic and generic: it works from the rule, the deck's own
master/layout values and the guideline config, never from file-specific values.
Findings that need human judgement are reported as manual, not guessed at.
The caller verifies every applied fix by re-analysing the corrected deck.
"""
from __future__ import annotations

import copy
import difflib
import re
from pathlib import Path
from typing import Callable

import yaml
from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from app.model.types import Deck, Finding
from app.rules.common import CONFIG_DIR, load_config

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}
TRAILING = re.compile(r"[.:;!?。]+(\s*)$")


class Manual(Exception):
    """This finding needs a person: the right change is a judgement, not a rule."""


# --- XML helpers -------------------------------------------------------------------------

def _shape(slide, shape_id: int):
    for nv in slide.shapes._spTree.iter(qn("p:cNvPr")):
        if nv.get("id") == str(shape_id):
            return nv.getparent().getparent()
    raise Manual("The flagged shape is no longer on the slide.")


def _runs(node):
    return [r for r in node.iter(qn("a:r")) if (r.findtext(qn("a:t")) or "").strip()]


_AFTER_LATIN = {qn(t) for t in ("a:ea", "a:cs", "a:sym", "a:hlinkClick", "a:hlinkMouseOver", "a:rtl", "a:extLst")}


def _rpr(run):
    rpr = run.find(qn("a:rPr"))
    if rpr is None:
        rpr = etree.Element(qn("a:rPr"), lang="en-US")
        run.insert(0, rpr)
    return rpr


def _style_run(run, font=None, size=None, bold=None, italic=None, color=None):
    rpr = _rpr(run)
    if size is not None:
        rpr.set("sz", str(int(round(size * 100))))
    if bold is not None:
        rpr.set("b", "1" if bold else "0")
    if italic is not None:
        rpr.set("i", "1" if italic else "0")
    if color is not None:
        for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:pattFill"):
            for old in rpr.findall(qn(tag)):
                rpr.remove(old)
        fill = etree.Element(qn("a:solidFill"))
        etree.SubElement(fill, qn("a:srgbClr")).set("val", color)
        ln = rpr.find(qn("a:ln"))  # schema order: ln, then fill
        rpr.insert(rpr.index(ln) + 1 if ln is not None else 0, fill)
    if font is not None:
        latin = rpr.find(qn("a:latin"))
        if latin is None:
            latin = etree.Element(qn("a:latin"))
            later = next((c for c in rpr if c.tag in _AFTER_LATIN), None)
            rpr.insert(rpr.index(later), latin) if later is not None else rpr.append(latin)
        latin.set("typeface", font)


def _paragraph_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(qn("a:t")))


def _set_paragraph_text(p, text: str):
    """Replace a paragraph's text, keeping the first run's formatting."""
    runs = p.findall(qn("a:r"))
    if not runs:
        return
    runs[0].find(qn("a:t")).text = text
    for extra in runs[1:]:
        p.remove(extra)


def _replace_text(node, pattern: re.Pattern, repl: str) -> bool:
    changed = False
    for p in node.iter(qn("a:p")):
        hit = False
        for t in p.iter(qn("a:t")):  # within a single run first, keeping formatting
            new = pattern.sub(repl, t.text or "")
            if new != (t.text or ""):
                t.text, hit = new, True
        if not hit:
            whole = _paragraph_text(p)
            new = pattern.sub(repl, whole)
            if new != whole:  # the match spans runs
                _set_paragraph_text(p, new)
                hit = True
        changed |= hit
    return changed


def _cells(table_shape):
    tbl = table_shape.find(".//" + qn("a:tbl"))
    if tbl is None:
        raise Manual("The flagged table is no longer on the slide.")
    return [tr.findall(qn("a:tc")) for tr in tbl.findall(qn("a:tr"))]


def _tcpr(tc):
    pr = tc.find(qn("a:tcPr"))
    if pr is None:
        pr = etree.SubElement(tc, qn("a:tcPr"))
    return pr


def _fill_cell(tc, color: str):
    pr = _tcpr(tc)
    for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:pattFill", "a:blipFill"):
        for old in pr.findall(qn(tag)):
            pr.remove(old)
    fill = etree.Element(qn("a:solidFill"))
    etree.SubElement(fill, qn("a:srgbClr")).set("val", color)
    # Fills follow cell borders (lnL..lnB) in schema order.
    borders = [c for c in pr if c.tag in {qn("a:lnL"), qn("a:lnR"), qn("a:lnT"), qn("a:lnB"), qn("a:lnTlToBr"), qn("a:lnBlToTr")}]
    pr.insert(pr.index(borders[-1]) + 1 if borders else 0, fill)


def _set_xfrm(shape, bbox):
    sppr = shape.find(qn("p:spPr"))
    if sppr is None:
        raise Manual("This shape has no position to reset.")
    xfrm = sppr.find(qn("a:xfrm"))
    if xfrm is None:
        xfrm = etree.Element(qn("a:xfrm"))
        sppr.insert(0, xfrm)
    off = xfrm.find(qn("a:off"))
    if off is None:
        off = etree.SubElement(xfrm, qn("a:off"))
    ext = xfrm.find(qn("a:ext"))
    if ext is None:
        ext = etree.SubElement(xfrm, qn("a:ext"))
    off.set("x", str(bbox.x)), off.set("y", str(bbox.y))
    ext.set("cx", str(bbox.w)), ext.set("cy", str(bbox.h))


# --- Fixers ------------------------------------------------------------------------------

class Ctx:
    def __init__(self, deck: Deck, config: dict):
        self.deck, self.config = deck, config
        self.palette = yaml.safe_load((CONFIG_DIR / "palette.yaml").read_text())["colors"]
        self.theme_changed: set[str] = set()

    def param(self, rule, key, fallback):
        return self.config.get(rule, {}).get("params", {}).get(key, fallback)

    @property
    def conf_text(self) -> str:
        return self.deck.master.confidentiality_text or self.param("CONF_MISSING", "text", "")

    def element(self, f: Finding):
        return next((e for s in self.deck.slides for e in s.elements if e.id == f.element_id), None)


Fixer = Callable[[Ctx, object, object | None, Finding], str]
FIXERS: dict[str, Fixer] = {}


def fixer(*rules):
    def register(fn):
        for r in rules:
            FIXERS[r] = fn
        return fn
    return register


# The label, an optional version stamp ("DRAFT 20260330", "draft v2") and its separators.
# Only version-like tokens go with it: the next word may be the title ("DRAFT ACME review").
DRAFT_LABEL = re.compile(
    r"[^\S\n]*[–—|-]?[^\S\n]*[(\[]?\bDRAFT\b(?:[^\S\n]+(?:v|rev\.?|r|#)?\d[\w./-]*)?[)\]]?"
    r"(?:[^\S\n]*[–—|:-](?=[^\S\n]))?",
    re.I,
)


@fixer("TITLESLIDE_DRAFT")
def _remove_draft(ctx, slide, shape, f):
    if not _replace_text(shape, DRAFT_LABEL, ""):
        raise Manual("The draft label could not be located in the text.")
    # A label that opened the line leaves its following space behind ("DRAFT ACME …").
    for para in shape.iter(qn("a:p")):
        for t in [t for t in para.iter(qn("a:t")) if t.text]:
            t.text = t.text.lstrip()
            if t.text:
                break
    # The separator before the label often sits in its own run ("May 11, 2026 – " + "DRAFT ...").
    for para in shape.iter(qn("a:p")):
        for t in reversed([t for t in para.iter(qn("a:t")) if t.text]):
            stripped = re.sub(r"[\s–—\-|,;:]+$", "", t.text)
            t.text = stripped
            if stripped:
                break
    return "Removed the draft designator."


@fixer("CONF_MISSING")
def _add_confidentiality(ctx, slide, shape, f):
    bbox = f.bbox or ctx.deck.master.confidentiality_bbox
    if not ctx.conf_text or bbox is None:
        raise Manual("No confidentiality wording or position is defined for this deck.")
    box = slide.shapes.add_textbox(Emu(bbox.x), Emu(bbox.y), Emu(bbox.w), Emu(bbox.h))
    tf = box.text_frame
    tf.text = ctx.conf_text
    para = tf.paragraphs[0]
    para.alignment = 2  # centred, as in the master footer
    run = para.runs[0]
    run.font.name, run.font.size = ctx.param("CONF_STYLE", "font", "Calibri"), Pt(ctx.param("CONF_STYLE", "size_pt", 8))
    return "Added the confidentiality statement in the master position."


@fixer("CONF_TEXT_MODIFIED")
def _restore_confidentiality(ctx, slide, shape, f):
    paras = shape.findall(".//" + qn("a:p"))
    _set_paragraph_text(paras[0], ctx.conf_text)
    for extra in paras[1:]:
        extra.getparent().remove(extra)
    return "Restored the exact confidentiality wording."


@fixer("CONF_MOVED")
def _move_confidentiality(ctx, slide, shape, f):
    if ctx.deck.master.confidentiality_bbox is None:
        raise Manual("The master defines no confidentiality position.")
    _set_xfrm(shape, ctx.deck.master.confidentiality_bbox)
    return "Moved the confidentiality statement back to the master position."


@fixer("TITLE_POSITION")
def _reset_title_position(ctx, slide, shape, f):
    sppr = shape.find(qn("p:spPr"))
    xfrm = sppr.find(qn("a:xfrm")) if sppr is not None else None
    if xfrm is None:
        raise Manual("The headline already uses its layout position.")
    sppr.remove(xfrm)  # a placeholder without its own position inherits the layout's exactly
    return "Reset the headline to its layout position and size."


def _restyle(shape, **style):
    runs = _runs(shape)
    if not runs:
        raise Manual("No text to restyle.")
    for r in runs:
        _style_run(r, **style)


@fixer("TITLE_FONT", "TITLESLIDE_FONT")
def _title_font(ctx, slide, shape, f):
    m = ctx.deck.master
    size = ctx.param("TEMPLATE_NONCONFORMING", "title_size_pt", 24)
    size = m.title_size_pt if m.title_size_pt and abs(m.title_size_pt - size) < 0.5 else size
    _restyle(shape, font=ctx.param("TEMPLATE_NONCONFORMING", "title_font", "Calibri"), size=size,
             bold=None if f.rule_id == "TITLESLIDE_FONT" else True)
    return f"Set the headline to Calibri{' bold' if f.rule_id == 'TITLE_FONT' else ''} {size:g} pt."


@fixer("BULLET_FONT")
def _bullet_font(ctx, slide, shape, f):
    _restyle(shape, font=ctx.param("BULLET_FONT", "font", "Calibri"), size=ctx.param("TEMPLATE_NONCONFORMING", "body_size_pt", 16))
    return "Set bulleted text to Calibri 16 pt."


@fixer("CONF_STYLE")
def _conf_style(ctx, slide, shape, f):
    _restyle(shape, font=ctx.param("CONF_STYLE", "font", "Calibri"), size=ctx.param("CONF_STYLE", "size_pt", 8))
    # White text hides the statement; drop the override so the master's colour applies.
    for rpr in shape.iter(qn("a:rPr")):
        fill = rpr.find(qn("a:solidFill"))
        color = fill.find(qn("a:srgbClr")) if fill is not None else None
        if color is not None and color.get("val", "").upper() == "FFFFFF":
            rpr.remove(fill)
    return "Set the confidentiality statement to Calibri 8 pt."


@fixer("FOOTNOTE_STYLE", "SOURCE_NOTE_STYLE")
def _note_style(ctx, slide, shape, f):
    size = ctx.param(f.rule_id, "size_pt", 10 if f.rule_id == "FOOTNOTE_STYLE" else 9)
    _restyle(shape, font="Calibri", size=size, italic=True)
    return f"Set the note to Calibri italic {size:g} pt."


@fixer("TABLE_CAPTION")
def _caption(ctx, slide, shape, f):
    _restyle(shape, font="Calibri", size=ctx.param("TABLE_CAPTION", "size_pt", 14), bold=True)
    for p in shape.iter(qn("a:p")):
        ppr = p.find(qn("a:pPr"))
        if ppr is None:
            ppr = etree.Element(qn("a:pPr"))
            p.insert(0, ppr)
        ppr.set("algn", "ctr")
    caption = ctx.element(f)
    slide_el = next((s for s in ctx.deck.slides if s.index == f.slide), None)
    tables = [e for e in (slide_el.elements if slide_el else []) if e.table and caption
              and e.bbox.y >= caption.bbox.y and min(e.bbox.x + e.bbox.w, caption.bbox.x + caption.bbox.w) > max(e.bbox.x, caption.bbox.x)]
    if caption and tables and not caption.group_path:
        table = min(tables, key=lambda e: e.bbox.y - caption.bbox.y)
        box = caption.bbox.model_copy(update={"x": table.bbox.x + (table.bbox.w - caption.bbox.w) // 2})
        _set_xfrm(shape, box)
    return "Set the table title to Calibri bold 14 pt and centred it over the table."


@fixer("TABLE_FONT")
def _table_font(ctx, slide, shape, f):
    lo, hi = ctx.param("TABLE_FONT", "min_pt", 10), ctx.param("TABLE_FONT", "max_pt", 12)
    el = ctx.element(f)
    effective = {(c.row, c.col): [r for p in c.paragraphs for r in p.runs if r.text.strip()] for c in (el.table.cells if el and el.table else [])}
    for ri, row in enumerate(_cells(shape)):
        col = 0
        for tc in row:
            known = effective.get((ri, col), [])
            col += int(tc.get("gridSpan", "1"))
            for i, r in enumerate(_runs(tc)):
                rpr = _rpr(r)
                latin = rpr.find(qn("a:latin"))
                if latin is not None and latin.get("typeface", "").lower().startswith(("wingdings", "symbol", "webdings")):
                    continue
                # The effective size may come from the table style rather than the run itself.
                size = (int(rpr.get("sz", "0")) / 100) or (known[i].size_pt if i < len(known) else None)
                _style_run(r, font="Calibri", size=min(max(size, lo), hi) if size else None)
    return f"Set table text to Calibri within {lo:g}–{hi:g} pt."


@fixer("TITLE_PUNCTUATION", "BULLET_PUNCTUATION")
def _strip_punctuation(ctx, slide, shape, f):
    changed = False
    for p in shape.iter(qn("a:p")):
        ts = [t for t in p.iter(qn("a:t")) if (t.text or "").strip()]
        if ts and TRAILING.search(ts[-1].text):
            ts[-1].text = TRAILING.sub(r"\1", ts[-1].text)
            changed = True
    if not changed:
        raise Manual("No trailing punctuation found.")
    return "Removed the trailing punctuation."


def _term(pattern, repl, label):
    def fix(ctx, slide, shape, f):
        if not _replace_text(shape, re.compile(pattern, re.I), repl):
            raise Manual(f"“{label}” was not found in the text.")
        return f"Replaced “{label}” with “{repl}”."
    return fix


@fixer("INITIALS_PUNCTUATION")
def _initials(ctx, slide, shape, f):
    found = (f.actual or "").split(", ")
    changed = _replace_text(shape, re.compile(r"\b((?:[A-Z]\.){1,3}[A-Z])(?![.\w&])"), r"\1.")
    # Bare middle initials are listed by name ("John A Smith"); fix exactly those.
    for name in (n for n in found if re.fullmatch(r"[A-Z][a-z]+ [A-Z] [A-Z][a-z'’-]+", n)):
        changed |= _replace_text(shape, re.compile(re.escape(name)), re.sub(r" ([A-Z]) ", r" \1. ", name))
    if not changed:
        raise Manual("The initials were not found in the text.")
    return "Added the missing full stop to " + ", ".join(f"“{i}”" for i in found) + "."
FIXERS["TERM_PERCENTILE"] = _term(r"\bpercentile\b", "%ile", "Percentile")
FIXERS["TERM_TGT"] = _term(r"\bTGT\b", "Target", "TGT")
FIXERS["TERM_INCUMBENT"] = _term(r"\bincumbent\b", "Executive", "Incumbent")
# Only a run that is wholly a placeholder is replaced, so ranges and prose are untouched.
FIXERS["TABLE_MISSING_VALUE"] = _term(r"^\s*(?:n/?a|n\.a\.?|not applicable|-{1,2}|—)\s*$", "–", "placeholder")


@fixer("KEY_LABEL_INCONSISTENT", "CLIENT_NAME_INCONSISTENT", "HEADER_TERM_INCONSISTENT")
def _align_wording(ctx, slide, shape, f):
    # Expected/actual carry the deck's majority wording and this slide's variant.
    variants = [v.strip() for v in (f.actual or "").split(",") if v.strip()]
    target = (f.expected or "").strip()
    if not target or not variants:
        raise Manual("No consistent wording to apply.")
    changed = False
    for v in variants:
        changed |= _replace_text(shape, re.compile(re.escape(v.rstrip(":")) + r"(:?)"), target.rstrip(":") + r"\1")
    if not changed:
        raise Manual("The inconsistent wording was not found.")
    return f"Changed “{variants[0]}” to “{target}” to match the rest of the deck."


@fixer("TABLE_HEADER_STYLE")
def _header_style(ctx, slide, shape, f):
    el = ctx.element(f)
    rows = _cells(shape)[: (el.table.header_rows if el and el.table else 1)]
    fill = ctx.param("TABLE_HEADER_STYLE", "fill", "006EBE")
    for row in rows:
        for tc in row:
            _fill_cell(tc, fill)
            _tcpr(tc).set("anchor", "b")
            for r in _runs(tc):
                _style_run(r, bold=True, color="FFFFFF")
    return f"Styled the header: bold white text on #{fill}, bottom aligned."


def _row_fill(rule, default):
    def fix(ctx, slide, shape, f):
        label = (f.actual or "").split(":")[0].strip().lower()
        color = ctx.param(rule, "fill", default)
        hits = 0
        for row in _cells(shape):
            first = "".join(t.text or "" for t in row[0].iter(qn("a:t"))).strip().lower() if row else ""
            if first and first == label:
                for tc in row:
                    _fill_cell(tc, color)
                hits += 1
        if not hits:
            raise Manual("The row could not be located.")
        return f"Filled the “{label}” row with #{color}."
    return fix


FIXERS["TABLE_STATS_ROW"] = _row_fill("TABLE_STATS_ROW", "FFFFDB")
FIXERS["TABLE_CLIENT_ROW"] = _row_fill("TABLE_CLIENT_ROW", "F1FFE1")


@fixer("TABLE_CELL_MARGIN")
def _cell_margin(ctx, slide, shape, f):
    margin = int(ctx.param("TABLE_CELL_MARGIN", "margin_in", 0.03) * 914400)
    el = ctx.element(f)
    skip = el.table.header_rows if el and el.table else 1
    for row in _cells(shape)[skip:]:
        if row:
            _tcpr(row[0]).set("marL", str(margin))
    return "Set the first-column cell margin to 0.03 in."


@fixer("TABLE_BORDERS")
def _borders(ctx, slide, shape, f):
    for row in _cells(shape):
        for edge, tc in (("a:lnL", row[0]), ("a:lnR", row[-1])):
            pr = _tcpr(tc)
            for old in pr.findall(qn(edge)):
                pr.remove(old)
            ln = etree.Element(qn(edge), w="0")
            etree.SubElement(ln, qn("a:noFill"))
            pr.insert(0, ln)
    return "Removed the table's left and right borders."


@fixer("BULLET_INDENT", "BULLET_SPACING")
def _bullet_layout(ctx, slide, shape, f):
    levels = {l.level: l for l in ctx.deck.master.body_levels}
    for p in shape.iter(qn("a:p")):
        if not _paragraph_text(p).strip():
            continue
        ppr = p.find(qn("a:pPr"))
        if ppr is None:
            ppr = etree.Element(qn("a:pPr"))
            p.insert(0, ppr)
        lvl = int(ppr.get("lvl", "0"))
        spec = levels.get(lvl)
        if f.rule_id == "BULLET_INDENT" and spec and spec.indent_in is not None:
            ppr.set("marL", str(int(spec.indent_in * 914400)))
        if f.rule_id == "BULLET_INDENT" and ppr.get("algn") in {"ctr", "r", "just", "dist"}:
            ppr.set("algn", "l")
        if f.rule_id == "BULLET_SPACING":
            for tag in ("a:lnSpc", "a:spcBef"):
                for old in ppr.findall(qn(tag)):
                    ppr.remove(old)
            before = spec.space_before_pt if spec and spec.space_before_pt is not None else (6 if lvl == 0 else 0)
            lnspc = etree.Element(qn("a:lnSpc"))
            etree.SubElement(lnspc, qn("a:spcPct")).set("val", "100000")
            spc = etree.Element(qn("a:spcBef"))
            etree.SubElement(spc, qn("a:spcPts")).set("val", str(int(before * 100)))
            ppr.insert(0, spc)
            ppr.insert(0, lnspc)
    return "Reset bullet indentation to the template." if f.rule_id == "BULLET_INDENT" else "Set single line spacing and template spacing before bullets."


def _palette(ctx, slide, shape, f):
    def nearest(c):
        dist = lambda p: sum((int(c[i:i + 2], 16) - int(p[i:i + 2], 16)) ** 2 for i in (0, 2, 4))
        return min(ctx.palette, key=dist)
    swapped = set()
    for clr in shape.iter(qn("a:srgbClr")):
        val = (clr.get("val") or "").upper()
        if val and val not in ctx.palette:
            clr.set("val", nearest(val))
            swapped.add(val)
    ctx.theme_changed |= _retheme(ctx, slide, nearest)
    theme = ctx.theme_changed
    if not swapped and not theme:
        raise Manual("These colours could not be traced to an editable colour; change them by hand.")
    parts = []
    if swapped:
        parts.append("replaced " + ", ".join(f"#{c}" for c in sorted(swapped)) + " on this shape")
    if theme:
        parts.append("mapped the deck theme's " + ", ".join(f"#{c}" for c in sorted(theme)) + " to the nearest palette colours")
    return "Colours " + " and ".join(parts) + "."


def _retheme(ctx, slide, nearest) -> set[str]:
    """Theme colours apply deck-wide; replace off-palette ones once, in the theme itself."""
    theme_part = next((rel.target_part for rel in slide.slide_layout.slide_master.part.rels.values()
                       if rel.reltype.endswith("/theme")), None)
    if theme_part is None or getattr(theme_part, "_redline_done", False):
        return set()
    root = etree.fromstring(theme_part.blob)
    changed = set()
    for scheme in root.iter("{%s}clrScheme" % A):
        for clr in scheme.iter("{%s}srgbClr" % A):
            val = (clr.get("val") or "").upper()
            if val and val not in ctx.palette and scheme.find("./{%s}dk1" % A) is not None:
                parent = clr.getparent().tag.split("}")[1]
                if parent in {"dk1", "lt1"}:  # text/background black and white stay as authored
                    continue
                clr.set("val", nearest(val))
                changed.add(val)
    if changed:
        theme_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    theme_part._redline_done = True
    return changed


ADVICE = re.compile(r"^\s*(use|consider|rewrite|change|replace|try|make|ensure|keep|avoid)\b|\be\.g\.|\bfor example\b| / ", re.I)


def drop_in(quote: str | None, fix: str | None) -> bool:
    """A suggestion is applied only when it is replacement text for the quote, not advice."""
    return bool(quote and fix and fix.strip() and not ADVICE.search(fix) and fix.strip() != quote.strip()
                and len(fix) <= max(3 * len(quote), len(quote) + 120))


def _ai_rewrite(ctx, slide, shape, f):
    if not drop_in(f.quote, f.suggested_fix):
        raise Manual("The suggestion is guidance rather than exact replacement text; edit the slide by hand.")
    fix = f.suggested_fix.strip()
    for para in shape.iter(qn("a:p")):
        text = _paragraph_text(para)
        if f.quote not in text:
            continue
        # A whole-sentence suggestion for a fragment quote replaces the sentence, never duplicating words.
        whole = difflib.SequenceMatcher(None, fix, text).ratio() > difflib.SequenceMatcher(None, fix, f.quote).ratio()
        new = fix if whole and len(fix) > len(f.quote) else text.replace(f.quote, fix, 1)
        if not _replace_text(para, re.compile(re.escape(text if new == fix and whole else f.quote)), (new if whole else fix).replace("\\", r"\\")):
            _set_paragraph_text(para, new)
        return "Applied the suggested wording."
    raise Manual("The quoted text has changed; apply the suggestion by hand.")


# --- Entry point -------------------------------------------------------------------------

def is_fixable(f: Finding) -> bool:
    if f.rule_id == "TITLESLIDE_DRAFT" and not re.search(r"\bdraft\b", f.actual or "", re.I):
        return False  # other designators ("Preliminary", "v0.3") are the author's to remove
    return f.rule_id in FIXERS or (f.origin == "ai" and f.rule_id in {"TYPO", "TITLE_CASE"} and drop_in(f.quote, f.suggested_fix))


# Fixes that change how much room text needs, or the room it has.
LAYOUT_RULES = {"BULLET_FONT", "BULLET_SPACING", "BULLET_INDENT", "TITLE_FONT", "TITLESLIDE_FONT", "TITLE_POSITION",
                "CONF_STYLE", "FOOTNOTE_STYLE", "SOURCE_NOTE_STYLE", "TABLE_CAPTION"}


def _keep_text_inside(ctx, f: Finding, shape, before) -> str:
    """After a fix, the text must still fit its box. Grow the box into free space below when the
    rule allows it; otherwise undo the change and leave it to the reviewer with the reason."""
    from app.fix import layout

    element, model = ctx.element(f), ctx.deck.slides[f.slide - 1]
    if element is None or layout.grows_to_fit(shape):
        return ""
    box = element.bbox
    if f.rule_id == "TITLE_POSITION":
        box = ctx.deck.master.title_bbox_by_layout.get(model.layout_name) or box
    sizes = [max((r.size_pt or 0) for r in p.runs) if p.runs else None for p in element.paragraphs]
    need = layout.text_height(shape, box.w, sizes)
    if need is None or need <= box.h * 1.08:
        return ""
    if f.rule_id not in {"TITLE_POSITION", "TITLE_FONT", "TITLESLIDE_FONT"}:
        # Room below: down to the next shape it would run into, or the confidentiality line.
        floor = min([e.bbox.y for e in model.elements if e.id != element.id and e.bbox.y >= box.y + box.h * 0.5
                     and e.bbox.x < box.x + box.w and box.x < e.bbox.x + e.bbox.w] + [ctx.deck.slide_height]) - 4 * layout.PT
        if box.y + need <= floor:
            _set_xfrm(shape, type(box)(x=box.x, y=box.y, w=box.w, h=need))
            return " The text box was made taller so the text still fits."
    shape.getparent().replace(shape, before)
    if f.rule_id == "TITLE_POSITION":
        raise Manual("At the master position this headline would overflow its box. Shorten it first, then reset its position.")
    raise Manual("At the required size this text would overflow its box. Make the box larger or shorten the text, then apply the style.")


def apply_fixes(src: Path, dst: Path, deck: Deck, findings: list[Finding], *, config: dict | None = None) -> dict[str, dict]:
    """Apply each finding's fix to a copy of src; returns {finding_id: {status, note}}."""
    prs = Presentation(str(src))
    ctx = Ctx(deck, load_config(config))
    report: dict[str, dict] = {}
    for f in findings:
        fix = FIXERS.get(f.rule_id) or (_ai_rewrite if f.origin == "ai" and is_fixable(f) else None)
        if fix is None:
            report[f.id] = {"status": "manual", "note": "This needs a person's judgement; Redline won't change it automatically."}
            continue
        try:
            slide = prs.slides[f.slide - 1]
            shape = None
            if f.element_id:
                shape = _shape(slide, int(f.element_id.split("-", 1)[1]))
            elif f.rule_id != "CONF_MISSING":
                raise Manual("This finding isn't tied to a specific shape.")
            guard = shape is not None and f.rule_id in LAYOUT_RULES and shape.find(".//" + qn("p:txBody")) is not None
            before = copy.deepcopy(shape) if guard else None
            note = fix(ctx, slide, shape, f)
            if before is not None:
                note += _keep_text_inside(ctx, f, shape, before)
            report[f.id] = {"status": "applied", "note": note}
        except Manual as m:
            report[f.id] = {"status": "manual", "note": str(m)}
        except Exception as exc:  # one bad fix must not block the others
            report[f.id] = {"status": "failed", "note": f"Could not apply automatically ({type(exc).__name__})."}
    prs.save(str(dst))
    return report
