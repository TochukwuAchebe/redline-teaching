"""Shared rule utilities; unknown extracted values never imply a violation."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml

from app.model.types import BBox, Deck, Element, Finding, Paragraph, Run, Slide
from app.rules.ids import RULES

CONFIG_DIR = Path(__file__).resolve().parents[1] / 'config'


def load_config(overrides: dict | None = None) -> dict:
    config = yaml.safe_load((CONFIG_DIR / 'rules.yaml').read_text()) or {}
    # Both {rules: {...}} and direct per-rule dictionaries are accepted.
    for key, value in (overrides or {}).get('rules', overrides or {}).items():
        if key not in RULES or not isinstance(value, dict):
            raise ValueError(f'Unknown rule or invalid configuration: {key}')
        config.setdefault(key, {}).update(value)
    for key, value in config.items():
        if value.get('severity', RULES[key][0]) not in {'error', 'warning', 'suggestion'}:
            raise ValueError(f'Invalid severity for {key}')
    return config


def runs(element: Element) -> list[Run]:
    return [r for p in element.paragraphs for r in p.runs if r.text.strip()]


def cell_runs(cell: Any) -> list[Run]:
    return [r for p in cell.paragraphs for r in p.runs if r.text.strip()]


def different(actual: Any, expected: Any, tolerance: float = 0) -> bool:
    if actual is None or expected is None:
        return False
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        return abs(actual - expected) > tolerance + 1e-9
    return actual != expected


def overlap(a: BBox, b: BBox) -> int:
    return max(0, min(a.x+a.w, b.x+b.w)-max(a.x,b.x)) * max(0, min(a.y+a.h,b.y+b.h)-max(a.y,b.y))


def enclosing_bbox(boxes: Iterable[BBox | None], fallback: BBox) -> BBox:
    known = [b for b in boxes if b is not None]
    if not known:
        return fallback
    left, top = min(b.x for b in known), min(b.y for b in known)
    return BBox(x=left, y=top, w=max(b.x+b.w for b in known)-left,
                h=max(b.y+b.h for b in known)-top)


def quote_bbox(element: Element, quote: str) -> BBox:
    """Locate exact table text without guessing character-level glyph positions."""
    if not element.table:
        return element.bbox
    matches = [c for c in element.table.cells if not c.is_spanned and quote in c.text]
    # Missing geometry in any matching cell must not hide part of the evidence.
    if not matches or any(c.bbox is None for c in matches):
        return element.bbox
    return enclosing_bbox((c.bbox for c in matches), element.bbox)


def strict_majority(values: Iterable[Any], minimum: int = 3) -> Any | None:
    values = list(values)
    if len(values) < minimum:
        return None
    value, count = Counter(values).most_common(1)[0]
    return value if count > len(values)/2 else None


class Reporter:
    """Coalesce a rule's repeated evidence within an element, without truncation."""
    def __init__(self, config: dict):
        self.config = config
        self.findings: dict[str, Finding] = {}

    def add(self, rule: str, slide: Slide, element: Element | None, expected: Any,
            actual: Any, message: str, bbox: BBox | None = None,
            confidence: float = 1.0) -> None:
        options = self.config.get(rule, {})
        if not options.get('enabled', True):
            return
        severity, origin, reference, title = RULES[rule]
        identity = f'{rule}:{slide.index}:{element.id if element else "slide"}'
        if identity in self.findings:
            return
        self.findings[identity] = Finding(
            id=identity, rule_id=rule, severity=options.get('severity', severity),
            origin=origin, slide=slide.index, element_id=element.id if element else None,
            bbox=bbox if bbox is not None else (element.bbox if element else None),
            expected=str(expected), actual=str(actual), message=message,
            title=title, rule_ref=reference, confidence=confidence,
        )

    def param(self, rule: str, key: str, fallback: Any) -> Any:
        return self.config.get(rule, {}).get('params', {}).get(key, fallback)
