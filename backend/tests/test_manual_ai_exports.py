"""Historical list rewrites remain auditable without becoming exported remedies."""
import csv
import io
from copy import deepcopy

from fastapi.testclient import TestClient


def test_legacy_list_export_is_manual_without_mutating_saved_evidence(monkeypatch):
    from app import jobs, main

    monkeypatch.setenv("APP_PASSWORD", "export-test")
    monkeypatch.setattr(jobs, "durable", lambda: False)
    stored = {
        "filename": "Historical review.pptx",
        "decisions": {"list": "accepted", "typo": "accepted", "rejected": "rejected"},
        "result": {"findings": [
            {"id": "list", "slide": 1, "rule_id": "BULLET_PARALLEL", "origin": "ai",
             "severity": "suggestion", "title": "List construction", "message": "Review the sibling constructions.",
             "actual": "Market data is refreshed each spring", "expected": "Refreshed market data each spring",
             "suggested_fix": "Refreshed market data each spring"},
            {"id": "typo", "slide": 2, "rule_id": "TYPO", "origin": "ai",
             "severity": "suggestion", "title": "Typo", "message": "Correct the spelling.",
             "actual": "retrun", "expected": "return", "suggested_fix": "return"},
            {"id": "rejected", "slide": 3, "rule_id": "TYPO", "origin": "ai",
             "severity": "suggestion", "title": "Rejected", "message": "Excluded."},
        ]},
    }
    before = deepcopy(stored)
    monkeypatch.setattr(jobs, "get_deck", lambda _: stored)
    with TestClient(main.app) as client:
        assert client.get('/api/decks/history/export.csv').status_code == 401
        assert client.post('/api/login', json={"password": "export-test"}).status_code == 200
        result = client.get('/api/decks/history/export.csv')
    assert result.status_code == 200
    rows = list(csv.DictReader(io.StringIO(result.text)))
    assert len(rows) == 2
    assert rows[0]['Actual'] == 'Market data is refreshed each spring'
    assert rows[0]['Expected'] == 'Review bullet structure manually'
    assert rows[0]['Suggested fix'] == ''
    assert rows[1]['Expected'] == rows[1]['Suggested fix'] == 'return'
    assert stored == before
