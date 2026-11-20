import asyncio
import pytest
from app.model.types import AnalysisResult


@pytest.mark.parametrize("cancelled", [False, True])
def test_durable_worker_publishes_only_live_jobs(tmp_path, monkeypatch, cancelled):
    from app import jobs, worker

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    job = jobs.create_deck("unseen.pptx", b"fixture")
    store = jobs.state()
    assert store.claim("owner") == job

    def render(source, target, stop):
        target.mkdir(parents=True)
        (target / "1.png").write_bytes(b"png")
        (target / "total.txt").write_text("1")
        return 1

    async def analyze(path, deck_id, **kwargs):
        if cancelled:
            jobs.cancel(deck_id)
        return AnalysisResult(
            deck_id=deck_id,
            filename="original.pptx",
            slide_count=1,
            slide_width=10,
            slide_height=10,
            findings=[],
        )

    monkeypatch.setattr(worker, "render_slides", render)
    monkeypatch.setattr(worker, "analyze", analyze)
    asyncio.run(worker.run_job(job, "owner"))
    saved = jobs.get_deck(job)
    assert saved["status"] == ("cancelled" if cancelled else "done")
    if not cancelled:
        assert saved["result"]["filename"] == "unseen.pptx"
    else:
        assert saved["result"] is None


def test_worker_shutdown_waits_for_renderer_before_removing_scratch(
    tmp_path, monkeypatch
):
    import threading, time
    from app import jobs, worker

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    job = jobs.create_deck("new.pptx", b"fixture")
    jobs.state().claim("owner")
    started = threading.Event()
    stopped = threading.Event()

    def render(source, target, stop):
        target.mkdir()
        started.set()
        while not stop():
            time.sleep(0.01)
        assert target.exists(), "Scratch was removed before the renderer stopped"
        stopped.set()
        return 0

    async def analyze(path, deck_id, **kw):
        return AnalysisResult(
            deck_id=deck_id,
            filename="new.pptx",
            slide_count=1,
            slide_width=10,
            slide_height=10,
            findings=[],
        )

    monkeypatch.setattr(worker, "render_slides", render)
    monkeypatch.setattr(worker, "analyze", analyze)

    async def exercise():
        task = asyncio.create_task(worker.run_job(job, "owner"))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert stopped.is_set()

    asyncio.run(exercise())


def test_cancelled_file_operation_finishes_before_scratch_cleanup(tmp_path):
    import threading
    from app.worker import _scratch_io
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def write():
        started.set()
        assert release.wait(2)
        (tmp_path / "complete").write_bytes(b"finished")
        finished.set()

    async def exercise():
        task = asyncio.create_task(_scratch_io(write))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and (tmp_path / "complete").read_bytes() == b"finished"
    asyncio.run(exercise())


def test_worker_retries_a_database_outage_instead_of_exiting(monkeypatch):
    from app import jobs, worker
    attempts, delays = [], []

    class Store:
        def claim(self, owner):
            attempts.append(owner)
            if len(attempts) == 1:
                raise OSError("temporary database outage")
            raise asyncio.CancelledError

    async def wait(delay):
        delays.append(delay)

    monkeypatch.setattr(jobs, "state", lambda: Store())
    monkeypatch.setattr(worker.asyncio, "sleep", wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.consume())
    assert len(attempts) == 2 and delays == [5]


def test_cancel_during_analysis_keeps_the_worker_serving(tmp_path, monkeypatch):
    """A reviewer cancelling mid-analysis must not stop the queue for everyone else."""
    from app import jobs, worker

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("REDLINE_DURABLE_JOBS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    first = jobs.create_deck("first.pptx", b"fixture")
    second = jobs.create_deck("second.pptx", b"fixture")

    def render(source, target, stop):
        target.mkdir(parents=True)
        (target / "1.png").write_bytes(b"png")
        (target / "total.txt").write_text("1")
        return 1

    async def analyze(path, deck_id, **kwargs):
        if deck_id == first:
            await asyncio.sleep(60)
        return AnalysisResult(deck_id=deck_id, filename="x.pptx", slide_count=1,
                              slide_width=10, slide_height=10, findings=[])

    monkeypatch.setattr(worker, "render_slides", render)
    monkeypatch.setattr(worker, "analyze", analyze)

    async def scenario():
        serving = asyncio.create_task(worker.serve())
        for _ in range(100):
            if jobs.get_deck(first)["status"] == "analysing":
                break
            await asyncio.sleep(0.1)
        assert jobs.cancel(first)
        for _ in range(150):
            if jobs.get_deck(second)["status"] == "done":
                break
            await asyncio.sleep(0.1)
        alive = not serving.done()
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        return alive

    assert asyncio.run(scenario())
    assert jobs.get_deck(first)["status"] == "cancelled"
    assert jobs.get_deck(second)["status"] == "done"
