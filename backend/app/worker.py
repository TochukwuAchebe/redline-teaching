"""Leased queue consumers. API replicas may run workers or dedicate worker replicas.

The database owns work; local scratch directories are never the durable source.
"""

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
import tempfile
import threading
import uuid
from app import jobs, objects
from app.pipeline import analyze
from app.render import render_slides

log = logging.getLogger("redline.worker")


async def _scratch_io(function, *args, **kwargs):
    """Cancellation must not delete scratch while a file-owning thread uses it."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def run_job(job: str, owner: str) -> None:
    store = await asyncio.to_thread(jobs.state)
    status = await asyncio.to_thread(store.get, job + "/status.json", {})
    if status.get("status") in ("cancelled", "done", "error"):
        await asyncio.to_thread(store.finish, job, owner)
        return
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="redline-worker-") as scratch:
        directory = Path(scratch)
        pptx = directory / "deck.pptx"
        # Local adapter uses the existing original; Azure workers download privately.
        local = jobs.deck_dir(job) / "deck.pptx"
        if objects.enabled():
            await _scratch_io(objects.fetch, pptx, job + "/deck.pptx")
        else:
            import shutil

            await _scratch_io(shutil.copyfile, local, pptx)
        if not pptx.exists():
            raise RuntimeError("Stored original is unavailable")
        await asyncio.to_thread(store.progress, job, owner, status="rendering", rendered=0)
        profile = await asyncio.to_thread(store.get, job + "/guideline.json")
        render = asyncio.create_task(
            asyncio.to_thread(render_slides, pptx, directory / "slides", stop.is_set)
        )
        analysis = asyncio.create_task(
            analyze(str(pptx), deck_id=job, guideline=profile)
        )
        uploaded = set()
        preview_lock = asyncio.Lock()

        async def publish_previews():
            async with preview_lock:
                await _publish_previews()

        async def _publish_previews():
            for image in (directory / "slides").glob("*.png"):
                if image.name in uploaded:
                    continue
                if stop.is_set() or await asyncio.to_thread(jobs.is_cancelled, job):
                    return
                if objects.enabled():
                    await _scratch_io(
                        objects.put, image, job + "/slides/" + image.name
                    )
                else:
                    import shutil

                    target = jobs.deck_dir(job) / "slides" / image.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    await _scratch_io(shutil.copyfile, image, target)
                uploaded.add(image.name)

        async def monitor():
            while True:
                try:
                    if not await asyncio.to_thread(
                        store.heartbeat, job, owner
                    ) or await asyncio.to_thread(jobs.is_cancelled, job):
                        stop.set()
                        analysis.cancel()
                        return False
                    slides = directory / "slides"
                    total = slides / "total.txt"
                    await publish_previews()
                    await asyncio.to_thread(
                        store.progress,
                        job,
                        owner,
                        rendered=len(uploaded),
                        **(
                            {"slide_count": int(total.read_text())}
                            if total.exists()
                            else {}
                        ),
                    )
                except Exception:
                    # A transient database error must not end the heartbeat; the
                    # lease is long enough to survive a few missed beats.
                    log.warning("Progress update failed for review %s; retrying", job)
                await asyncio.sleep(2)

        watch = asyncio.create_task(monitor())
        try:
            count = await asyncio.shield(render)
            if await asyncio.to_thread(jobs.is_cancelled, job) or stop.is_set():
                return
            await asyncio.to_thread(store.progress,
                job, owner, status="analysing", rendered=count, slide_count=count
            )
            try:
                result = await analysis
            except asyncio.CancelledError:
                # Our own monitor cancelled the analysis (reviewer cancel or lost
                # lease). End this job quietly so the consumer keeps serving the queue.
                if stop.is_set() and not asyncio.current_task().cancelling():
                    return
                raise
            result.filename = status.get("filename", result.filename)
            if stop.is_set() or await asyncio.to_thread(jobs.is_cancelled, job):
                return
            # Only complete PNGs are published. Originals and the immutable policy
            # were persisted before enqueue; result publication is lease-fenced.
            await publish_previews()
            if len(uploaded) != count:
                raise RuntimeError("Not all rendered previews were persisted")
            await asyncio.to_thread(store.publish, job, owner, result.model_dump())
        finally:
            stop.set()
            analysis.cancel()
            watch.cancel()
            # Ensure LibreOffice has exited before deleting its scratch directory.
            await asyncio.gather(render, analysis, watch, return_exceptions=True)


async def run_fix(task: str, owner: str) -> None:
    from app.fix import apply_fixes
    from app.model.extract import extract_deck
    from app.model.types import Finding
    from datetime import datetime, timezone
    import shutil

    store = await asyncio.to_thread(jobs.state)
    info = await asyncio.to_thread(store.get, "tasks/" + task)
    review = info["review"]
    accepted = [Finding.model_validate(f) for f in info["accepted"]]
    stop = threading.Event()

    async def heartbeat():
        while True:
            try:
                if not await asyncio.to_thread(store.heartbeat, task, owner):
                    stop.set()
                    return
            except Exception:
                log.warning("Heartbeat failed for task %s; retrying", task)
            await asyncio.sleep(2)

    watch = asyncio.create_task(heartbeat())
    render = None
    try:
        with tempfile.TemporaryDirectory(prefix="redline-fixes-") as scratch:
            directory = Path(scratch)
            original = directory / "deck.pptx"
            fixed = directory / "fixed.pptx"
            if objects.enabled():
                await _scratch_io(objects.fetch, original, review + "/deck.pptx")
            else:
                await _scratch_io(
                    shutil.copyfile, jobs.deck_dir(review) / "deck.pptx", original
                )
            deck = await _scratch_io(extract_deck, str(original))
            if accepted:
                report = await _scratch_io(
                    apply_fixes, original, fixed, deck, accepted,
                    config=(info.get("guideline") or {}).get("config")
                )
            else:
                report = {}
                await _scratch_io(shutil.copyfile, original, fixed)
            from app import redesign

            upgrades = await _scratch_io(redesign.apply_chosen, fixed, info.get("upgrades") or {}, directory)
            added = sum(1 for u in upgrades.values() if u["status"] == "applied")
            corrected = await _scratch_io(extract_deck, str(fixed))
            if len(corrected.slides) != len(deck.slides) + added:
                raise RuntimeError("Correction changed the slide count")
            remaining = {
                f.id
                for f in (
                    await analyze(
                        str(fixed),
                        deck_id=review,
                        use_ai=False,
                        guideline=info.get("guideline"),
                    )
                ).findings
            }
            for f in accepted:
                row = report[f.id]
                if row["status"] == "applied":
                    row["verified"] = (
                        None if f.origin == "ai" else f.id not in remaining
                    )
                    if row["verified"] is False:
                        row.update(
                            status="manual",
                            note=row["note"]
                            + " Rechecking still sees this issue; please inspect it manually.",
                        )
            render = asyncio.create_task(
                asyncio.to_thread(
                    render_slides, fixed, directory / "fixed_slides", stop.is_set
                )
            )
            try:
                await asyncio.shield(render)
                prefix = review + "/fixes/" + task
                for path in [
                    fixed,
                    *sorted((directory / "fixed_slides").glob("*.png")),
                ]:
                    if stop.is_set():
                        return
                    relative = path.relative_to(directory).as_posix()
                    if objects.enabled():
                        await _scratch_io(
                            objects.put, path, prefix + "/" + relative
                        )
                    else:
                        target = jobs.DATA_DIR / prefix / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        await _scratch_io(shutil.copyfile, path, target)
                await asyncio.to_thread(store.publish_fix,
                    task,
                    owner,
                    {
                        "state": "done",
                        "report": report,
                        "upgrades": upgrades,
                        "counts": {
                            k: sum(r["status"] == k for r in report.values())
                            for k in ("applied", "manual", "failed")
                        },
                        "artifact_prefix": prefix,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            finally:
                stop.set()
                if render:
                    await asyncio.gather(render, return_exceptions=True)
    finally:
        stop.set()
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)


async def consume() -> None:
    owner = uuid.uuid4().hex
    while True:
        try:
            job = await asyncio.to_thread(jobs.state().claim, owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Queue temporarily unavailable; retrying in five seconds")
            await asyncio.sleep(5)
            continue
        if job is None:
            await asyncio.sleep(1)
            continue
        try:
            info = await asyncio.to_thread(jobs.state().get, "tasks/" + job)
            if info and info.get("kind") == "fix":
                await run_fix(job, owner)
            else:
                await run_job(job, owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Worker failed for review %s", job)
            try:
                await asyncio.to_thread(jobs.state().fail_task, job, owner)
            except Exception:
                # The lease expires and another worker recovers the task after
                # database access returns. A transient outage must not kill consumers.
                log.warning("Failure state unavailable; task left for lease recovery")


async def serve() -> None:
    tasks = [asyncio.create_task(consume()) for _ in range(jobs.MAX_CONCURRENT_JOBS)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    if not jobs.durable():
        raise SystemExit("Set REDLINE_DURABLE_JOBS=1 for a queue worker")
    asyncio.run(serve())
