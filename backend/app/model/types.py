"""Shared data contract. Every module reads/writes these types.

FROZEN: change only by agreement — the frontend mirrors this in frontend/src/types.ts.
Units: positions/sizes in EMU (914400 per inch); font sizes in points; colours as
uppercase 6-char hex without '#', e.g. "006EBE". `None` means "not determinable".
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

EMU_PER_INCH = 914400

Severity = Literal["error", "warning", "suggestion"]
Origin = Literal["code", "ai"]
LayoutKind = Literal["title", "section", "content", "other"]
ElementKind = Literal[
    "placeholder",
    "textbox",
    "autoshape",
    "table",
    "picture",
    "chart",
    "group",
    "line",
    "other",
]
Role = Literal[
    "title",  # slide headline (title placeholder)
    "subtitle",  # title-slide subtitle / date line
    "authors",  # title-slide author block
    "body",  # bulleted / body text
    "confidentiality",  # the legal footer statement
    "slide_number",
    "table",
    "table_caption",  # bold title sitting directly above a table
    "chart_caption",  # bold title sitting directly above a picture/chart
    "picture",
    "chart",
    "source_note",  # "Source: ..." directly below a chart/picture
    "footnote",  # bottom-of-slide notes, "(1) ...", "* ..."
    "key_label",  # the "Key:" / "Legend:" text
    "key_item",  # swatch + label pairs belonging to a key
    "callout",  # emphasis box (e.g. red outline)
    "shape",  # other drawn shapes with text
    "other",
]
BulletKind = Literal["square", "dash", "arrow", "circle", "other"]


class BBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Run(BaseModel):
    text: str
    font: Optional[str] = (
        None  # effective typeface, theme refs resolved (+mn-lt -> "Calibri")
    )
    size_pt: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color: Optional[str] = None
    superscript: bool = False


class Paragraph(BaseModel):
    text: str
    level: int = 0  # 0-based outline level
    bullet: Optional[BulletKind] = None  # normalised glyph; None = no bullet
    bullet_char: Optional[str] = None  # raw char as stored
    bullet_font: Optional[str] = None  # e.g. "Wingdings" ('§' in Wingdings == square)
    align: Optional[str] = None  # "l" | "ctr" | "r" | "just"
    indent_in: Optional[float] = None  # effective marL in inches
    space_before_pt: Optional[float] = None
    space_after_pt: Optional[float] = None
    line_spacing: Optional[float] = None  # 1.0 == single
    line_spacing_pt: Optional[float] = None  # exact leading, when set in points instead
    runs: list[Run] = Field(default_factory=list)


class Cell(BaseModel):
    row: int
    col: int
    text: str
    # Absolute slide coordinates; absent in historical extraction records.
    bbox: Optional[BBox] = None
    paragraphs: list[Paragraph] = Field(default_factory=list)
    fill: Optional[str] = None
    margin_left_in: Optional[float] = None
    anchor: Optional[str] = None  # "t" | "ctr" | "b"
    grid_span: int = 1
    row_span: int = 1
    is_spanned: bool = False  # covered by another cell's merge
    border_left: Optional[str] = None  # hex colour, or None for no border
    border_right: Optional[str] = None
    border_top: Optional[str] = None
    border_bottom: Optional[str] = None


class Table(BaseModel):
    n_rows: int
    n_cols: int
    cells: list[Cell]
    header_rows: int = 1  # best guess at header row count


class Element(BaseModel):
    id: str  # f"s{slide}-{shape_id}"
    slide: int  # 1-based
    shape_id: int
    name: str
    kind: ElementKind
    placeholder_type: Optional[str] = None  # "TITLE", "BODY", "FOOTER", ...
    role: Role = "other"
    role_confidence: float = 1.0
    bbox: BBox
    text: str = ""
    paragraphs: list[Paragraph] = Field(default_factory=list)
    table: Optional[Table] = None
    fill: Optional[str] = None
    line_color: Optional[str] = None
    # Colours outside fill/line/text: gradient stops and native chart series and points.
    extra_colors: list[str] = Field(default_factory=list)
    vertical_anchor: Optional[str] = None
    group_path: list[int] = Field(
        default_factory=list
    )  # parent group shape ids, outermost first
    source: dict[str, str] = Field(
        default_factory=dict
    )  # property -> run|paragraph|shape|layout|master|theme


class BodyLevelSpec(BaseModel):
    level: int
    bullet: Optional[BulletKind]
    bullet_char: Optional[str]
    size_pt: Optional[float]
    indent_in: Optional[float]
    space_before_pt: Optional[float]


class MasterSpec(BaseModel):
    """Canonical values read from the deck's own slide master/layouts/theme."""

    theme_major_font: Optional[str] = None
    theme_minor_font: Optional[str] = None
    title_font: Optional[str] = None
    title_size_pt: Optional[float] = None
    title_bold: Optional[bool] = None
    title_anchor: Optional[str] = None
    title_bbox_by_layout: dict[str, BBox] = Field(default_factory=dict)
    body_levels: list[BodyLevelSpec] = Field(default_factory=list)
    confidentiality_text: Optional[str] = None
    confidentiality_bbox: Optional[BBox] = None


class Slide(BaseModel):
    index: int  # 1-based
    layout_name: str
    layout_kind: LayoutKind
    hidden: bool = False
    elements: list[Element] = Field(default_factory=list)
    notes: str = ""  # speaker notes; NEVER used by rules (it's the answer key)


class Deck(BaseModel):
    filename: str
    slide_width: int
    slide_height: int
    master: MasterSpec
    slides: list[Slide]


class Finding(BaseModel):
    id: str  # stable: f"{rule_id}:{slide}:{element_id or 'slide'}[:n]"
    rule_id: str  # one of app.rules.ids.RULES
    severity: Severity
    origin: Origin
    slide: int
    element_id: Optional[str] = None
    bbox: Optional[BBox] = None  # what to highlight; element bbox if None
    title: str  # short card title, e.g. "Confidentiality statement missing"
    message: str  # one sentence, plain English
    expected: Optional[str] = None
    actual: Optional[str] = None
    quote: Optional[str] = None  # exact slide text (mandatory for origin="ai")
    rule_ref: str = ""  # guideline section, e.g. "Content Slides › Slide Title"
    suggested_fix: Optional[str] = None
    confidence: float = 1.0
    group_id: Optional[str] = None  # set by noise policy when grouped across slides
    fix: Optional[dict] = None  # {"element_id","property","value"} for future auto-fix


class AnalysisResult(BaseModel):
    deck_id: str
    filename: str
    slide_count: int
    slide_width: int
    slide_height: int
    findings: list[Finding]
    stats: dict[str, int] = Field(default_factory=dict)  # counts by severity
    # Additive contract extension: explicit coverage prevents failed AI from
    # appearing to be a clean review. Must be declared in the implementation PR.
    coverage: dict = Field(default_factory=dict)
    # What ran: {"formatting": {...}, "writing": {"state": off|completed|partial|unavailable, ...}}
    checks: dict[str, dict] = Field(default_factory=dict)
