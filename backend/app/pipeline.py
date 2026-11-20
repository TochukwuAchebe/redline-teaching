"""Orchestration: extract -> rules -> AI -> noise policy. Task C1, see docs/TASKS.md."""

import asyncio
import os

from app.ai.checks import run_ai_checks
from app.model.extract import extract_deck
from app.model.types import AnalysisResult, Deck, Finding
from app.fix import is_fixable
from app.noise import apply_noise_policy
from app.rules import run_rules
from app.rules.common import load_config


async def analyze(
    path: str,
    deck_id: str = "local",
    use_ai: bool | None = None,
    guideline: dict | None = None,
) -> AnalysisResult:
    deck: Deck = await asyncio.to_thread(extract_deck, path)
    coverage = {"formatting": "complete", "ai": {"state": "not_run"}}
    if guideline and guideline.get("id") != "acme":
        from app.guidelines import check_custom

        findings = await asyncio.to_thread(check_custom, deck, guideline)
        # The ACME writing prompt is not a generic custom-guideline evaluator.
        use_ai = False
        coverage["ai"]["state"] = "not_applicable"
        coverage["unmapped_clauses"] = guideline.get("unmapped_clauses", [])
        coverage["guideline"] = {k: guideline[k] for k in ("id", "name", "version")}
    else:
        from app.acme_policy import apply_interpretation
        from app.guidelines import ACME_MANUAL_CHECKS

        coverage["manual_checks"] = (guideline or {}).get("manual_checks", ACME_MANUAL_CHECKS)

        config = (
            guideline.get("config") if guideline else None
        )
        if guideline and config is not None:
            # A saved policy is an allowlist, not a partial override of future rules.
            # Keep direct run_rules overrides convenient for development, while
            # rechecks cannot silently enable rules absent from the pinned snapshot.
            from app.rules.ids import RULES

            pinned = config.get("rules", config)
            config = {rule: {"enabled": False} for rule in RULES} | pinned
        findings = await asyncio.to_thread(run_rules, deck, config)
        if not guideline or guideline.get("interpretation") != "legacy":
            findings = apply_interpretation(findings)
    if use_ai is None:
        use_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))
    writing: dict = {"state": "off"}
    if use_ai:
        # A rule switched off in rules.yaml is off for the agent too: the triage lever when
        # one kind of suggestion proves unreliable, without stopping the rest of the review.
        off = {rule for rule, options in load_config().items() if not options.get("enabled", True)}
        findings += [f for f in await run_ai_checks(deck, writing) if f.rule_id not in off]
        # One headline, one takeaway suggestion: the agent's (with its wording) replaces the code backstop.
        agent_titles = {f.element_id for f in findings if f.rule_id == "TITLE_TAKEAWAY"}
        findings = [f for f in findings if not (f.rule_id == "TITLE_TOPIC_ONLY" and f.element_id in agent_titles)]
    if coverage["ai"]["state"] != "not_applicable":
        coverage["ai"] = writing
    findings = apply_noise_policy(findings, deck)
    _fill_highlight_boxes(findings, deck)
    for (
        f
    ) in findings:  # tells the UI whether accepting will change the deck automatically
        f.fix = {"auto": is_fixable(f)}
    stats = {
        s: sum(1 for f in findings if f.severity == s)
        for s in ("error", "warning", "suggestion")
    }
    return AnalysisResult(
        deck_id=deck_id,
        filename=deck.filename,
        slide_count=len(deck.slides),
        slide_width=deck.slide_width,
        slide_height=deck.slide_height,
        findings=findings,
        stats=stats,
        coverage=coverage,
        checks={
            "formatting": {
                "state": "completed",
                "slides": len(deck.slides),
                "findings": sum(1 for f in findings if f.origin == "code"),
            },
            "writing": writing,
        },
    )


def _fill_highlight_boxes(findings: list[Finding], deck: Deck) -> None:
    """The UI highlights `bbox`; default it to the flagged element's box."""
    boxes = {e.id: e.bbox for s in deck.slides for e in s.elements}
    for f in findings:
        if f.bbox is None and f.element_id in boxes:
            f.bbox = boxes[f.element_id]


def analyze_sync(path: str, **kw) -> AnalysisResult:
    return asyncio.run(analyze(path, **kw))
