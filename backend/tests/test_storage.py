"""Reviews survive losing the server's disk when Blob Storage is configured.

Runs against the Azurite emulator when it is listening on 127.0.0.1:10000
(docker run -p 10000:10000 mcr.microsoft.com/azure-storage/azurite azurite-blob --blobHost 0.0.0.0);
Set REDLINE_TEST_AZURITE_PORT to use a separate loopback emulator port; skipped
when the configured listener is absent. API-version compatibility mode may be
needed for newer SDKs; record that setting separately from real Azure validation.
"""
import os
import shutil
import socket
import time
import uuid

import pytest
from fastapi.testclient import TestClient

AZURITE_PORT = int(os.environ.get("REDLINE_TEST_AZURITE_PORT", "10000"))

AZURITE = ("DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
           "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
           f"BlobEndpoint=http://127.0.0.1:{AZURITE_PORT}/devstoreaccount1;")


def _azurite_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", AZURITE_PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _azurite_up(), reason="Azurite emulator not running")


def test_review_is_restored_after_the_disk_is_lost(tmp_path, monkeypatch):
    import io, zipfile

    def _pptx():
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for part in ("ppt/presentation.xml", "ppt/slides/slide1.xml"):
                archive.writestr(part, "<x/>")
        return out.getvalue()

    from app import jobs, main, storage
    from app.model.types import AnalysisResult

    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", AZURITE)
    monkeypatch.setenv("REDLINE_BLOB_CONTAINER", f"t{uuid.uuid4().hex[:12]}")
    storage.reset()
    disk = tmp_path / "disk"
    disk.mkdir()
    monkeypatch.setattr(jobs, "DATA_DIR", disk)

    def fake_render(pptx, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "1.png").write_bytes(b"\x89PNG")
        return 1

    async def fake_analyze(path, deck_id="x", use_ai=None, guideline=None):
        return AnalysisResult(deck_id=deck_id, filename="d.pptx", slide_count=1, slide_width=100, slide_height=75,
                              findings=[], stats={})

    monkeypatch.setattr(jobs, "render_slides", fake_render)
    monkeypatch.setattr(jobs, "analyze", fake_analyze)
    with TestClient(main.app) as client:
        assert client.post("/api/login", json={"password": "secret"}).status_code == 200
        deck_id = client.post("/api/decks", files={"file": ("Durable.pptx", _pptx())}).json()["deck_id"]
        for _ in range(50):
            if client.get(f"/api/decks/{deck_id}").json()["status"] == "done":
                break
            time.sleep(0.05)
        client.put(f"/api/decks/{deck_id}/decisions", json={"x": "accepted"})

        shutil.rmtree(disk)  # the replica's disk is gone: a restart or a new instance
        disk.mkdir()

        history = client.get("/api/decks").json()["decks"]
        assert [r["filename"] for r in history if r["deck_id"] == deck_id] == ["Durable.pptx"]
        deck = client.get(f"/api/decks/{deck_id}").json()
        assert deck["status"] == "done" and deck["decisions"] == {"x": "accepted"}
        assert client.get(f"/api/decks/{deck_id}/slides/1.png").status_code == 200
    storage.reset()


def test_shared_postgres_and_blob_survive_new_api_disk(tmp_path, monkeypatch):
    """Exercise the production state/object adapters together, not BlobMirror."""
    import os
    if os.environ.get("REDLINE_TEST_POSTGRES") != "1":
        pytest.skip("Opt in to dedicated PostgreSQL integration")
    import psycopg
    from azure.storage.blob import BlobServiceClient
    from app import jobs, objects, worker
    from app.model.types import AnalysisResult, Finding
    import asyncio

    schema = "validation_" + uuid.uuid4().hex
    # A dedicated local database; credentials via PGPASSFILE or in the URL.
    base = os.environ.get("REDLINE_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/redline")
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA " + schema)
    container = BlobServiceClient.from_connection_string(AZURITE).get_container_client("t" + uuid.uuid4().hex[:12])
    container.create_container()
    monkeypatch.setenv("DATABASE_URL", base + "?options=-csearch_path%3D" + schema)
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://test.blob.core.windows.net")
    monkeypatch.setattr(objects, "_container", lambda *args: container)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "first-replica")
    original = b"private original deck bytes"
    try:
        job = jobs.create_deck("shared.pptx", original, "durable-upload")
        assert jobs.state().claim("worker") == job

        def render(source, target, stop):
            assert source.read_bytes() == original
            target.mkdir()
            (target / "1.png").write_bytes(b"preview bytes")
            return 1

        async def analyze(path, deck_id, **kwargs):
            return AnalysisResult(deck_id=deck_id, filename="shared.pptx", slide_count=1,
                                  slide_width=10, slide_height=10,
                                  findings=[Finding(id="f", rule_id="TITLE_FONT", slide=1, origin="code",
                                                    severity="warning", title="Font", message="m")])

        monkeypatch.setattr(worker, "render_slides", render)
        monkeypatch.setattr(worker, "analyze", analyze)
        asyncio.run(worker.run_job(job, "worker"))
        jobs.update_decisions(job, {"f": "accepted"})
        jobs._state.cache_clear()
        monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "empty-new-replica")
        saved = jobs.get_deck(job)
        assert saved["status"] == "done" and saved["decisions"] == {"f": "accepted"}
        assert b"".join(objects.stream(job + "/slides/1.png")) == b"preview bytes"
        path = objects.fetch(tmp_path / "download.pptx", job + "/deck.pptx")
        assert path.read_bytes() == original
        assert jobs.create_deck("shared.pptx", original, "durable-upload") == job
    finally:
        container.delete_container()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute("DROP SCHEMA " + schema + " CASCADE")
