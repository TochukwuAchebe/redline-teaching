"""User-reviewed, immutable guideline profiles. Uploaded prose is data, not code.

Custom checks never inherit ACME rules. Unmapped clauses stay explicit.
"""

from __future__ import annotations
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.model.types import Deck, Finding


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["font", "font_size", "palette", "required_text"]
    scope: Literal["title", "body", "all"] = "all"
    value: str = Field(min_length=1, max_length=1000)
    clause: int = Field(ge=1)
    severity: Literal["warning", "suggestion"] = "warning"

    @model_validator(mode="after")
    def validate_value(self):
        if self.kind == "font_size":
            try:
                value = float(self.value)
            except ValueError:
                raise ValueError("Font size must be a number in points")
            if not 1 <= value <= 200:
                raise ValueError("Font size must be between 1 and 200 points")
        if self.kind == "palette":
            values = re.split(r"[,\s]+", self.value)
            if len(values) > 32 or any(
                not re.fullmatch(r"#?[0-9a-fA-F]{6}", v) for v in values
            ):
                raise ValueError(
                    "Use up to 32 six-digit hex colours separated by commas"
                )
        if self.kind == "required_text" and self.scope != "all":
            raise ValueError("Required text is checked on every slide; choose all")
        return self


class ProfileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=30000)
    checks: list[Check] = Field(min_length=1, max_length=100)
    acknowledge_gaps: bool = False

    @model_validator(mode="after")
    def coverage(self):
        lines = self.clauses
        if len(lines) > 300:
            raise ValueError("Use at most 300 nonempty guideline lines")
        if any(c.clause > len(lines) for c in self.checks):
            raise ValueError("Each check must refer to an existing guideline line")
        for i, check in enumerate(self.checks):
            for other in self.checks[:i]:
                same_scope = check.scope == other.scope or "all" in {
                    check.scope,
                    other.scope,
                }
                if (
                    check.kind == other.kind
                    and same_scope
                    and check.kind != "required_text"
                ):
                    if check.value.casefold() != other.value.casefold():
                        raise ValueError(
                            "Overlapping checks prescribe conflicting values; resolve them before saving"
                        )
        if self.gaps and not self.acknowledge_gaps:
            raise ValueError(
                "Some guideline lines have no automatic check. Review and acknowledge these gaps before saving."
            )
        return self

    @property
    def clauses(self):
        return [line.strip() for line in self.source.splitlines() if line.strip()]

    @property
    def gaps(self):
        mapped = {c.clause for c in self.checks}
        return [
            {"line": i, "text": text}
            for i, text in enumerate(self.clauses, 1)
            if i not in mapped
        ]


def snapshot(draft: ProfileDraft) -> dict:
    data = draft.model_dump()
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return {
        **data,
        "id": uuid.uuid4().hex,
        "version": digest,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "unmapped_clauses": draft.gaps,
        "engine": "custom-deterministic-v1",
    }


def check_custom(deck: Deck, profile: dict) -> list[Finding]:
    draft = ProfileDraft.model_validate(
        {k: profile[k] for k in ProfileDraft.model_fields}
    )
    findings = []
    for index, check in enumerate(draft.checks, 1):
        rule = f"CUSTOM_{check.kind.upper()}_{index}"
        reference = (
            f"{draft.name} · line {check.clause}: {draft.clauses[check.clause - 1]}"
        )

        def add(slide, element, actual, message):
            findings.append(
                Finding(
                    id=f"{rule}:{slide.index}:{element.id if element else 'slide'}",
                    rule_id=rule,
                    severity=check.severity,
                    origin="code",
                    slide=slide.index,
                    element_id=element.id if element else None,
                    bbox=element.bbox if element else None,
                    title={
                        "font": "Font differs from your guideline",
                        "font_size": "Text size differs from your guideline",
                        "palette": "Colour outside your palette",
                        "required_text": "Required wording missing",
                    }[check.kind],
                    message=message,
                    expected=check.value,
                    actual=actual,
                    rule_ref=reference,
                )
            )

        for slide in deck.slides:
            if check.kind == "required_text":
                texts = [e.text for e in slide.elements]
                texts += [
                    c.text
                    for e in slide.elements
                    if e.table
                    for c in e.table.cells
                    if not c.is_spanned
                ]
                if not any(check.value in text for text in texts):
                    add(
                        slide,
                        None,
                        "Not found",
                        "The required wording was not found on this slide.",
                    )
                continue
            for element in slide.elements:
                if check.scope != "all" and element.role != check.scope:
                    continue
                runs = [r for p in element.paragraphs for r in p.runs if r.text.strip()]
                if element.table:
                    runs += [
                        r
                        for c in element.table.cells
                        if not c.is_spanned
                        for p in c.paragraphs
                        for r in p.runs
                        if r.text.strip()
                    ]
                if check.kind == "font":
                    bad = sorted(
                        {
                            r.font
                            for r in runs
                            if r.font and r.font.casefold() != check.value.casefold()
                        }
                    )
                    if bad:
                        add(
                            slide,
                            element,
                            ", ".join(bad),
                            "This text uses a font outside the selected guideline.",
                        )
                elif check.kind == "font_size":
                    bad = sorted(
                        {
                            r.size_pt
                            for r in runs
                            if r.size_pt is not None
                            and abs(r.size_pt - float(check.value)) > 0.05
                        }
                    )
                    if bad:
                        add(
                            slide,
                            element,
                            ", ".join(f"{s:g} pt" for s in bad),
                            "This text differs from the required size.",
                        )
                elif check.kind == "palette":
                    allowed = {
                        v.lstrip("#").upper() for v in re.split(r"[,\s]+", check.value)
                    }
                    colors = {r.color for r in runs} | {
                        element.fill,
                        element.line_color,
                    }
                    if element.table:
                        colors |= {
                            c.fill for c in element.table.cells if not c.is_spanned
                        }
                    bad = sorted(
                        c.upper() for c in colors if c and c.upper() not in allowed
                    )
                    if bad:
                        add(
                            slide,
                            element,
                            ", ".join("#" + c for c in bad),
                            "This element contains a colour outside your allowed palette.",
                        )
    return findings


ACME_MANUAL_CHECKS = [
    "Layout: clear white space between the headline and the body, and a slide that isn't overcrowded (only direct overlaps are flagged automatically).",
    "Charts and visuals: chart titles, axis labels, legends, series colours and any text inside images (pictures can't be read automatically).",
    "Tables: subtle gridlines and white space; thin black lines above and below header, statistics and client rows; consistent alignment within each column.",
    "Shapes: the same font size across a collection of shapes.",
    "Emphasis: anything in a red callout box should also appear in the headline.",
    "Writing: board-ready tone, minimal jargon, and implications rather than narration (agent suggestions help but don't prove compliance).",
    "Disputed examples: where the guide's appendix contradicts a written rule, the written rule is flagged as a suggestion for you to decide.",
]


def builtin_snapshot() -> dict:
    """ACME source and selected interpretation are immutable per review."""
    from pathlib import Path
    import yaml

    directory = Path(__file__).parent / "config"
    source = (directory / "acme-guidelines.md").read_text()
    config = yaml.safe_load((directory / "rules.yaml").read_text())
    from app.rules.ids import RULES

    for rule in RULES:
        config.setdefault(rule, {"enabled": True})
    policy = {
        "source": source,
        "config": config,
        "interpretation": "detailed-written-v1",
        "manual_checks": ACME_MANUAL_CHECKS,
    }
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    return {"id": "acme", "name": "ACME", "version": digest, **policy}
