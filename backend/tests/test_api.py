import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.model.types import AnalysisResult, BBox, Finding


def _pptx(parts=("ppt/presentation.xml", "ppt/slides/slide1.xml")) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in parts:
            z.writestr(name, "<x/>")
    return buf.getvalue()


def _wait(client, deck_id, statuses=("done", "error", "cancelled"), timeout=10.0):
    """Poll a background job instead of sleeping a fixed time, so tests hold under load."""
    end = time.time() + timeout
    while time.time() < end:
        deck = client.get(f"/api/decks/{deck_id}").json()
        if deck["status"] in statuses:
            return deck
        time.sleep(0.05)
    raise AssertionError(f"deck {deck_id} did not finish")


def _finding(fid: str, slide: int = 1) -> Finding:
    return Finding(id=fid, rule_id="CONF_MISSING", severity="error", origin="code", slide=slide,
                   title="Confidentiality statement missing", message="Add the statement.",
                   bbox=BBox(x=0, y=0, w=10, h=10))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret")
    from app import jobs, main

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)

    def fake_render(pptx, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "1.png").write_bytes(b"\x89PNG")
        return 1

    async def fake_analyze(path, deck_id="x", use_ai=None, guideline=None):
        return AnalysisResult(deck_id=deck_id, filename="deck.pptx", slide_count=1, slide_width=100,
                              slide_height=75, findings=[_finding("a"), _finding("b")], stats={"error": 2})

    monkeypatch.setattr(jobs, "render_slides", fake_render)
    monkeypatch.setattr(jobs, "analyze", fake_analyze)
    with TestClient(main.app) as c:
        yield c


def _login(c):
    assert c.post("/api/login", json={"password": "secret"}).status_code == 200


def test_auth_required(client):
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/api/decks/abc").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/session").status_code == 401
    _login(client)
    assert client.get("/api/session").status_code == 200


def test_api_schema_is_not_public_and_responses_carry_security_headers(client):
    assert '"openapi"' not in client.get("/openapi.json").text  # no route list for unauthenticated visitors
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in response.headers  # plain HTTP, no forwarded HTTPS
    behind_tls = client.get("/api/health", headers={"x-forwarded-proto": "https"})
    assert behind_tls.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_production_cookie_is_secure_when_ingress_forwards_http(client, monkeypatch):
    monkeypatch.setenv('COOKIE_SECURE', 'true')
    # This test transport deliberately uses HTTP, matching the internal hop
    # behind Azure TLS termination. The deployment setting must override it.
    response = client.post('/api/login', json={'password': 'secret'})
    cookie = response.headers['set-cookie']
    assert response.status_code == 200
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie


def test_upload_review_export(client):
    _login(client)
    assert client.post("/api/decks", files={"file": ("x.txt", b"hi")}).status_code == 400
    deck_id = client.post("/api/decks", files={"file": ("Demo.pptx", _pptx())}).json()["deck_id"]
    for _ in range(50):
        deck = client.get(f"/api/decks/{deck_id}").json()
        if deck["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert deck["status"] == "done", deck
    assert deck["filename"] == "Demo.pptx"
    assert len(deck["result"]["findings"]) == 2
    assert client.get(f"/api/decks/{deck_id}/slides/1.png").status_code == 200

    client.put(f"/api/decks/{deck_id}/decisions", json={"a": "accepted", "b": "rejected"})
    client.put(f"/api/decks/{deck_id}/decisions", json={"b": None})
    assert client.get(f"/api/decks/{deck_id}").json()["decisions"] == {"a": "accepted"}

    csv = client.get(f"/api/decks/{deck_id}/export.csv").text
    assert csv.count("Confidentiality statement missing") == 1


@pytest.mark.parametrize("content, reason", [
    (b"not a zip", "readable .pptx"),                      # corrupt, .ppt, password-protected, lock file
    (_pptx(("word/document.xml",)), "isn't a PowerPoint"),  # some other Office zip
    (_pptx(("ppt/presentation.xml",)), "no slides"),        # empty deck
])
def test_unreviewable_uploads_rejected_before_queueing(client, content, reason):
    _login(client)
    res = client.post("/api/decks", files={"file": ("deck.pptx", content)})
    assert res.status_code == 400 and reason in res.json()["detail"]


def test_cancel_wins_over_late_completion(client, monkeypatch):
    import asyncio
    from app import jobs

    async def slow_analyze(path, deck_id="x", use_ai=None):
        await asyncio.sleep(0.3)
        return AnalysisResult(deck_id=deck_id, filename="d.pptx", slide_count=1, slide_width=100,
                              slide_height=75, findings=[], stats={})

    monkeypatch.setattr(jobs, "analyze", slow_analyze)
    _login(client)
    deck_id = client.post("/api/decks", files={"file": ("d.pptx", _pptx())}).json()["deck_id"]
    assert client.post(f"/api/decks/{deck_id}/cancel").json() == {"cancelled": True}
    time.sleep(0.6)
    deck = client.get(f"/api/decks/{deck_id}").json()
    assert deck["status"] == "cancelled" and deck["result"] is None
    # Finished or cancelled decks cannot be cancelled again.
    assert client.post(f"/api/decks/{deck_id}/cancel").json() == {"cancelled": False}


def test_history_orders_reviews_created_within_same_second(client, monkeypatch):
    from datetime import datetime, timezone
    from app import jobs

    instants = iter([
        datetime(2026, 10, 21, 12, 0, 0, 100000, tzinfo=timezone.utc),
        datetime(2026, 10, 21, 12, 0, 0, 200000, tzinfo=timezone.utc),
    ])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(instants)

    monkeypatch.setattr(jobs, "datetime", Clock)
    older = jobs.create_deck("Older.pptx", _pptx())
    newer = jobs.create_deck("Newer.pptx", _pptx())
    assert [row["deck_id"] for row in jobs.list_decks()] == [newer, older]


def test_history_lists_reviews_and_recheck_creates_a_new_one(client):
    _login(client)
    first = client.post("/api/decks", files={"file": ("Alpha.pptx", _pptx())}).json()["deck_id"]
    _wait(client, first)
    client.put(f"/api/decks/{first}/decisions", json={"a": "accepted"})
    history = client.get("/api/decks").json()["decks"]
    row = next(r for r in history if r["deck_id"] == first)
    assert row["filename"] == "Alpha.pptx" and row["status"] == "done" and row["created_at"]
    assert row["decisions_total"] == 2 and row["decisions_made"] == 1

    again = client.post(f"/api/decks/{first}/recheck").json()["deck_id"]
    assert again != first
    _wait(client, again)
    history = client.get("/api/decks").json()["decks"]
    assert history[0]["deck_id"] == again and history[0]["filename"] == "Alpha.pptx"
    # The earlier review and its decisions are untouched.
    assert client.get(f"/api/decks/{first}").json()["decisions"] == {"a": "accepted"}
    assert client.post("/api/decks/missing123/recheck").status_code == 404


def test_cancel_by_upload_token_stops_an_orphaned_job(client, monkeypatch):
    import asyncio
    from app import jobs

    async def slow_analyze(path, deck_id="x", use_ai=None):
        await asyncio.sleep(0.3)
        return AnalysisResult(deck_id=deck_id, filename="d.pptx", slide_count=1, slide_width=100,
                              slide_height=75, findings=[], stats={})

    monkeypatch.setattr(jobs, "analyze", slow_analyze)
    _login(client)
    deck_id = client.post("/api/decks", data={"upload_token": "tok-12345678"},
                          files={"file": ("d.pptx", _pptx())}).json()["deck_id"]
    assert client.post("/api/uploads/tok-12345678/cancel").json() == {"cancelled": True}
    time.sleep(0.6)
    assert client.get(f"/api/decks/{deck_id}").json()["status"] == "cancelled"
    assert client.post("/api/uploads/bad/cancel").json() == {"cancelled": False}


def test_upload_arriving_after_its_cancellation_is_born_cancelled(client):
    _login(client)
    assert client.post("/api/uploads/tok-early-0001/cancel").json() == {"cancelled": False}
    deck_id = client.post("/api/decks", data={"upload_token": "tok-early-0001"},
                          files={"file": ("d.pptx", _pptx())}).json()["deck_id"]
    assert _wait(client, deck_id)["status"] == "cancelled"


def test_repeated_wrong_passwords_are_throttled(client):
    from app import auth
    auth._failures.clear()
    codes = [client.post("/api/login", json={"password": "wrong"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    # Throttled even with the right password until the window passes.
    assert client.post("/api/login", json={"password": "secret"}).status_code == 429
    auth._failures.clear()
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200


def test_throttle_keys_on_the_client_address_without_its_port(client):
    from app import auth
    auth._failures.clear()
    codes = [client.post("/api/login", json={"password": "wrong"}, headers={"x-forwarded-for": f"203.0.113.7:{50000 + i}"}).status_code
             for i in range(6)]
    assert codes[-1] == 429
    auth._failures.clear()
