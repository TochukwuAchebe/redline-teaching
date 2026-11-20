import json
from app import jobs
from app.migration import migrate


def test_legacy_without_timestamp_appears_on_an_empty_replica(tmp_path, monkeypatch):
    import os
    from datetime import datetime, timezone

    directory = tmp_path / "legacy" / "0123456789abcdef"
    directory.mkdir(parents=True)
    status_path = directory / "status.json"
    status_path.write_text(json.dumps({"status": "done", "filename": "older.pptx"}))
    os.utime(status_path, (1700000000, 1700000000))
    (directory / "deck.pptx").write_bytes(b"original")
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "new")
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    migrate(directory.parent, "legacy")
    # State remains shared while this replica has no legacy status files.
    (jobs.deck_dir(directory.name) / "deck.pptx").unlink()
    jobs.deck_dir(directory.name).rmdir()
    rows = jobs.list_decks()
    assert rows[0]["deck_id"] == directory.name
    assert rows[0]["created_at"] == datetime.fromtimestamp(
        1700000000, timezone.utc
    ).isoformat(timespec="seconds")
    assert "created_at" not in json.loads(status_path.read_text())


def test_durable_history_never_requires_a_legacy_status_file(tmp_path, monkeypatch):
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "replica")
    jobs.state().put("0123456789abcdef/status.json", {
        "status": "done", "filename": "legacy.pptx"
    })
    assert not (jobs.DATA_DIR / "0123456789abcdef/status.json").exists()
    row = jobs.list_decks()[0]
    assert row["filename"] == "legacy.pptx" and row["created_at"] == ""


def test_legacy_migration_is_repeatable_and_preserves_decisions(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    directory = legacy / "0123456789abcdef"
    directory.mkdir(parents=True)
    status = {
        "deck_id": directory.name,
        "filename": "private original.pptx",
        "status": "done",
        "created_at": "2026-10-21T01:00:00Z",
    }
    for name, data in [
        ("status.json", status),
        ("result.json", {"findings": [], "slide_count": 1}),
        ("decisions.json", {"flag": "accepted"}),
    ]:
        (directory / name).write_text(json.dumps(data))
    (directory / "deck.pptx").write_bytes(b"original")
    (directory / "slides").mkdir()
    (directory / "slides/1.png").write_bytes(b"preview")
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "new")
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    first = migrate(legacy, "491e8a4")
    second = migrate(legacy, "491e8a4")
    assert first == second and first["reviews"] == 1 and first["files"] == 2
    saved = jobs.get_deck(directory.name)
    assert saved["decisions"] == {"flag": "accepted"}
    assert (
        jobs.state().get(directory.name + "/guideline.json")["interpretation"]
        == "legacy"
    )
    assert (
        jobs.state().claim("worker") is None
    )  # Historical reviews must not be analysed again.
    assert (directory / "deck.pptx").read_bytes() == b"original"


def test_migration_cannot_overwrite_an_activated_store(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "new")
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    jobs.state().put("system/live", True)
    with pytest.raises(ValueError, match="already activated"):
        migrate(tmp_path, "test")


def test_migration_preserves_existing_corrected_artifacts(tmp_path, monkeypatch):
    directory = tmp_path / "legacy" / "0123456789abcdef"
    directory.mkdir(parents=True)
    (directory / "deck.pptx").write_bytes(b"original")
    (directory / "status.json").write_text(json.dumps({"status": "done", "filename": "deck.pptx"}))
    (directory / "fixed.pptx").write_bytes(b"corrected")
    (directory / "fixed_slides").mkdir()
    (directory / "fixed_slides/1.png").write_bytes(b"corrected preview")
    fix = {"state": "done", "counts": {"applied": 1}}
    (directory / "fix.json").write_text(json.dumps(fix))
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "new")
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    receipt = migrate(directory.parent, "legacy")
    assert receipt["verified"] and receipt["files"] == 3
    # The record is kept, plus where its corrected files now live.
    assert jobs.fix_state(directory.name) == fix | {"artifact_prefix": directory.name}
    assert (jobs.deck_dir(directory.name) / "fixed.pptx").read_bytes() == b"corrected"
    assert (jobs.deck_dir(directory.name) / "fixed_slides/1.png").read_bytes() == b"corrected preview"


def test_migrated_corrections_stay_downloadable_and_stuck_runs_reset(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    for job, fix in [("0123456789abcdef", {"state": "done", "report": {}}),
                     ("fedcba9876543210", {"state": "running"})]:
        directory = legacy / job
        directory.mkdir(parents=True)
        (directory / "status.json").write_text(json.dumps(
            {"deck_id": job, "filename": "deck.pptx", "status": "done",
             "created_at": "2026-10-21T01:00:00Z"}))
        (directory / "fix.json").write_text(json.dumps(fix))
        (directory / "deck.pptx").write_bytes(b"original")
        (directory / "fixed.pptx").write_bytes(b"corrected")
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "new")
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    migrate(legacy, "legacy")
    store = jobs.state()
    done = store.get("0123456789abcdef/fix.json")
    assert done["artifact_prefix"] == "0123456789abcdef"
    assert (jobs.DATA_DIR / done["artifact_prefix"] / "fixed.pptx").read_bytes() == b"corrected"
    assert store.get("fedcba9876543210/fix.json") == {"state": "none"}
