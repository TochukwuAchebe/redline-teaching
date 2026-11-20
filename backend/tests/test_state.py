from concurrent.futures import ThreadPoolExecutor
from app.state import State


def test_queue_claims_once_and_recovers_expired_lease(tmp_path):
    state = State(tmp_path)
    state.enqueue("job")
    with ThreadPoolExecutor(max_workers=12) as pool:
        claimed = list(pool.map(lambda n: state.claim(str(n)), range(12)))
    assert claimed.count("job") == 1
    with state.transaction() as c:
        c.execute("UPDATE queue SET expires=0 WHERE id='job'")
    assert State(tmp_path).claim("replacement") == "job"
    assert not state.heartbeat("job", "stale-owner")
    assert not state.publish("job", "stale-owner", {"slide_count": 1})
    assert state.publish("job", "replacement", {"slide_count": 1, "findings": []})
    assert state.get("job/status.json")["status"] == "done"


def test_cancelled_job_cannot_publish_or_lose_cancellation(tmp_path):
    state = State(tmp_path)
    state.enqueue("job")
    state.claim("worker")
    state.put("job/status.json", {"status": "cancelled"})
    assert not state.progress("job", "worker", status="analysing")
    assert not state.publish("job", "worker", {"slide_count": 1})
    assert State(tmp_path).get("job/status.json")["status"] == "cancelled"


def test_transactional_decisions_do_not_lose_concurrent_writes(tmp_path):
    state = State(tmp_path)

    def patch(n):
        state.mutate("decisions", lambda old: {**old, str(n): "accepted"})

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(patch, range(50)))
    assert State(tmp_path).get("decisions") == {str(n): "accepted" for n in range(50)}


def test_queue_is_fifo_and_exhaustion_is_visible(tmp_path):
    state = State(tmp_path)
    state.enqueue("zfirst")
    state.enqueue("asecond")
    assert state.claim("owner") == "zfirst"
    state.finish("zfirst", "owner")
    for _ in range(5):
        assert state.claim("owner", lease=-1) == "asecond"
    assert state.claim("owner") is None
    assert state.get("asecond/status.json")["status"] == "error"


def test_durable_upload_token_survives_state_reconstruction(tmp_path, monkeypatch):
    from app import jobs

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    assert not jobs.cancel_by_token("token-before-upload")
    jobs._cancelled_tokens.clear()
    jobs._state.cache_clear()
    job = jobs.create_deck("fresh.pptx", b"fixture", "token-before-upload")
    assert jobs.get_deck(job)["status"] == "cancelled"


def test_postgres_real_concurrency_and_literal_search(tmp_path, monkeypatch):
    import os, uuid
    import pytest

    if os.environ.get("REDLINE_TEST_POSTGRES") != "1":
        pytest.skip("Opt in to dedicated local PostgreSQL integration")
    import psycopg

    schema = "validation_" + uuid.uuid4().hex
    # A dedicated local database; credentials via PGPASSFILE or in the URL.
    base = os.environ.get("REDLINE_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/redline")
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA " + schema)
    monkeypatch.setenv("DATABASE_URL", base + "?options=-csearch_path%3D" + schema)
    try:
        state = State(tmp_path)
        state.put(
            "job/status.json",
            {
                "filename": "100%_review.pptx",
                "created_at": "2026-10-21T01:00:00Z",
                "status": "queued",
            },
        )
        state.enqueue("job")
        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = list(pool.map(lambda n: state.claim(str(n)), range(12)))
        assert claims.count("job") == 1
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(
                pool.map(
                    lambda n: state.mutate(
                        "decisions", lambda old: {**old, str(n): "accepted"}
                    ),
                    range(30),
                )
            )
        assert len(state.get("decisions")) == 30
        assert state.history(25, 0, "%_")[0] == ["job"]
        assert state.history(25, 0, "nonexistent")[1] == 0
        state.put("job/status.json", {"status": "cancelled"})
        owner = str(claims.index("job"))
        assert not state.publish("job", owner, {"slide_count": 1})
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute("DROP SCHEMA " + schema + " CASCADE")


def test_duplicate_upload_token_creates_only_one_job_and_rejects_changed_payload(
    tmp_path, monkeypatch
):
    import pytest
    from app import jobs

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def create(_):
        return jobs.create_deck("deck.pptx", b"same deck", "one-idempotency-token")

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    assert len(jobs.state().keys("/status.json")) == 1
    with pytest.raises(ValueError, match="already used"):
        jobs.create_deck("changed.pptx", b"another deck", "one-idempotency-token")
    assert (jobs.deck_dir(ids[0]) / "deck.pptx").read_bytes() == b"same deck"


def test_history_pages_include_old_matches_and_literal_punctuation(tmp_path):
    state = State(tmp_path)
    for n in range(60):
        state.put(
            f"{n:016x}/status.json",
            {
                "filename": f"{n} results.pptx",
                "created_at": f"2026-10-21T00:00:{n:02d}Z",
                "status": "done",
            },
        )
    ids, total = state.history(25, 0, "")
    assert total == 60 and len(ids) == 25
    second, _ = state.history(25, 25, "")
    assert not set(ids) & set(second)
    assert state.history(25, 0, "0 results")[1] == 6
    state.put(
        "special/status.json",
        {"filename": "100%_review.pptx", "created_at": "2026-10-22", "status": "done"},
    )
    assert state.history(25, 0, "%_")[0] == ["special"]


def _review(state, job, name):
    state.register(job, {"deck_id": job, "filename": name, "status": "done",
                         "created_at": "2026-10-22T00:00:00+00:00"},
                   {"id": "acme", "name": "ACME", "version": "v"})


def test_workspaces_share_a_database_without_sharing_reviews(tmp_path, monkeypatch):
    import pytest
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDLINE_WORKSPACE", raising=False)
    original = State(tmp_path)
    _review(original, "aaaa1111", "earlier.pptx")
    original.put("guidelines/" + "a" * 32 + ".json", {"name": "Test guideline"})

    monkeypatch.setenv("REDLINE_WORKSPACE", "fresh")
    clean = State(tmp_path)
    assert clean.history(25, 0, "")[0] == []
    assert clean.get("aaaa1111/status.json") is None
    assert clean.get("guidelines/" + "a" * 32 + ".json") is None
    _review(clean, "bbbb2222", "fresh.pptx")
    assert [k.split("/")[0] for k in clean.history(25, 0, "")[0]] == ["bbbb2222"]

    monkeypatch.delenv("REDLINE_WORKSPACE")
    again = State(tmp_path)  # the earlier workspace is untouched
    assert [k.split("/")[0] for k in again.history(25, 0, "")[0]] == ["aaaa1111"]
    assert again.get("guidelines/" + "a" * 32 + ".json") == {"name": "Test guideline"}

    monkeypatch.setenv("REDLINE_WORKSPACE", "Bad Name;")
    with pytest.raises(ValueError):
        State(tmp_path)


def test_postgres_workspace_isolation(tmp_path, monkeypatch):
    import os, uuid
    import pytest

    if os.environ.get("REDLINE_TEST_POSTGRES") != "1":
        pytest.skip("Opt in to dedicated local PostgreSQL integration")
    import psycopg

    schema = "validation_" + uuid.uuid4().hex
    base = os.environ.get("REDLINE_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/redline")
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA " + schema)
    monkeypatch.setenv("DATABASE_URL", base + "?options=-csearch_path%3D" + schema)
    try:
        monkeypatch.delenv("REDLINE_WORKSPACE", raising=False)
        _review(State(tmp_path), "aaaa1111", "earlier.pptx")
        monkeypatch.setenv("REDLINE_WORKSPACE", "fresh")
        clean = State(tmp_path)
        assert clean.history(25, 0, "")[0] == []
        _review(clean, "bbbb2222", "fresh.pptx")
        assert [k.split("/")[0] for k in clean.history(25, 0, "")[0]] == ["bbbb2222"]
        with psycopg.connect(base + "?options=-csearch_path%3D" + schema) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,))}
        assert {"documents", "fresh_documents", "fresh_queue", "fresh_reviews"} <= tables
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute("DROP SCHEMA " + schema + " CASCADE")
