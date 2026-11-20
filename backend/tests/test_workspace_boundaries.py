"""A clean store must not borrow another workspace's work, tokens or decisions."""
from contextlib import contextmanager
import os
import uuid

import pytest

from app.state import State


@contextmanager
def isolated_backend(monkeypatch, postgres):
    monkeypatch.delenv('AZURE_STORAGE_ACCOUNT_URL', raising=False)
    monkeypatch.delenv('REDLINE_WORKSPACE', raising=False)
    if not postgres:
        monkeypatch.delenv('DATABASE_URL', raising=False)
        yield
        return
    if os.environ.get('REDLINE_TEST_POSTGRES') != '1':
        pytest.skip('Requires the dedicated local PostgreSQL test service')
    import psycopg
    schema = 'workspace_matrix_' + uuid.uuid4().hex
    base = os.environ.get('REDLINE_TEST_DATABASE_URL', 'postgresql://postgres@127.0.0.1:55432/redline')
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute('CREATE SCHEMA ' + schema)
    monkeypatch.setenv('DATABASE_URL', base + '?options=-csearch_path%3D' + schema)
    try:
        yield
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute('DROP SCHEMA ' + schema + ' CASCADE')


@pytest.mark.parametrize('postgres', [False, True], ids=['sqlite', 'postgres'])
def test_same_identifiers_have_independent_lifecycles(tmp_path, monkeypatch, postgres):
    with isolated_backend(monkeypatch, postgres):
        original = State(tmp_path)
        job = 'same-review'
        token = 'same-upload-token'
        status = {'deck_id': job, 'filename': 'original.pptx', 'status': 'queued',
                  'created_at': '2026-10-22T00:00:00+00:00', 'upload_token': token,
                  'content_sha256': 'same-content'}
        original.register(job, status, {'id': 'original-policy'})
        original.put('guidelines/shared.json', {'name': 'Original guideline'})
        original.mutate(job + '/decisions.json', lambda _: {'finding': 'accepted'})

        monkeypatch.setenv('REDLINE_WORKSPACE', 'fresh')
        clean = State(tmp_path)
        assert clean.claim('clean-owner') is None  # Must not take original work.
        assert clean.get(job + '/status.json') is None
        assert clean.get('guidelines/shared.json') is None
        clean.register(job, {**status, 'filename': 'fresh.pptx'}, {'id': 'new-policy'})
        clean.put('guidelines/shared.json', {'name': 'Fresh guideline'})
        clean.mutate(job + '/decisions.json', lambda _: {'finding': 'rejected'})

        assert original.claim('original-owner') == clean.claim('clean-owner') == job
        assert original.publish(job, 'original-owner', {'slide_count': 1, 'findings': [], 'marker': 'original'})
        assert clean.cancel_job(job)
        assert not clean.publish(job, 'clean-owner', {'slide_count': 1, 'marker': 'late'})
        assert original.get(job + '/status.json')['status'] == 'done'
        assert original.get(job + '/result.json')['marker'] == 'original'
        assert clean.get(job + '/status.json')['status'] == 'cancelled'
        assert clean.get(job + '/result.json') is None
        assert original.get(job + '/decisions.json') == {'finding': 'accepted'}
        assert clean.get(job + '/decisions.json') == {'finding': 'rejected'}
        assert original.get('guidelines/shared.json') == {'name': 'Original guideline'}
        assert clean.get('guidelines/shared.json') == {'name': 'Fresh guideline'}
        assert original.history(10, 0, 'original.pptx')[0] == [job]
        assert clean.history(10, 0, 'original.pptx')[0] == []
        # Retry tokens deduplicate only within the store where they were received.
        assert original.register('retry', status, {}) == job
        assert clean.register('retry', status, {}) == job
        assert original.claim('idle') is None
        assert clean.claim('idle') is None


def test_cached_job_state_follows_workspace_without_mutating_old_handle(tmp_path, monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs, 'DATA_DIR', tmp_path)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('AZURE_STORAGE_ACCOUNT_URL', raising=False)
    monkeypatch.delenv('REDLINE_WORKSPACE', raising=False)
    original = jobs.state()
    original.put('sentinel', {'store': 'original'})
    monkeypatch.setenv('REDLINE_WORKSPACE', 'fresh')
    fresh_store = jobs.state()
    assert fresh_store is not original and fresh_store.get('sentinel') is None
    fresh_store.put('sentinel', {'store': 'fresh'})
    monkeypatch.delenv('REDLINE_WORKSPACE')
    assert jobs.state() is original
    assert original.get('sentinel') == {'store': 'original'}
    assert fresh_store.get('sentinel') == {'store': 'fresh'}
