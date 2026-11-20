"""Optional Claude review with grounded suggestions and private caching.

Missing/refused/failed output means unavailable review, never a clean bill of
health. Logs expose failure categories, not client slide text or credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import os
import stat
import tempfile
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from app.ai.prompts import AI_RULE_IDS, PROMPT_VERSION, REPORT_TOOL, SYSTEM_PROMPT
from app.model.types import Deck, Finding, Slide
from app.rules.common import quote_bbox
from app.rules.ids import RULES

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_CONCURRENCY = 8
CALL_TIMEOUT_SECONDS = 30
MIN_CONFIDENCE = 0.7
MAX_PAYLOAD_CHARS = 60_000
MAX_CACHE_BYTES = 1_000_000
CLOSE_TIMEOUT_SECONDS = 2
_provider_slots: WeakKeyDictionary = WeakKeyDictionary()


def _provider_semaphore():
    loop = asyncio.get_running_loop()
    if loop not in _provider_slots:
        _provider_slots[loop] = asyncio.Semaphore(MAX_CONCURRENCY)
    return _provider_slots[loop]


CONTINUED = re.compile(r"\s*[\(\[]?\s*(continued|cont\.?|cont'd)\s*[\)\]]?\s*$", re.I)


def _title(slide: Slide) -> str:
    return next(
        (
            e.text.strip()
            for e in slide.elements
            if e.role == "title" and e.text.strip()
        ),
        "",
    )


def deck_context(deck: Deck) -> dict[int, dict[str, bool]]:
    """What a human sees around a slide: whether it is reference material, and whether its content
    continues across slides. One slide alone cannot show either."""
    out, in_appendix = {}, False
    base = [CONTINUED.sub("", _title(s)).rstrip(" :").lower() for s in deck.slides]
    for i, slide in enumerate(deck.slides):
        if slide.layout_kind == "section":
            in_appendix = "appendix" in _title(slide).lower()
        prev_same = i > 0 and base[i] and base[i] == base[i - 1]
        next_same = i + 1 < len(deck.slides) and base[i] and base[i] == base[i + 1]
        out[slide.index] = {
            "appendix": in_appendix,
            "continues_from_previous_slide": bool(
                prev_same or CONTINUED.search(_title(slide))
            ),
            "continues_on_next_slide": bool(next_same),
        }
    return out


def _slide_payload(slide: Slide) -> dict[str, Any]:
    """Whitelist review text; never serialise a Slide (which has notes)."""
    elements: list[dict[str, Any]] = []
    for element in slide.elements:
        item: dict[str, Any] = {"element_id": element.id, "role": element.role}
        if element.role == "title" and element.text.strip():
            item["text"] = element.text
        elif element.table is not None:
            item["cells"] = [
                {"row": cell.row, "col": cell.col, "text": cell.text}
                for cell in element.table.cells
                if not cell.is_spanned
                and cell.text.strip()
                and (cell.row < element.table.header_rows or cell.col == 0)
            ]
            if not item["cells"]:
                continue
        else:
            if element.role not in {"body", "shape", "other"}:
                continue
            paragraphs = []
            group_id = 0
            in_list = False
            ancestors: dict[int, int] = {}
            list_ids: dict[tuple, int] = {}
            for paragraph_index, paragraph in enumerate(element.paragraphs):
                if paragraph.bullet and paragraph.text.strip():
                    level = paragraph.level
                    # Sibling root bullets remain one list across their children,
                    # but sub-bullets under different parent items are not peers.
                    ancestors = {
                        depth: index
                        for depth, index in ancestors.items()
                        if depth < level
                    }
                    identity = (group_id, level, tuple(sorted(ancestors.items())))
                    list_id = list_ids.setdefault(identity, len(list_ids))
                    paragraphs.append(
                        {"text": paragraph.text, "level": level, "list_id": list_id}
                    )
                    ancestors[level] = paragraph_index
                    in_list = True
                elif paragraph.text.strip() and in_list:
                    group_id += 1
                    ancestors.clear()
                    in_list = False
            plain = "\n".join(
                p.text for p in element.paragraphs if p.text.strip() and not p.bullet
            )
            if plain:
                item["text"] = plain
            if paragraphs:
                item["paragraphs"] = paragraphs
            if not plain and not paragraphs:
                continue
        elements.append(item)
    return {"elements": elements}


def _texts(item: dict[str, Any]) -> list[str]:
    return ([item["text"]] if "text" in item else []) + [
        p["text"] for key in ("paragraphs", "cells") for p in item.get(key, [])
    ]


def _validated_findings(
    raw: Any, slide: Slide, payload: dict[str, Any]
) -> list[Finding]:
    if not isinstance(raw, list):
        return []
    submitted = {item["element_id"]: item for item in payload["elements"]}
    elements = {element.id: element for element in slide.elements}
    findings: dict[str, Finding] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule, element_id, quote = (
            item.get(k) for k in ("rule_id", "element_id", "quote")
        )
        confidence, message, fix = (
            item.get(k) for k in ("confidence", "message", "suggested_fix")
        )
        manual = item.get("manual_review") is True
        if "manual_review" in item and not isinstance(item["manual_review"], bool):
            continue
        if not isinstance(rule, str) or rule not in AI_RULE_IDS:
            continue
        if not isinstance(element_id, str) or element_id not in submitted:
            continue
        if not all(isinstance(v, str) and v.strip() for v in (quote, message)):
            continue
        # Hierarchy can need a manual list restructure rather than replacement
        # wording. Other rules and ordinary rewrite suggestions keep the exact
        # drop-in contract; a missing fix alone never qualifies as manual review.
        if manual:
            if rule != "BULLET_PARALLEL" or fix is not None:
                continue
        elif not isinstance(fix, str) or not fix.strip():
            continue
        if not any(quote in text for text in _texts(submitted[element_id])):
            continue
        if rule == "TYPO" and isinstance(fix, str) and " ".join(fix.split()) == " ".join(quote.split()):
            continue  # a spacing-only change is too trivial to cost a reviewer a decision
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not MIN_CONFIDENCE <= confidence <= 1
        ):
            continue
        element = elements[element_id]
        if rule in {"TITLE_TAKEAWAY", "TITLE_CASE"} and element.role != "title":
            continue
        list_id = None
        if rule == "BULLET_PARALLEL":
            paragraphs = submitted[element_id].get("paragraphs", [])
            matching = [p for p in paragraphs if quote in p["text"]]
            if not any(quote == p["text"] for p in matching):
                continue  # Structural evidence must quote a complete affected bullet.
            eligible = {
                match["list_id"]
                for match in matching
                if sum(
                    (p["level"], p["list_id"]) == (match["level"], match["list_id"])
                    for p in paragraphs
                )
                >= 2
            }
            if len(eligible) != 1:
                continue  # The quote must unambiguously identify one sibling list.
            list_id = next(iter(eligible))
        digest = (
            f"list-{list_id}"
            if list_id is not None
            else hashlib.sha256(quote.encode()).hexdigest()[:16]
        )
        finding_id = f"{rule}:{slide.index}:{element_id}:{digest}"
        _, _, rule_ref, title = RULES[rule]
        previous = findings.get(finding_id)
        if previous is not None and (previous.confidence, previous.quote or "") >= (
            confidence,
            quote,
        ):
            continue  # One evidence-bearing suggestion per list; retain the strongest.
        findings[finding_id] = Finding(
            id=finding_id,
            rule_id=rule,
            severity="suggestion",
            origin="ai",
            slide=slide.index,
            element_id=element_id,
            bbox=quote_bbox(element, quote),
            title=title,
            message=message.strip(),
            quote=quote,
            expected="Review bullet structure manually" if rule == "BULLET_PARALLEL" else fix.strip(),
            actual=quote,
            rule_ref=rule_ref,
            # Parallelism belongs to the entire sibling list. Even a validated
            # legacy response must not present a partial rewrite as its remedy.
            suggested_fix=None if rule == "BULLET_PARALLEL" else fix.strip(),
            confidence=confidence,
        )
    return sorted(
        findings.values(), key=lambda f: (f.rule_id, f.element_id or "", f.id)
    )


def _cache_key(model: str, payload: dict[str, Any]) -> str:
    material = {
        "model": model,
        "version": PROMPT_VERSION,
        "system": SYSTEM_PROMPT,
        "tool": REPORT_TOOL,
        "rules": {r: RULES[r] for r in AI_RULE_IDS},
        "payload": payload,
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _cache_directory() -> Path | None:
    try:
        default_root = str(Path(tempfile.gettempdir()).resolve() / "redline")
        directory = Path(os.environ.get("DATA_DIR", default_root)) / "cache" / "ai"
        if any(parent.is_symlink() for parent in (directory, *directory.parents)):
            raise OSError("symlink cache path")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        return directory
    except OSError:
        logger.warning("AI cache unavailable; continuing without disk cache")
        return None


def _read_cache(directory: Path | None, key: str) -> list[Any] | None:
    if directory is None:
        return None
    try:
        descriptor = os.open(
            directory / (key + ".json"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "r") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CACHE_BYTES:
                return None
            raw = json.load(handle)
            return raw if isinstance(raw, list) else None
    except (OSError, ValueError):
        return None


def _write_cache(directory: Path | None, key: str, findings: list[Any]) -> None:
    if directory is None:
        return
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=directory, delete=False
        ) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            json.dump(findings, handle, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, directory / (key + ".json"))
    except (OSError, ValueError):
        logger.warning("AI cache write unavailable")
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _tool_findings(response: Any) -> list[Any] | None:
    if getattr(response, "stop_reason", None) != "tool_use":
        return None
    reports = [
        block
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "tool_use"
        and getattr(block, "name", None) == "report_findings"
    ]
    if len(reports) != 1:
        return (
            None  # Multiple competing reports are ambiguous; never silently drop one.
        )
    body = getattr(reports[0], "input", None)
    if isinstance(body, dict) and isinstance(body.get("findings"), list):
        return body["findings"]
    return None


async def run_ai_checks(deck: Deck, report: dict | None = None) -> list[Finding]:
    """Review content slides; failure leaves deterministic analysis available.

    When `report` is given it is filled with what actually ran, so the product can
    say so plainly instead of implying a clean writing review.
    """
    report = report if report is not None else {}
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        logger.info("AI review skipped: ANTHROPIC_API_KEY is unset")
        report.update(state="off")
        return []
    client = None
    try:
        from anthropic import AsyncAnthropic

        model = (
            os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        )
        # Retry transient network/overload errors; a single blip should not cost a slide.
        client = AsyncAnthropic(
            api_key=key, timeout=CALL_TIMEOUT_SECONDS, max_retries=2
        )
        semaphore = _provider_semaphore()
        access_denied = asyncio.Event()
        cache = _cache_directory()
        # Any visible slide with text, not just "content" layouts: real decks often build
        # slides on a Blank layout with free text boxes, and those deserve review too.
        slides = [
            slide
            for slide in deck.slides
            if not slide.hidden
            and slide.layout_kind not in {"title", "section"}
            and any(
                e.text.strip() or e.table
                for e in slide.elements
                if e.role not in {"slide_number", "confidentiality"}
            )
        ]
        contexts = deck_context(deck)
        diagnostics = {"reviewed": 0, "cached": 0, "unavailable": 0, "filtered": 0}
        report.update(state="running", model=model, slides=len(slides))
        budget = max(1, min(500, int(os.environ.get("MAX_AI_SLIDES", "100"))))
        diagnostics["unavailable"] += max(0, len(slides) - budget)
        slides = slides[:budget]
        report["prompt_version"] = PROMPT_VERSION

        async def review(slide: Slide) -> list[Finding]:
            try:
                payload = {**_slide_payload(slide), "context": contexts[slide.index]}
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if not payload["elements"]:
                    return []
                if len(encoded) > MAX_PAYLOAD_CHARS:
                    diagnostics["unavailable"] += 1
                    logger.warning(
                        "AI slide %d review skipped: payload exceeds safe limit",
                        slide.index,
                    )
                    return []
                digest = _cache_key(model, payload)
                raw = _read_cache(cache, digest)
                if raw is None:
                    async with semaphore:
                        if access_denied.is_set():
                            diagnostics["unavailable"] += 1
                            logger.warning(
                                "AI slide %d review skipped: provider access unavailable",
                                slide.index,
                            )
                            return []
                        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                            response = await client.messages.create(
                                model=model,
                                max_tokens=4096,
                                system=SYSTEM_PROMPT,
                                tools=[REPORT_TOOL],
                                tool_choice={"type": "auto"},
                                messages=[{"role": "user", "content": encoded}],
                            )
                    raw = _tool_findings(response)
                    if raw is None:
                        diagnostics["unavailable"] += 1
                        logger.warning(
                            "AI slide %d review unavailable: response lacks complete review tool output",
                            slide.index,
                        )
                        return []
                    validated = _validated_findings(raw, slide, payload)
                    diagnostics["reviewed"] += 1
                    diagnostics["filtered"] += len(raw) - len(validated)
                    if len(validated) < len(raw):
                        logger.info(
                            "AI review dropped ungrounded, invalid or duplicate suggestions"
                        )
                    _write_cache(
                        cache,
                        digest,
                        [
                            {
                                "rule_id": f.rule_id,
                                "element_id": f.element_id,
                                "quote": f.quote,
                                "message": f.message,
                                "suggested_fix": f.suggested_fix,
                                "manual_review": f.rule_id == "BULLET_PARALLEL" and f.suggested_fix is None,
                                "confidence": f.confidence,
                            }
                            for f in validated
                        ],
                    )
                    return validated
                diagnostics["cached"] += 1
                return _validated_findings(raw, slide, payload)
            except Exception as exc:
                if getattr(exc, "status_code", None) in (401, 403):
                    access_denied.set()  # Do not keep sending client text with unusable credentials.
                diagnostics["unavailable"] += 1
                logger.warning(
                    "AI slide %d review unavailable (%s)",
                    slide.index,
                    type(exc).__name__,
                )
                return []

        results = await asyncio.gather(*(review(slide) for slide in slides))
        found = [finding for findings in results for finding in findings]
        done = diagnostics["reviewed"] + diagnostics["cached"]
        report.update(
            diagnostics,
            suggestions=len(found),
            state="completed"
            if not diagnostics["unavailable"]
            else ("partial" if done else "unavailable"),
        )
        logger.info(
            "AI review completion: content=%d reviewed=%d cached=%d unavailable=%d filtered=%d",
            len(slides),
            diagnostics["reviewed"],
            diagnostics["cached"],
            diagnostics["unavailable"],
            diagnostics["filtered"],
        )
        return found
    except Exception as exc:
        logger.warning("AI review unavailable (%s)", type(exc).__name__)
        report.update(state="unavailable")
        return []
    finally:
        if client is not None:
            try:
                async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                    await client.close()
            except Exception:
                logger.warning("AI client close failed or exceeded cleanup deadline")
