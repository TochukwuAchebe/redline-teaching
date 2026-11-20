"""Operator maintenance: keeping named reviews removes the rest, with a backup first."""
import json

import pytest

from app import jobs, maintenance


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    s = jobs.state()
    for i, name in {"keep1": "Demo.pptx", "keep2": "Clean.pptx", "test1": "QA.pptx", "test2": "QA.pptx"}.items():
        s.put(f"{i}/status.json", {"filename": name, "created_at": "2026-10-23"})
        s.put(f"{i}/decisions.json", {"f": "accepted"})
    return s


def test_keep_removes_every_other_review_and_backs_it_up_first(store):
    result = maintenance.keep({"keep1", "keep2"})
    assert sorted(i for i, _ in result["kept"]) == ["keep1", "keep2"]
    assert sorted(i for i, _ in result["removed"]) == ["test1", "test2"]
    assert sorted(store.keys(".json")) == ["keep1/decisions.json", "keep1/status.json",
                                           "keep2/decisions.json", "keep2/status.json"]
    backup = json.loads((store.root / result["backup"]).read_text())
    assert set(backup) == {"test1", "test2"}
    assert len(backup["test1"]["documents"]) == 2 and len(backup["test1"]["reviews"]) == 1


def test_keep_refuses_and_removes_nothing_when_a_named_review_is_missing(store):
    with pytest.raises(SystemExit, match="nothing removed"):
        maintenance.keep({"keep1", "typo"})
    assert len(store.keys("/status.json")) == 4


def test_drop_guideline_removes_only_that_guideline_and_backs_it_up(store):
    store.put("guidelines/test.json", {"name": "Test guideline", "created_at": "2026-10-23"})
    store.put("guidelines/keep.json", {"name": "Northwind", "created_at": "2026-10-22"})
    result = maintenance.drop_guideline("test")
    assert result["removed"] == "Test guideline"
    assert [k for k in store.keys(".json") if k.startswith("guidelines/")] == ["guidelines/keep.json"]
    assert json.loads((store.root / result["backup"]).read_text())["documents"][0][0] == "guidelines/test.json"
    with pytest.raises(SystemExit, match="nothing removed"):
        maintenance.drop_guideline("test")
