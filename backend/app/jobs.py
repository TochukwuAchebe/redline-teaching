"""Deck jobs: upload storage, background render + analysis, decisions. State lives on disk per deck."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import uuid
import hashlib
import fcntl
import tempfile
from contextlib import contextmanager
from weakref import WeakKeyDictionary
from pathlib import Path
from functools import lru_cache
from app import objects
from app.state import State

from app.pipeline import analyze
from app import storage
from app.render import render_slides
from app.fix import apply_fixes
from app.model.extract import extract_deck
from app.model.types import Finding

log = logging.getLogger("redline.jobs")
DATA_DIR = Path(
    os.environ.get("DATA_DIR", Path(__file__).resolve().parents[2] / "data")
)

MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
if not 1 <= MAX_CONCURRENT_JOBS <= 4:
    raise ValueError("MAX_CONCURRENT_JOBS must be between 1 and 4")
_slots_by_loop: WeakKeyDictionary = WeakKeyDictionary()


def durable() -> bool:
    return os.environ.get("REDLINE_DURABLE_JOBS", "").lower() in {"1", "true"}


@lru_cache(maxsize=8)
def _state(root: str, database_url: str, workspace: str = "") -> State:
    return State(Path(root))


def state() -> State:
    return _state(str(DATA_DIR), os.environ.get("DATABASE_URL", ""), os.environ.get("REDLINE_WORKSPACE", ""))


def _key(path: Path) -> str:
    return path.relative_to(DATA_DIR).as_posix()


def _job_slots() -> asyncio.Semaphore:
    # A loop-local semaphore also supports separate TestClient lifetimes without
    # reusing an asyncio primitive bound to a closed loop. Uvicorn uses one loop.
    loop = asyncio.get_running_loop()
    if loop not in _slots_by_loop:
        _slots_by_loop[loop] = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    return _slots_by_loop[loop]


def deck_dir(deck_id: str) -> Path:
    if not deck_id.isalnum():
        raise ValueError("bad deck id")
    return DATA_DIR / deck_id


def _persist(*paths: Path) -> None:
    """Mirror written files to durable storage; a storage outage is logged, not fatal."""
    store = None if durable() else storage.mirror(DATA_DIR)
    if store is None:
        return
    try:
        for path in paths:
            store.upload_dir(path) if path.is_dir() else store.upload(path)
    except Exception:
        log.exception("could not persist %s to blob storage", [str(p) for p in paths])


def _restore(deck_id: str, meta_only: bool = False) -> None:
    store = None if durable() else storage.mirror(DATA_DIR)
    if store is None:
        return
    try:
        store.restore(deck_id, meta_only=meta_only)
    except Exception:
        log.exception("could not restore %s from blob storage", deck_id)


def _write(path: Path, data) -> None:
    if durable():
        state().put(_key(path), data)
        return
    # Each writer owns its temporary file; a shared decisions.tmp races even
    # when replacement is atomic. Keep the temp on the same filesystem.
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
    ) as handle:
        tmp = Path(handle.name)
        try:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
        _persist(path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _decision_lock(directory: Path):
    # flock serializes read/modify/write across both request threads and worker
    # processes. The deployed runtime is Linux; local macOS supports it too.
    with (directory / ".decisions.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read(path: Path, default=None):
    if durable():
        value = state().get(_key(path))
        if value is not None:
            return value
        if path.exists():
            value = json.loads(path.read_text())
            state().put(_key(path), value)
            return value
        return default
    return json.loads(path.read_text()) if path.exists() else default


def set_status(deck_id: str, /, **fields) -> None:
    path = deck_dir(deck_id) / "status.json"
    if durable():
        state().mutate(
            _key(path),
            lambda old: old if old.get("status") == "cancelled" else {**old, **fields},
        )
        return
    _write(path, {**_read(path, {}), **fields})


def create_deck(
    filename: str,
    content: bytes,
    upload_token: str | None = None,
    guideline: dict | None = None,
) -> str:
    deck_id = uuid.uuid4().hex[:16]
    d = deck_dir(deck_id)
    d.mkdir(parents=True)
    (d / "deck.pptx").write_bytes(content)
    if durable():
        objects.put(d / "deck.pptx", deck_id + "/deck.pptx")
    else:
        _persist(d / "deck.pptx")
    from app.guidelines import builtin_snapshot
    from app.version import engine_version

    guideline = guideline or builtin_snapshot()
    if durable():
        metadata = dict(
            deck_id=deck_id,
            filename=filename,
            status="queued",
            upload_token=upload_token,
            content_sha256=hashlib.sha256(content).hexdigest(),
            created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            guideline={k: guideline[k] for k in ("id", "name", "version")},
            engine_version=engine_version(),
        )
        try:
            registered = state().register(deck_id, metadata, guideline)
        except ValueError:
            objects.discard_unregistered(deck_id + "/deck.pptx")
            (d / "deck.pptx").unlink(missing_ok=True)
            raise
        if registered != deck_id:
            objects.discard_unregistered(deck_id + "/deck.pptx")
        if objects.enabled() or registered != deck_id:
            (d / "deck.pptx").unlink(missing_ok=True)
            d.rmdir()
        return registered
    _write(d / "guideline.json", guideline)
    cancelled = (
        state().get("cancel_tokens/" + upload_token)
        if durable() and upload_token
        else upload_token in _cancelled_tokens
        if upload_token
        else False
    )
    born = "cancelled" if cancelled else "queued"
    set_status(
        deck_id,
        deck_id=deck_id,
        filename=filename,
        status=born,
        upload_token=upload_token,
        created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        guideline={k: guideline[k] for k in ("id", "name", "version")}
        if guideline
        else {"id": "acme", "name": "ACME", "version": "legacy"},
        engine_version=engine_version(),
    )
    return deck_id


def recheck(deck_id: str) -> str:
    """Review a stored deck again with the current rules, as a new review; decisions on the old one are kept."""
    source = deck_dir(deck_id)
    if not durable() and not (source / "deck.pptx").exists():
        _restore(deck_id)
    filename = _read(source / "status.json", {}).get("filename", "deck.pptx")
    if objects.enabled():
        with tempfile.TemporaryDirectory(prefix="redline-recheck-") as temp:
            original = objects.fetch(Path(temp) / "deck.pptx", deck_id + "/deck.pptx")
            return create_deck(
                filename,
                original.read_bytes(),
                guideline=_read(source / "guideline.json"),
            )
    return create_deck(
        filename,
        (source / "deck.pptx").read_bytes(),
        guideline=_read(source / "guideline.json"),
    )


def list_decks(limit: int = 100, offset: int = 0, query: str = "") -> list[dict]:
    """Every review on this server, newest first, with enough to choose one without opening it."""
    rows = []
    store = None if durable() else storage.mirror(DATA_DIR)
    if store is not None:
        try:  # reviews stored durably but not yet on this replica's disk
            for deck_id in store.deck_ids() - {p.name for p in DATA_DIR.glob("*")}:
                _restore(deck_id, meta_only=True)
        except Exception:
            log.exception("could not list reviews in blob storage")
    if not durable() and not DATA_DIR.is_dir():
        return rows
    directories = (
        [DATA_DIR / key for key in state().history(limit, offset, query)[0]]
        if durable()
        else DATA_DIR.iterdir()
    )
    for d in directories:
        status = _read(d / "status.json") if d.name.isalnum() else None
        if not status or (not durable() and store is None and not (d / "deck.pptx").exists()):
            continue
        result = _read(d / "result.json") or {}
        findings = result.get("findings", [])
        decisions = _read(d / "decisions.json", {})
        units: dict[str, list[str]] = {}
        for f in findings:
            units.setdefault(f.get("group_id") or f["id"], []).append(f["id"])
        created = status.get("created_at") or ""
        if not created and not durable():
            created = datetime.fromtimestamp(
                (d / "status.json").stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds")
        rows.append(
            {
                "deck_id": status.get("deck_id", d.name),
                "filename": status.get("filename", "deck.pptx"),
                "status": status.get("status"),
                "created_at": created,
                "slide_count": result.get("slide_count") or status.get("slide_count"),
                "stats": result.get("stats", {}),
                "decisions_total": len(units),
                "decisions_made": sum(
                    1 for ids in units.values() if all(i in decisions for i in ids)
                ),
            }
        )
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    if durable():
        return rows
    rows = [r for r in rows if query.casefold() in r["filename"].casefold()]
    return rows[offset : offset + limit]


ACTIVE = ("queued", "rendering", "analysing")


def is_cancelled(deck_id: str) -> bool:
    return _read(deck_dir(deck_id) / "status.json", {}).get("status") == "cancelled"


# Tokens cancelled before their upload finished saving; such an upload is born cancelled.
_cancelled_tokens: set[str] = set()


def cancel_by_token(token: str) -> bool:
    """Cancel the job an aborted upload may still have started, before its ID reached the browser."""
    if not token:
        return False
    if durable():
        job = state().cancel_token(token)
        return state().cancel_job(job) if job else False
    else:
        _cancelled_tokens.add(token)
    if not DATA_DIR.is_dir():
        return False
    directories = (
        [DATA_DIR / key.split("/")[0] for key in state().keys("/status.json")]
        if durable()
        else DATA_DIR.iterdir()
    )
    for d in directories:
        status = _read(d / "status.json") if d.name.isalnum() else None
        if status and status.get("upload_token") == token:
            return cancel(d.name)
    return False


def cancel(deck_id: str) -> bool:
    """Stop a queued or running job. The reviewer's choice wins over late completion."""
    if durable():
        return state().cancel_job(deck_id)
    status = _read(deck_dir(deck_id) / "status.json", {}).get("status")
    if status not in ACTIVE:
        return False
    set_status(deck_id, status="cancelled")
    return True


async def process(deck_id: str) -> None:
    # Accepted uploads remain queued until capacity is available. Only an active
    # worker may spawn LibreOffice and analysis, keeping resource use bounded.
    async with _job_slots():
        if is_cancelled(deck_id):
            return
        await _process_active(deck_id)


async def _process_active(deck_id: str) -> None:
    d = deck_dir(deck_id)
    pptx = d / "deck.pptx"
    set_status(deck_id, status="rendering")
    # Rendering and analysis run side by side; the UI shows rendering until slides exist.
    render = asyncio.create_task(asyncio.to_thread(render_slides, pptx, d / "slides"))
    guideline = _read(d / "guideline.json")
    options = {"guideline": guideline} if guideline else {}
    analysis = asyncio.create_task(analyze(str(pptx), deck_id=deck_id, **options))
    try:
        slide_count = await render
        await asyncio.to_thread(_persist, d / "slides")
        if is_cancelled(deck_id):
            analysis.cancel()
            return
        set_status(deck_id, status="analysing", slide_count=slide_count)
        result = await analysis
        if is_cancelled(deck_id):
            return
        result.filename = _read(d / "status.json", {}).get("filename", result.filename)
        _write(d / "result.json", result.model_dump())
        set_status(deck_id, status="done")
    except Exception as exc:  # surfaced to the UI; details stay in the log
        for t in (render, analysis):
            t.cancel()
        if is_cancelled(deck_id):
            return
        log.exception("deck %s failed", deck_id)
        set_status(deck_id, status="error", error=_friendly(exc))


def _friendly(exc: Exception) -> str:
    if isinstance(exc, NotImplementedError):
        return "The analysis engine is not available yet."
    if "soffice" in str(exc) or "LibreOffice" in str(exc):
        return "Slides could not be rendered."
    return (
        "This file could not be analysed. Check it opens in PowerPoint and try again."
    )


def get_deck(deck_id: str) -> dict | None:
    d = deck_dir(deck_id)
    if not durable() and not (d / "deck.pptx").exists():
        _restore(
            deck_id
        )  # not on this replica's disk: bring the whole review back from durable storage
    status = _read(d / "status.json")
    if status is None:
        return None
    slides = d / "slides"
    total = slides / "total.txt"
    progress = {
        "rendered": status.get(
            "rendered", sum(1 for p in slides.glob("*.png")) if slides.is_dir() else 0
        ),
        "slides_total": status.get("slide_count")
        or (int(total.read_text()) if total.exists() else None),
    }
    return {
        **status,
        **progress,
        "result": _read(d / "result.json"),
        "decisions": _read(d / "decisions.json", {}),
        "fix": _read(d / "fix.json"),
    }


# Fix: change it in the corrected copy. To-do: the reviewer changes it. Dismiss: keep as is.
DECISIONS = ("accepted", "todo", "rejected")


def update_decisions(deck_id: str, changes: dict[str, str | None]) -> dict:
    directory = deck_dir(deck_id)
    path = directory / "decisions.json"
    if durable():

        def patch(decisions):
            for fid, value in changes.items():
                if value in DECISIONS:
                    decisions[fid] = value
                else:
                    decisions.pop(fid, None)
            return decisions

        return state().mutate(_key(path), patch)
    with _decision_lock(directory):
        decisions = _read(path, {})
        for fid, value in changes.items():
            if value in DECISIONS:
                decisions[fid] = value
            else:
                decisions.pop(fid, None)
        _write(path, decisions)
    return decisions


# --- Applying accepted fixes ------------------------------------------------------------


def fix_state(deck_id: str) -> dict | None:
    return _read(deck_dir(deck_id) / "fix.json")


def start_fixes(deck_id: str) -> bool:
    """Atomically queue one correction run from the current accepted findings."""
    if durable():
        return state().start_fix(deck_id)
    current = fix_state(deck_id) or {}
    if current.get("state") == "running":
        return False
    _write(deck_dir(deck_id) / "fix.json", {**current, "state": "running"})
    return True


def _apply_and_verify(deck_id: str) -> dict:
    d = deck_dir(deck_id)
    result = _read(d / "result.json") or {}
    decisions = _read(d / "decisions.json", {})
    accepted = [
        Finding(**f)
        for f in result.get("findings", [])
        if decisions.get(f["id"]) == "accepted"
    ]
    original = d / "deck.pptx"
    fixed = d / "fixed.pptx"
    guideline = _read(d / "guideline.json")
    if accepted:
        report = apply_fixes(original, fixed, extract_deck(str(original)), accepted, config=(guideline or {}).get("config"))
    else:
        import shutil

        report = {}
        shutil.copyfile(original, fixed)
    from app import redesign

    with tempfile.TemporaryDirectory(prefix="redline-upgrades-") as scratch:
        upgrades = redesign.apply_chosen(fixed, chosen_upgrades((_read(_upgrades_path(deck_id), {}) or {}).get("items")), Path(scratch))
    # Verify by checking the corrected deck again: an applied fix only counts once its flag is gone.
    from app.pipeline import analyze_sync

    remaining = {
        f.id for f in analyze_sync(str(fixed), deck_id=deck_id, use_ai=False, guideline=guideline).findings
    }
    for f in accepted:
        row = report[f.id]
        if row["status"] == "applied":
            row["verified"] = None if f.origin == "ai" else f.id not in remaining
            if row["verified"] is False:
                row["status"], row["note"] = (
                    "manual",
                    row["note"]
                    + " The re-check still sees this issue; please check it by hand.",
                )
    slides = d / "fixed_slides"
    if slides.exists():
        for old in slides.glob("*"):
            old.unlink()
    render_slides(fixed, slides)
    _persist(fixed, slides)
    counts = {
        k: sum(1 for r in report.values() if r["status"] == k)
        for k in ("applied", "manual", "failed")
    }
    version = (fix_state(deck_id) or {}).get("version", 0) + 1
    return {
        "state": "done",
        "report": report,
        "upgrades": upgrades,
        "counts": counts,
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


async def run_fixes(deck_id: str) -> None:
    try:
        state = await asyncio.to_thread(_apply_and_verify, deck_id)
    except Exception:  # surfaced to the UI; details stay in the log
        log.exception("fixes for %s failed", deck_id)
        state = {
            **(fix_state(deck_id) or {}),
            "state": "error",
            "error": "The fixes could not be applied to this deck.",
        }
    _write(deck_dir(deck_id) / "fix.json", state)


# --- Design upgrades -----------------------------------------------------------------------
# One document per review: which slides can be redesigned, each proposal, and the reviewer's
# choice. Proposals are optional; applying fixes also applies the ones marked "use".

def _upgrades_path(deck_id: str) -> Path:
    return deck_dir(deck_id) / "upgrades.json"


def _local_deck(deck_id: str) -> Path:
    path = deck_dir(deck_id) / "deck.pptx"
    if not path.exists() and objects.enabled():
        path.parent.mkdir(parents=True, exist_ok=True)
        objects.fetch(path, deck_id + "/deck.pptx")
    return path


def _mutate_upgrades(deck_id: str, change) -> dict:
    path = _upgrades_path(deck_id)
    if durable():
        return state().mutate(_key(path), lambda doc: change(doc or {}))
    with _decision_lock(deck_dir(deck_id)):
        doc = change(_read(path, {}) or {})
        _write(path, doc)
    return doc


def list_upgrades(deck_id: str) -> dict:
    from app import redesign

    doc = _read(_upgrades_path(deck_id), {}) or {}
    if doc.get("engine") != redesign.ENGINE:
        # Candidates and suggestions from an earlier engine are recomputed; anything no longer
        # offered, or made by the earlier chart-only engine, is retired and can be asked for again.
        found = redesign.candidates(extract_deck(str(_local_deck(deck_id))))
        slides = {str(c["slide"]) for c in found}
        doc = _mutate_upgrades(deck_id, lambda d: {**d, "engine": redesign.ENGINE, "candidates": found, "items": {
            k: v for k, v in d.get("items", {}).items() if k in slides and not _stale(v)}})
    return {"candidates": doc.get("candidates", []), "items": doc.get("items", {})}


def generate_upgrade(deck_id: str, slide: int) -> dict:
    from app import redesign

    source = _local_deck(deck_id)
    deck = extract_deck(str(source))
    out = deck_dir(deck_id) / "upgrades"
    try:
        proposal = redesign.propose(deck, slide)
        proposal.update(redesign.preview(source, deck, proposal, out, render_slides))
        for name in (f"{slide}.png", f"{slide}-appendix.png"):
            if not (out / name).exists():  # text-only redesigns have no appendix slide
                continue
            if objects.enabled():
                objects.put(out / name, f"{deck_id}/upgrades/{name}")
            else:
                _persist(out / name)
        item = {"state": "ready", "proposal": proposal, "choice": None}
    except redesign.NotUpgradable as exc:
        item = {"state": "unavailable", "note": str(exc)}
    _mutate_upgrades(deck_id, lambda d: {**d, "items": {**d.get("items", {}), str(slide): item}})
    return item


def choose_upgrade(deck_id: str, slide: int, choice: str | None) -> dict:
    def change(doc):
        items = dict(doc.get("items", {}))
        current = items.get(str(slide))
        if not current or current.get("state") != "ready":
            raise ValueError("There is no design suggestion for this slide yet.")
        items[str(slide)] = {**current, "choice": choice}
        return {**doc, "items": items}

    return _mutate_upgrades(deck_id, change)["items"][str(slide)]


def _stale(item: dict) -> bool:
    """A suggestion made by an earlier engine, which let the agent write its own text."""
    from app import redesign

    return bool(item.get("proposal")) and item["proposal"].get("engine") != redesign.ENGINE


def chosen_upgrades(items: dict) -> dict:
    return {k: v for k, v in (items or {}).items() if v.get("choice") == "use" and v.get("proposal") and not _stale(v)}
