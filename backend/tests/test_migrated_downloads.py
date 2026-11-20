"""HTTP boundaries for corrected artifacts created before durable migration."""
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("prefix", [None, "fix-new-attempt"])
def test_corrected_routes_keep_historical_layout_and_auth(monkeypatch, tmp_path, prefix):
    from app import jobs, main, objects

    review = "0123456789abcdef"
    fix = {"state": "done"}
    if prefix:
        fix["artifact_prefix"] = prefix
    monkeypatch.setenv("APP_PASSWORD", "download-test")
    monkeypatch.setenv("SESSION_SECRET", "test-only-stable-download-session-secret")
    monkeypatch.setenv("REDLINE_RUN_WORKERS", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setattr(jobs, "durable", lambda: True)
    monkeypatch.setattr(objects, "enabled", lambda: True)
    monkeypatch.setattr(jobs, "get_deck", lambda key: {
        "deck_id": review, "filename": "Résumé review.pptx", "fix": fix,
    } if key == review else None)
    requested = []
    artifact_root = prefix or review
    content = {
        artifact_root + "/fixed.pptx": b"PK\x03\x04corrected",
        artifact_root + "/fixed_slides/1.png": b"\x89PNGpreview",
    }

    def stream(key):
        from azure.core.exceptions import ResourceNotFoundError
        requested.append(key)
        if key not in content:
            raise ResourceNotFoundError("missing fixture artifact")
        return iter([content[key]])

    monkeypatch.setattr(objects, "stream", stream)
    with TestClient(main.app) as client:
        for suffix in ["fixed.pptx", "fixed-slides/1.png"]:
            assert client.get(f"/api/decks/{review}/{suffix}").status_code == 401
        assert requested == []
        assert client.post("/api/login", json={"password": "download-test"}).status_code == 200
        download = client.get(f"/api/decks/{review}/fixed.pptx")
        assert download.status_code == 200
        assert download.content == content[artifact_root + "/fixed.pptx"]
        assert download.headers["content-type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        assert "Résumé review - corrected.pptx" in unquote(download.headers["content-disposition"])
        assert download.headers["cache-control"] == "no-store"
        image = client.get(f"/api/decks/{review}/fixed-slides/1.png")
        assert image.status_code == 200 and image.content == content[artifact_root + "/fixed_slides/1.png"]
        assert client.get(f"/api/decks/{review}/fixed-slides/2.png").status_code == 404
        assert requested == [artifact_root + "/fixed.pptx", artifact_root + "/fixed_slides/1.png", artifact_root + "/fixed_slides/2.png"]
