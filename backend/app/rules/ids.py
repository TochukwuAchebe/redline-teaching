"""Canonical rule IDs. Rules, the eval answer key and the UI all use these strings.

rule_id -> (default severity, origin, guideline section, short title)
"""

RULES: dict[str, tuple[str, str, str, str]] = {
    # Template
    "TEMPLATE_NONCONFORMING": ("error", "code", "Requirements by PowerPoint Element", "Deck not built on the ACME template"),
    # Confidentiality
    "CONF_MISSING": ("error", "code", "Content Slides › Confidentiality Statement", "Confidentiality statement missing"),
    "CONF_TEXT_MODIFIED": ("error", "code", "Content Slides › Confidentiality Statement", "Confidentiality wording changed"),
    "CONF_MOVED": ("warning", "code", "Content Slides › Confidentiality Statement", "Confidentiality statement moved"),
    "CONF_STYLE": ("warning", "code", "Content Slides › Confidentiality Statement", "Confidentiality statement styling"),
    "CONF_OVERLAP": ("warning", "code", "Content Slides › Confidentiality Statement", "Content overlaps confidentiality statement"),
    # Title slide
    "TITLESLIDE_DRAFT": ("error", "code", "Title Slides", "Draft designator on title slide"),
    "TITLESLIDE_DATE": ("warning", "code", "Title Slides", "Title slide date missing or invalid"),
    "TITLESLIDE_AUTHORS": ("warning", "code", "Title Slides", "Authors not identified"),
    "TITLESLIDE_FONT": ("warning", "code", "Title Slides", "Title slide typography"),
    # Headlines
    "TITLE_FONT": ("warning", "code", "Content Slides › Slide Title", "Headline typography"),
    "TITLE_VERTICAL_ALIGNMENT": ("suggestion", "code", "Content Slides › Slide Title", "Headline vertical alignment"),
    "TITLE_POSITION": ("warning", "code", "Content Slides › Slide Title", "Headline moved from master position"),
    "TITLE_TOO_LONG": ("warning", "code", "Content Slides › Slide Title", "Headline exceeds 3 lines"),
    "TITLE_PUNCTUATION": ("warning", "code", "Content Slides › Slide Title", "Headline ends with punctuation"),
    "TITLE_BODY_OVERLAP": ("warning", "code", "Content Slides › Overall", "Content overlaps the headline"),
    "SHAPE_FONT": ("warning", "code", "Collections of PowerPoint Shapes", "Shape text not in the body font"),
    "TITLE_TOPIC_ONLY": ("suggestion", "code", "Content Slides › Slide Title", "Headline names a topic, not a takeaway"),
    "TITLE_TAKEAWAY": ("suggestion", "ai", "Content Slides › Slide Title", "Headline states topic, not takeaway"),
    "TITLE_CASE": ("suggestion", "ai", "Content Slides › Slide Title", "Headline not in sentence case"),
    # Bullets
    "BULLET_FONT": ("warning", "code", "Bulleted Lists", "Bullet typography"),
    "BULLET_GLYPH": ("warning", "code", "Bulleted Lists", "Wrong bullet symbol for level"),
    "BULLET_INDENT": ("warning", "code", "Bulleted Lists", "Bullet indentation"),
    "BULLET_SPACING": ("warning", "code", "Bulleted Lists", "Bullet spacing"),
    "BULLET_PUNCTUATION": ("warning", "code", "Bulleted Lists", "Bullet ends with punctuation"),
    "BULLET_PARALLEL": ("suggestion", "ai", "Bulleted Lists", "Bullets not parallel in structure"),
    # Tables
    "TABLE_FONT": ("warning", "code", "Tables › Overall", "Table font size or typeface"),
    "TABLE_HEADER_STYLE": ("warning", "code", "Tables › Table Header", "Table header styling"),
    "TABLE_HEADER_SIZE_INCONSISTENT": ("warning", "code", "Tables › Overall", "Header font size inconsistent"),
    "TABLE_ZEBRA": ("warning", "code", "Tables › Table Body", "Row shading pattern"),
    "TABLE_STATS_ROW": ("warning", "code", "Tables › Statistics Row(s)", "Statistics row styling"),
    "TABLE_CLIENT_ROW": ("warning", "code", "Tables › Client Row(s)", "Client row styling"),
    "TABLE_CELL_MARGIN": ("warning", "code", "Tables › Overall", "First column cell margin"),
    "TABLE_BORDERS": ("warning", "code", "Tables › Overall", "Left/right table borders"),
    "TABLE_NUMBER_FORMAT": ("warning", "code", "Tables › Overall", "Number formatting"),
    "TABLE_MISSING_VALUE": ("warning", "code", "Tables › Overall", "Missing value not shown as an en-dash or blank"),
    "TABLE_UNITS_MISSING": ("suggestion", "code", "Tables › Table Header", "Units not stated"),
    "TABLE_CAPTION": ("warning", "code", "Tables › Table Title", "Table title placement or styling"),
    "TABLE_MERGED_CELL": ("suggestion", "code", "Tables › Overall", "Possibly unintended merged cells"),
    # Terminology / text
    "TERM_PERCENTILE": ("warning", "code", "Tables › Overall", "Use \"%ile\", not \"Percentile\""),
    "TERM_INCUMBENT": ("warning", "code", "Tables › Table Header", "Use \"Executive\", not \"Incumbent\""),
    "TERM_TGT": ("warning", "code", "Other", "Spell out \"Target\""),
    "LEFTOVER_COMMENT": ("warning", "code", "Writing Style", "Leftover reviewer comment"),
    "INITIALS_PUNCTUATION": ("warning", "code", "Writing Style", "Initials missing a full stop"),
    "TYPO": ("suggestion", "ai", "Writing Style", "Possible typo or naming error"),
    # Footnotes
    "FOOTNOTE_UNMATCHED": ("warning", "code", "Footnotes & Sources", "Footnote marker without matching note"),
    "FOOTNOTE_STYLE": ("warning", "code", "Footnotes & Sources", "Footnote styling"),
    "SOURCE_NOTE_STYLE": ("warning", "code", "Chart Source Notes", "Chart source note styling"),
    # Colour
    "COLOR_OFF_PALETTE": ("warning", "code", "ACME Color Palette", "Colour not in ACME palette"),
    # Deck consistency
    "KEY_LABEL_INCONSISTENT": ("warning", "code", "Writing Style › Consistent terminology", "\"Key\" vs \"Legend\" inconsistent"),
    "KEY_ORDER_INCONSISTENT": ("warning", "code", "Writing Style › Consistent terminology", "Key items in different order"),
    "KEY_SPACING": ("warning", "code", "Content Slide Content › Overall", "Key items unevenly spaced"),
    "KEY_BOX_INCONSISTENT": ("warning", "code", "Writing Style › Consistent terminology", "Key styled differently from other slides"),
    "KEY_MISSING_LABEL": ("warning", "code", "Charts and Visuals › Content", "Key swatch without label"),
    "TERM_INCONSISTENT": ("warning", "code", "Writing Style & Terminology", "Same plan called by different names"),
    "CLIENT_NAME_INCONSISTENT": ("warning", "code", "Writing Style › Consistent terminology", "Client name used inconsistently"),
    "HEADER_TERM_INCONSISTENT": ("suggestion", "code", "Writing Style › Consistent terminology", "Column header wording inconsistent"),
    # Off by default (guide says it, reviewer never flags it)
    "MARGIN_EDGE": ("suggestion", "code", "Content Slide Content › Overall", "Content close to slide edge"),
}
