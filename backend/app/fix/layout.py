"""Will the text still fit its box after a fix?

A fix that makes a slide look broken (text spilling out of its box) is worse than no
fix. These estimates use real font metrics where available and err on the side of
"doesn't fit", so a doubtful change is left to the reviewer instead of applied.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pptx.oxml.ns import qn

PT = 12700  # EMU per point
PX = 96 / 72
# The bundled Carlito comes first, so every platform measures identically.
FONTS = (
    str(Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Carlito-Regular.ttf"),
    "/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
    "Carlito-Regular.ttf", "calibri.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


@lru_cache(maxsize=64)
def _font(size_px: int):
    try:
        from PIL import ImageFont
    except ImportError:
        return None, 1.0
    for i, path in enumerate(FONTS):
        try:
            # Arial is the fallback for Calibri's metrics; Calibri sets about 10% narrower.
            return ImageFont.truetype(path, max(1, size_px)), 0.9 if path.endswith("Arial.ttf") else 1.0
        except OSError:
            continue
    return None, 1.0


def _lines(text: str, size_pt: float, width_emu: int) -> int:
    width_px = width_emu / PT * PX
    font, scale = _font(round(size_pt * PX))
    measure = (lambda t: font.getlength(t) * scale) if font else (lambda t: len(t) * size_pt * PX * 0.5)
    count = 0
    for line in text.split("\n") or [""]:
        count += 1
        current = ""
        for word in line.split():
            candidate = f"{current} {word}".strip()
            if current and measure(candidate) > width_px:
                count += 1
                current = word
            else:
                current = candidate
    return count


def text_height(shape, width_emu: int, fallback_sizes: list[float] | None = None) -> int | None:
    """Estimated height in EMU the shape's text needs at `width_emu`, or None when it has no text."""
    body = shape.find(".//" + qn("p:txBody"))
    if body is None:
        return None
    bpr = body.find(qn("a:bodyPr"))
    get = (lambda k, d: int(bpr.get(k, d))) if bpr is not None else (lambda k, d: d)
    usable = width_emu - get("lIns", 91440) - get("rIns", 91440)
    total = get("tIns", 45720) + get("bIns", 45720)
    paragraphs = body.findall(qn("a:p"))
    for i, p in enumerate(paragraphs):
        text = "".join(t.text or "" for t in p.iter(qn("a:t")))
        sizes = [int(r.get("sz")) / 100 for r in p.iter(qn("a:rPr")) if r.get("sz")]
        size = max(sizes) if sizes else (fallback_sizes[i] if fallback_sizes and i < len(fallback_sizes) and fallback_sizes[i] else 18)
        ppr = p.find(qn("a:pPr"))
        spacing, before = 1.0, 0.0
        if ppr is not None:
            pct = ppr.find(f"{qn('a:lnSpc')}/{qn('a:spcPct')}")
            if pct is not None:
                spacing = int(pct.get("val")) / 100000
            pts = ppr.find(f"{qn('a:spcBef')}/{qn('a:spcPts')}")
            if pts is not None and i:
                before = int(pts.get("val")) / 100
            indent = int(ppr.get("marL", 0))
        else:
            indent = 0
        lines = _lines(text, size, max(1, usable - indent)) if text.strip() else 1
        total += int((lines * size * 1.2 * spacing + before) * PT)
    return total


def grows_to_fit(shape) -> bool:
    """PowerPoint resizes this shape to its text, so overflow can't happen."""
    bpr = shape.find(".//" + qn("a:bodyPr"))
    return bpr is not None and bpr.find(qn("a:spAutoFit")) is not None
