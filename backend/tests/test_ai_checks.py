"""Offline tests for AI grounding, isolation, failure and bounded execution."""

import asyncio
import copy
import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import pytest

from app.ai import checks
from app.model.types import (
    BBox,
    Cell,
    Deck,
    Element,
    MasterSpec,
    Paragraph,
    Slide,
    Table,
)


def make_slide(index=1, layout_kind="content", hidden=False):
    return Slide(
        index=index,
        layout_name="Title and Content",
        layout_kind=layout_kind,
        hidden=hidden,
        notes="SECRET ANSWER KEY: issue from notes only",
        elements=[
            Element(
                id=f"s{index}-1",
                slide=index,
                shape_id=1,
                name="Title",
                kind="placeholder",
                role="title",
                bbox=BBox(x=100, y=100, w=1000, h=500),
                text="Revenue outlook",
            ),
            Element(
                id=f"s{index}-2",
                slide=index,
                shape_id=2,
                name="Body",
                kind="textbox",
                role="body",
                bbox=BBox(x=100, y=700, w=1000, h=500),
                paragraphs=[
                    Paragraph(text="We increased revenue", bullet="square"),
                    Paragraph(text="Expanding margins", bullet="square"),
                ],
            ),
        ],
    )


def make_deck(slides=None):
    return Deck(
        filename="irrelevant.pptx",
        slide_width=1200000,
        slide_height=800000,
        master=MasterSpec(),
        slides=slides if slides is not None else [make_slide()],
    )


def candidate(**changes):
    item = {
        "rule_id": "BULLET_PARALLEL",
        "element_id": "s1-2",
        "quote": "Expanding margins",
        "message": "The second bullet uses a different grammatical structure.",
        "suggested_fix": "We expanded margins",
        "confidence": 0.9,
    }
    return item | changes


def response(items=None, stop_reason="tool_use"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(
                type="tool_use", name="report_findings", input={"findings": items or []}
            )
        ],
    )


@pytest.fixture
def mock_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-test-key-not-a-real-credential")
    monkeypatch.setenv("DATA_DIR", str(tmp_path.resolve()))
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(return_value=response([candidate()]))
        ),
        close=AsyncMock(),
    )
    constructor = __import__("unittest.mock", fromlist=["Mock"]).Mock(
        return_value=client
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", constructor)
    return client, constructor


def test_missing_key_never_constructs_client_or_touches_cache(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda **kwargs: pytest.fail("SDK called")
    )
    monkeypatch.setattr(
        checks, "_cache_directory", lambda: pytest.fail("Cache accessed")
    )
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"quote": "not present on slide"},
        {"quote": ""},
        {"quote": " "},
        {"quote": "SECRET ANSWER KEY: issue from notes only"},
        {"confidence": 0.69},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": True},
        {"confidence": "0.9"},
        {"confidence": 1.1},
        {"rule_id": "CONF_MISSING"},
        {"rule_id": []},
        {"element_id": "unknown"},
        {"element_id": "s1-1"},
        {"rule_id": "TITLE_CASE"},
        {"message": ""},
        {"suggested_fix": None},
    ],
)
def test_invalid_candidates_are_rejected(changes):
    slide = make_slide()
    assert (
        checks._validated_findings(
            [candidate(**changes)], slide, checks._slide_payload(slide)
        )
        == []
    )


def test_boundary_confidence_and_stable_grounded_metadata():
    slide = make_slide()
    payload = checks._slide_payload(slide)
    values = checks._validated_findings(
        [candidate(confidence=0.7), candidate(confidence=0.7)], slide, payload
    )
    assert len(values) == 1
    found = values[0]
    assert found.origin == "ai" and found.severity == "suggestion"
    assert found.bbox == slide.elements[1].bbox and found.rule_ref == "Bulleted Lists"
    assert found.actual == found.quote and found.suggested_fix is None
    assert found.expected == "Review bullet structure manually"
    assert values == checks._validated_findings(
        [candidate(confidence=0.7)], slide, payload
    )


def test_manual_bullet_structure_is_grounded_and_cannot_be_automatically_applied():
    from app.fix import is_fixable
    slide = make_slide()
    manual = candidate(manual_review=True, suggested_fix=None,
                       message="Put this supporting detail beneath its main point; review the list hierarchy manually.")
    found = checks._validated_findings([manual], slide, checks._slide_payload(slide))
    assert len(found) == 1 and found[0].quote == "Expanding margins"
    assert found[0].suggested_fix is None and not is_fixable(found[0])
    assert found[0].expected == "Review bullet structure manually"
    assert found[0].severity == "suggestion"


@pytest.mark.parametrize('changes', [
    {'rule_id': 'TYPO'}, {'rule_id': 'TITLE_CASE'},
    {'suggested_fix': 'Replacement wording'}, {'manual_review': 'true'},
    {'confidence': .69}, {'quote': 'Expanding'}, {'quote': 'not in the slide'},
])
def test_manual_structure_contract_cannot_relax_other_validation(changes):
    slide = make_slide()
    raw = candidate(manual_review=True, suggested_fix=None) | changes
    assert not checks._validated_findings([raw], slide, checks._slide_payload(slide))


def test_manual_structure_cache_preserves_the_absence_of_a_replacement(mock_client):
    client, _ = mock_client
    client.messages.create.return_value = response([candidate(manual_review=True, suggested_fix=None)])
    first = asyncio.run(checks.run_ai_checks(make_deck()))
    second = asyncio.run(checks.run_ai_checks(make_deck()))
    assert len(first) == 1 and first == second
    assert first[0].suggested_fix is None
    assert client.messages.create.call_count == 1


def test_legacy_parallel_rewrite_is_not_presented_as_a_partial_remedy():
    slide = make_slide()
    found = checks._validated_findings([candidate(suggested_fix="We changed the timing")], slide, checks._slide_payload(slide))
    assert len(found) == 1
    assert found[0].suggested_fix is None
    assert found[0].expected == "Review bullet structure manually"


def test_parallelism_requires_two_bullets_at_same_level():
    slide = make_slide()
    slide.elements[1].paragraphs[1].level = 1
    assert (
        checks._validated_findings([candidate()], slide, checks._slide_payload(slide))
        == []
    )


def test_payload_excludes_notes_footer_nonselected_table_cells_and_filename():
    slide = make_slide()
    slide.elements += [
        Element(
            id="s1-3",
            slide=1,
            shape_id=3,
            name="Confidentiality",
            kind="textbox",
            role="confidentiality",
            text="Confidential footer text",
            bbox=BBox(x=0, y=0, w=10, h=10),
        ),
        Element(
            id="s1-4",
            slide=1,
            shape_id=4,
            name="Table",
            kind="table",
            role="table",
            bbox=BBox(x=0, y=0, w=10, h=10),
            table=Table(
                n_rows=2,
                n_cols=2,
                cells=[
                    Cell(row=0, col=0, text="Header"),
                    Cell(row=0, col=1, text="Metric"),
                    Cell(row=1, col=0, text="Company"),
                    Cell(row=1, col=1, text="Excluded data"),
                ],
            ),
        ),
    ]
    serialized = json.dumps(checks._slide_payload(slide))
    assert all(
        text not in serialized
        for text in [
            "SECRET",
            "Confidential footer",
            "Excluded data",
            "irrelevant.pptx",
        ]
    )
    assert all(text in serialized for text in ["Header", "Metric", "Company"])


def test_mocked_sdk_request_and_cache(mock_client, tmp_path):
    client, constructor = mock_client
    first = asyncio.run(checks.run_ai_checks(make_deck()))
    assert len(first) == 1
    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["model"] == checks.DEFAULT_MODEL
    assert kwargs["tool_choice"] == {
        "type": "auto"
    }  # Sonnet 5.5 rejects forced tool choice.
    assert "temperature" not in kwargs
    assert "SECRET" not in json.dumps(kwargs)
    # Transient errors are retried inside the per-call timeout, so latency stays bounded.
    assert constructor.call_args.kwargs["max_retries"] == 2
    assert constructor.call_args.kwargs["timeout"] == 30
    second = asyncio.run(checks.run_ai_checks(make_deck()))
    assert second == first
    assert client.messages.create.await_count == 1
    assert client.close.await_count == 2
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700


def test_cache_key_invalidates_on_model_prompt_and_slide_changes(monkeypatch):
    payload = checks._slide_payload(make_slide())
    baseline = checks._cache_key("model-a", payload)
    assert checks._cache_key("model-b", payload) != baseline
    changed = copy.deepcopy(payload)
    changed["elements"][0]["text"] = "Another headline"
    assert checks._cache_key("model-a", changed) != baseline
    monkeypatch.setattr(checks, "SYSTEM_PROMPT", "updated review rules")
    assert checks._cache_key("model-a", payload) != baseline


def test_corrupt_and_symlink_cache_are_not_trusted(tmp_path):
    root = tmp_path.resolve()
    (root / "bad.json").write_text("not json")
    assert checks._read_cache(root, "bad") is None
    secret = root / "secret"
    secret.write_text("[]")
    (root / "linked.json").symlink_to(secret)
    assert checks._read_cache(root, "linked") is None


def test_failure_returns_empty_and_logs_no_sensitive_exception(mock_client, caplog):
    client, _ = mock_client
    client.messages.create.side_effect = RuntimeError(
        "secret request body / credentials"
    )
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []
    assert "RuntimeError" in caplog.text
    assert "secret request" not in caplog.text
    client.close.assert_awaited_once()


def test_timeout_is_bounded_and_not_cached(mock_client, monkeypatch, tmp_path):
    client, _ = mock_client

    async def slow(**kwargs):
        await asyncio.sleep(1)

    client.messages.create.side_effect = slow
    monkeypatch.setattr(checks, "CALL_TIMEOUT_SECONDS", 0.001)
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []
    assert not list(tmp_path.rglob("*.json"))


def test_maximum_concurrency_and_one_call_per_content_slide(mock_client):
    client, _ = mock_client
    active = peak = 0

    async def delayed(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.002)
        active -= 1
        return response()

    client.messages.create.side_effect = delayed
    deck = make_deck(
        [make_slide(i) for i in range(1, 18)]
        + [
            make_slide(18, "title"),
            make_slide(19, "section"),
            make_slide(20, hidden=True),
        ]
    )
    assert asyncio.run(checks.run_ai_checks(deck)) == []
    assert client.messages.create.await_count == 17
    assert 1 < peak <= 8


@pytest.mark.parametrize("stop_reason", ["max_tokens", "end_turn", "refusal"])
def test_incomplete_or_non_tool_response_not_cached(mock_client, tmp_path, stop_reason):
    client, _ = mock_client
    client.messages.create.return_value = response(
        [candidate()], stop_reason=stop_reason
    )
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []
    assert not list(tmp_path.rglob("*.json"))


def test_model_override(mock_client, monkeypatch):
    client, _ = mock_client
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model-explicit")
    asyncio.run(checks.run_ai_checks(make_deck()))
    assert client.messages.create.await_args.kwargs["model"] == "test-model-explicit"


def test_separate_lists_within_one_textbox_are_not_compared():
    slide = make_slide()
    slide.elements[1].paragraphs.insert(1, Paragraph(text="A different section"))
    payload = checks._slide_payload(slide)
    paragraphs = payload["elements"][1]["paragraphs"]
    assert paragraphs[0]["list_id"] != paragraphs[1]["list_id"]
    assert checks._validated_findings([candidate()], slide, payload) == []


def test_blank_paragraph_does_not_break_same_list():
    slide = make_slide()
    slide.elements[1].paragraphs.insert(1, Paragraph(text=""))
    assert (
        len(
            checks._validated_findings(
                [candidate()], slide, checks._slide_payload(slide)
            )
        )
        == 1
    )


def test_cache_identity_changes_when_slide_element_ids_change():
    slide = make_slide(1)
    moved = make_slide(2)
    assert checks._cache_key(
        "same-model", checks._slide_payload(slide)
    ) != checks._cache_key("same-model", checks._slide_payload(moved))


def test_duplicate_tool_reports_are_rejected_instead_of_silently_losing_findings(
    mock_client, tmp_path
):
    client, _ = mock_client
    reports = response([candidate()])
    reports.content += response([]).content
    client.messages.create.return_value = reports
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []
    assert not list(tmp_path.rglob("*.json"))


def test_client_cleanup_has_a_deadline(mock_client, monkeypatch, caplog):
    client, _ = mock_client

    async def never_close():
        await asyncio.sleep(1)

    client.close.side_effect = never_close
    monkeypatch.setattr(checks, "CLOSE_TIMEOUT_SECONDS", 0.001)
    assert len(asyncio.run(checks.run_ai_checks(make_deck()))) == 1
    assert "cleanup deadline" in caplog.text


def test_sdk_initialization_failure_is_safe(mock_client, caplog):
    _, constructor = mock_client
    constructor.side_effect = RuntimeError("not safe to log request data")
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []
    assert "not safe to log" not in caplog.text


def test_completion_log_distinguishes_failed_review_from_clean_review(
    mock_client, caplog
):
    client, _ = mock_client
    client.messages.create.side_effect = TimeoutError(
        "private text must not reach logs"
    )
    caplog.set_level("INFO", logger=checks.__name__)
    assert asyncio.run(checks.run_ai_checks(make_deck())) == []
    assert "content=1 reviewed=0 cached=0 unavailable=1" in caplog.text
    assert "private text" not in caplog.text


def test_oversize_payload_is_skipped_without_network(mock_client, caplog):
    client, _ = mock_client
    slide = make_slide()
    slide.elements[0].text = "x" * (checks.MAX_PAYLOAD_CHARS + 1)
    assert asyncio.run(checks.run_ai_checks(make_deck([slide]))) == []
    client.messages.create.assert_not_awaited()
    assert "payload exceeds" in caplog.text


def test_single_failed_slide_preserves_other_verified_results(mock_client):
    client, _ = mock_client
    client.messages.create.side_effect = [
        response([candidate()]),
        TimeoutError("API timeout"),
    ]
    found = asyncio.run(checks.run_ai_checks(make_deck([make_slide(1), make_slide(2)])))
    assert len(found) == 1 and found[0].slide == 1


def test_realistic_spelling_findings_on_table_header_are_grounded():
    slide = make_slide()
    slide.elements.append(
        Element(
            id="s1-8",
            slide=1,
            shape_id=8,
            name="Header",
            kind="table",
            role="table",
            bbox=BBox(x=0, y=0, w=100, h=50),
            table=Table(
                n_rows=2,
                n_cols=2,
                cells=[
                    Cell(row=0, col=0, text="Executve"),
                    Cell(row=0, col=1, text="Salary"),
                    Cell(row=1, col=0, text="Jane"),
                    Cell(row=1, col=1, text="Excluded typo"),
                ],
            ),
        )
    )
    payload = checks._slide_payload(slide)
    typo = candidate(
        rule_id="TYPO", element_id="s1-8", quote="Executve", suggested_fix="Executive"
    )
    assert len(checks._validated_findings([typo], slide, payload)) == 1
    assert (
        checks._validated_findings([typo | {"quote": "Excluded typo"}], slide, payload)
        == []
    )


def test_notes_never_affect_payload_or_cache_identity():
    original = make_slide()
    altered = original.model_copy(deep=True)
    altered.notes = "A different private answer key"
    assert checks._slide_payload(original) == checks._slide_payload(altered)
    assert checks._cache_key(
        "same-model", checks._slide_payload(original)
    ) == checks._cache_key("same-model", checks._slide_payload(altered))


def test_access_denied_stops_queued_requests(mock_client, caplog):
    client, _ = mock_client

    class Denied(Exception):
        status_code = 401

    client.messages.create.side_effect = Denied("never log credentials")
    assert (
        asyncio.run(
            checks.run_ai_checks(make_deck([make_slide(i) for i in range(1, 24)]))
        )
        == []
    )
    # The synchronous failure occurs before the next mocked request begins;
    # a real concurrent batch may already have up to8 requests in flight.
    assert client.messages.create.await_count == 1
    assert "provider access unavailable" in caplog.text
    assert "never log credentials" not in caplog.text


def test_nested_bullets_under_different_parents_are_not_siblings():
    slide = make_slide()
    slide.elements[1].paragraphs = [
        Paragraph(text="Revenue", level=0, bullet="square"),
        Paragraph(text="Sales improved", level=1, bullet="dash"),
        Paragraph(text="Costs", level=0, bullet="square"),
        Paragraph(text="Reduction in expenses", level=1, bullet="dash"),
    ]
    payload = checks._slide_payload(slide)
    paragraphs = payload["elements"][1]["paragraphs"]
    assert paragraphs[0]["list_id"] == paragraphs[2]["list_id"]
    assert paragraphs[1]["list_id"] != paragraphs[3]["list_id"]
    found = checks._validated_findings(
        [candidate(quote="Reduction in expenses")], slide, payload
    )
    assert found == []


def test_nested_bullets_with_same_parent_are_compared():
    slide = make_slide()
    slide.elements[1].paragraphs = [
        Paragraph(text="Revenue", level=0, bullet="square"),
        Paragraph(text="Sales improved", level=1, bullet="dash"),
        Paragraph(text="Expansion into new markets", level=1, bullet="dash"),
    ]
    payload = checks._slide_payload(slide)
    paragraphs = payload["elements"][1]["paragraphs"]
    assert paragraphs[1]["list_id"] == paragraphs[2]["list_id"]
    assert (
        len(
            checks._validated_findings(
                [candidate(quote="Expansion into new markets")], slide, payload
            )
        )
        == 1
    )


def test_parallelism_is_grouped_once_per_sibling_list():
    slide = make_slide()
    raw = [
        candidate(confidence=0.8),
        candidate(quote="We increased revenue", confidence=0.9),
    ]
    found = checks._validated_findings(raw, slide, checks._slide_payload(slide))
    assert len(found) == 1 and found[0].quote == "We increased revenue"
    assert found == checks._validated_findings(
        list(reversed(raw)), slide, checks._slide_payload(slide)
    )


def test_parallelism_quote_ambiguous_across_two_lists_is_rejected():
    slide = make_slide()
    slide.elements[1].paragraphs += [
        Paragraph(text="A separate section"),
        Paragraph(text="We reviewed costs", bullet="square"),
        Paragraph(text="Expanding margins", bullet="square"),
    ]
    assert (
        checks._validated_findings([candidate()], slide, checks._slide_payload(slide))
        == []
    )


def test_plain_text_on_a_blank_layout_is_reviewed_without_notes(mock_client):
    client, _ = mock_client
    slide = make_slide(layout_kind="other")
    body = slide.elements[1]
    body.text = "Please retrun the survey"
    body.paragraphs = [Paragraph(text=body.text)]
    client.messages.create.return_value = response(
        [candidate(rule_id="TYPO", quote="retrun", suggested_fix="return")]
    )
    payload = checks._slide_payload(slide)
    assert "SECRET ANSWER KEY" not in __import__("json").dumps(payload)
    report = {}
    findings = asyncio.run(checks.run_ai_checks(make_deck([slide]), report))
    assert any(f.quote == "retrun" for f in findings) and report["state"] == "completed"


def test_spacing_only_typo_suggestions_are_dropped():
    slide = make_slide(layout_kind="other")
    body = slide.elements[1]
    body.text = "Peers  meet the criteria and retrun"
    body.paragraphs = [Paragraph(text=body.text)]
    payload = checks._slide_payload(slide)
    spacing = candidate(rule_id="TYPO", element_id=body.id, quote="Peers  meet", suggested_fix="Peers meet")
    real = candidate(rule_id="TYPO", element_id=body.id, quote="retrun", suggested_fix="return")
    kept = checks._validated_findings([spacing, real], slide, payload)
    assert [f.quote for f in kept] == ["retrun"]
