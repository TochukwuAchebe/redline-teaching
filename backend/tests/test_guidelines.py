import asyncio
import pytest
from pydantic import ValidationError
from app.guidelines import ProfileDraft, snapshot, check_custom
from tests.test_rules import deck, element


def draft(**kw):
    data = {
        "name": "Orion brand",
        "source": "Titles use Arial\nKeep headlines helpful",
        "checks": [{"kind": "font", "scope": "title", "value": "Arial", "clause": 1}],
        "acknowledge_gaps": True,
    }
    return ProfileDraft.model_validate(data | kw)


def test_unmapped_guidance_needs_acknowledgement():
    with pytest.raises(ValidationError, match="no automatic check"):
        draft(acknowledge_gaps=False)
    assert draft().gaps == [{"line": 2, "text": "Keep headlines helpful"}]


@pytest.mark.parametrize(
    "check",
    [
        {"kind": "font_size", "value": "nan"},
        {"kind": "font_size", "value": "0"},
        {"kind": "palette", "value": "red"},
        {"kind": "required_text", "scope": "title", "value": "Private"},
        {"kind": "font", "value": "Arial", "clause": 9},
        {"kind": "execute", "value": "print(1)"},
    ],
)
def test_invalid_custom_instructions_rejected(check):
    with pytest.raises(ValidationError):
        draft(checks=[{"scope": "all", "clause": 1} | check])


def test_snapshot_content_hash_and_versions_are_immutable():
    a = snapshot(draft())
    b = snapshot(draft())
    assert a["id"] != b["id"] and a["version"] == b["version"]
    changed = snapshot(draft(name="Other brand"))
    assert changed["version"] != a["version"]


@pytest.mark.parametrize(
    "kind,value,bad,good",
    [
        ("font", "Arial", "Calibri", "Arial"),
        ("font_size", "18", 16, 18),
        ("palette", "#112233, FFFFFF", "006EBE", "112233"),
    ],
)
def test_custom_checks_have_positive_and_clean_controls(kind, value, bad, good):
    p = snapshot(
        draft(
            source="Use the specified style",
            checks=[{"kind": kind, "value": value, "scope": "title", "clause": 1}],
        )
    )
    e = element("title")
    d = deck([e])
    r = e.paragraphs[0].runs[0]
    field = {"font": "font", "font_size": "size_pt", "palette": "color"}[kind]
    setattr(r, field, bad)
    findings = check_custom(d, p)
    assert len(findings) == 1 and findings[0].element_id == e.id
    assert "Orion brand" in findings[0].rule_ref and "line 1" in findings[0].rule_ref
    setattr(r, field, good)
    assert check_custom(d, p) == []


def test_required_text_uses_native_slide_text_not_notes():
    p = snapshot(
        draft(
            source="Each slide says Private",
            checks=[
                {
                    "kind": "required_text",
                    "value": "Private",
                    "scope": "all",
                    "clause": 1,
                }
            ],
        )
    )
    d = deck([element("body", "Public")])
    d.slides[0].notes = "Private"
    assert len(check_custom(d, p)) == 1
    d.slides[0].elements[0].text = "Private"
    assert check_custom(d, p) == []


def test_custom_pipeline_never_runs_acme_or_acme_ai(monkeypatch):
    from app import pipeline

    p = snapshot(draft(source="Titles use Arial"))
    monkeypatch.setattr(pipeline, "extract_deck", lambda _: deck([element("title")]))

    def forbidden(*a, **k):
        pytest.fail("ACME evaluator leaked into custom mode")

    monkeypatch.setattr(pipeline, "run_rules", forbidden)
    monkeypatch.setattr(pipeline, "run_ai_checks", forbidden)
    result = asyncio.run(pipeline.analyze("unseen.pptx", guideline=p, use_ai=True))
    assert len(result.findings) == 1 and result.findings[0].rule_id.startswith(
        "CUSTOM_"
    )
    assert result.coverage["ai"]["state"] == "not_applicable"


def test_conflicting_overlapping_checks_are_not_silently_saved():
    with pytest.raises(ValidationError, match="conflicting"):
        draft(
            checks=[
                {"kind": "font", "scope": "all", "value": "Arial", "clause": 1},
                {"kind": "font", "scope": "title", "value": "Calibri", "clause": 1},
            ]
        )


def test_custom_guideline_api_pins_snapshot_and_recheck(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import jobs, main

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("APP_PASSWORD", "fixture")
    monkeypatch.setenv(
        "SESSION_SECRET", "local-validation-session-secret-32-characters"
    )
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.setenv("REDLINE_RUN_WORKERS", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    from tests.test_api import _pptx

    with TestClient(main.app) as c:
        assert c.get("/api/guidelines").status_code == 401
        c.post("/api/login", json={"password": "fixture"})
        profile = c.post("/api/guidelines", json=draft().model_dump()).json()
        assert profile["unmapped_clauses"][0]["line"] == 2
        response = c.post(
            "/api/decks",
            data={"guideline_id": profile["id"]},
            files={"file": ("new.pptx", _pptx())},
        )
        assert response.status_code == 200
        job = response.json()["deck_id"]
        before = c.get("/api/decks/" + job).json()
        assert before["guideline"]["version"] == profile["version"]
        newer = c.post(
            "/api/guidelines", json=draft(name="New rules").model_dump()
        ).json()
        assert newer["version"] != profile["version"]
        recheck = c.post("/api/decks/" + job + "/recheck").json()["deck_id"]
        assert c.get("/api/decks/" + recheck).json()["guideline"] == before["guideline"]
        bad = c.post(
            "/api/decks",
            data={"guideline_id": "missing"},
            files={"file": ("new.pptx", _pptx())},
        )
        assert bad.status_code == 400
        assert len(c.get("/api/guidelines").json()["guidelines"]) == 2


def test_acme_conflicts_are_advisory_without_dropping_evidence():
    from app.acme_policy import apply_interpretation
    from app.model.types import Finding

    f = Finding(
        id="x",
        rule_id="TABLE_ZEBRA",
        severity="warning",
        origin="code",
        slide=1,
        title="Row shading",
        message="Row does not alternate.",
        expected="F2F2F2",
        actual="FFFFFF",
    )
    [out] = apply_interpretation([f])
    assert out.severity == "suggestion" and out.expected == "F2F2F2"
    assert "disputed" in out.rule_ref and "discourages shading" in out.message
