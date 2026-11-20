"""HTTP API and static UI."""

import asyncio
import csv
import io
import os
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import jobs
from app.auth import COOKIE, MAX_AGE, auth_middleware, issue_token, login_blocked, password_ok, record_failure
from app.guidelines import ProfileDraft, snapshot

MAX_UPLOAD = 50 * 1024 * 1024


@asynccontextmanager
async def lifespan(app):
    worker = None
    if jobs.durable():
        if len(os.environ.get("SESSION_SECRET", "")) < 32:
            raise RuntimeError(
                "Durable deployments require a stable SESSION_SECRET of at least 32 characters"
            )
        store = await asyncio.to_thread(jobs.state)
        await asyncio.to_thread(store.put, "system/live", True)
    if jobs.durable() and os.environ.get("REDLINE_RUN_WORKERS", "1") == "1":
        from app.worker import serve

        worker = asyncio.create_task(serve())
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title="Redline", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
from app.limits import RequestLimits
app.add_middleware(RequestLimits)
app.middleware("http")(auth_middleware)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "same-origin",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    # TLS ends at the ingress, so the forwarded scheme says whether the browser used HTTPS.
    if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response
_tasks: set[asyncio.Task] = set()


class Login(BaseModel):
    password: str


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/session")
def session():
    return {"ok": True}  # reaching here means the auth middleware accepted the cookie


@app.post("/api/login")
def login(body: Login, request: Request):
    import time

    # Behind the managed ingress (Envoy) the client address arrives in a header, sometimes with a port.
    raw = (request.headers.get("x-envoy-external-address") or request.headers.get("x-forwarded-for")
           or (request.client.host if request.client else ""))
    client = raw.split(",")[0].strip()
    if client.count(":") == 1:  # "203.0.113.7:51234" -> "203.0.113.7"; IPv6 keeps its colons
        client = client.split(":")[0]
    now = time.monotonic()
    if login_blocked(client, now):
        raise HTTPException(429, "Too many sign-in attempts. Wait a few minutes, then try again.", headers={"Retry-After": "300"})
    if not password_ok(body.password):
        record_failure(client, now)
        raise HTTPException(401, "Incorrect password")
    resp = JSONResponse({"ok": True})
    # Managed HTTPS ingress can forward HTTP to the container. Production must
    # require secure cookies independently of the proxy's internal URL scheme.
    secure_cookie = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
    resp.set_cookie(
        COOKIE,
        issue_token(),
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure_cookie or request.url.scheme == "https",
    )
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/decks")
def list_decks(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    query: Annotated[str, Query(max_length=100)] = "",
):
    rows = jobs.list_decks(limit, offset, query)
    # Fetch one extra row rather than claim this page is the complete history.
    more = bool(jobs.list_decks(1, offset + limit, query))
    total = (
        jobs.state().history(1, 0, query)[1]
        if jobs.durable()
        else len(jobs.list_decks(1000000, 0, query))
    )
    return {
        "decks": rows,
        "total": total,
        "next_offset": offset + limit if more else None,
    }


@app.post("/api/decks")
async def upload(
    file: UploadFile,
    upload_token: Annotated[str | None, Form()] = None,
    guideline_id: Annotated[str, Form()] = "acme",
):
    guideline = None
    if guideline_id != "acme":
        if not re.fullmatch(r"[a-f0-9]{32}", guideline_id):
            raise HTTPException(400, "Choose a saved guideline")
        guideline = await asyncio.to_thread(jobs._read, jobs.DATA_DIR / "guidelines" / (guideline_id + ".json"))
        if guideline is None:
            raise HTTPException(404, "Guideline not found")
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(400, "Please upload a .pptx file")
    # Bound every read, including the one-byte overflow probe. The multipart
    # parser may spool to disk, but reading it wholesale would defeat our limit.
    content = bytearray()
    try:
        while chunk := await file.read(min(1024 * 1024, MAX_UPLOAD - len(content) + 1)):
            content.extend(chunk)
            if len(content) > MAX_UPLOAD:
                raise HTTPException(413, "File is larger than 50 MB")
    finally:
        await file.close()
    payload = bytes(content)
    del content
    _check_pptx(payload)
    try:
        deck_id = await asyncio.to_thread(
            jobs.create_deck,
            Path(file.filename).name,
            payload,
            _token(upload_token),
            guideline,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not jobs.durable():
        task = asyncio.create_task(jobs.process(deck_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {"deck_id": deck_id}


@app.get("/api/guidelines")
def guidelines():
    directory = jobs.DATA_DIR / "guidelines"
    if jobs.durable():
        profiles = [
            jobs.state().get(k)
            for k in jobs.state().keys(".json")
            if k.startswith("guidelines/")
        ]
    else:
        profiles = (
            [jobs._read(p) for p in directory.glob("*.json")]
            if directory.exists()
            else []
        )
    return {"guidelines": sorted(profiles, key=lambda p: p["created_at"], reverse=True)}


@app.post("/api/guidelines")
def save_guideline(body: ProfileDraft):
    profile = snapshot(body)
    directory = jobs.DATA_DIR / "guidelines"
    directory.mkdir(parents=True, exist_ok=True)
    jobs._write(directory / (profile["id"] + ".json"), profile)
    return profile


SLIDE_PART = re.compile(r"ppt/slides/slide\d+\.xml")


def _check_pptx(content: bytes) -> None:
    """Reject files that cannot be a reviewable deck before any work is queued."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = z.namelist()
            parts = z.infolist()
            if (
                len(parts) > 20000
                or sum(p.file_size for p in parts) > 128 * 1024 * 1024
            ):
                raise HTTPException(
                    413,
                    "The expanded deck is too large to review. Split it into smaller decks.",
                )
            if len(names) != len(set(names)):
                raise HTTPException(
                    400,
                    "This deck contains duplicate archive entries. Save a fresh .pptx in PowerPoint.",
                )
            if sum(bool(SLIDE_PART.fullmatch(n)) for n in names) > 500:
                raise HTTPException(
                    413,
                    "A review supports up to 500 slides. Split this presentation into smaller decks.",
                )
    except zipfile.BadZipFile:
        # Covers .ppt, password-protected decks, Office lock files and truncated uploads.
        raise HTTPException(
            400,
            "This isn't a readable .pptx file. If it's password-protected or "
            "an older .ppt, open it in PowerPoint, save it as .pptx and try again.",
        )
    if "ppt/presentation.xml" not in names:
        raise HTTPException(400, "This file isn't a PowerPoint presentation.")
    if not any(SLIDE_PART.fullmatch(n) for n in names):
        raise HTTPException(400, "This deck has no slides to review.")


def _deck_or_404(deck_id: str) -> dict:
    try:
        deck = jobs.get_deck(deck_id)
    except ValueError:
        deck = None
    if deck is None:
        raise HTTPException(404, "Deck not found")
    return deck


@app.get("/api/decks/{deck_id}")
def get_deck(deck_id: str):
    return _deck_or_404(deck_id)


@app.post("/api/decks/{deck_id}/recheck")
async def recheck_deck(deck_id: str):
    await asyncio.to_thread(_deck_or_404, deck_id)
    new_id = await asyncio.to_thread(jobs.recheck, deck_id)
    if not jobs.durable():
        task = asyncio.create_task(jobs.process(new_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {"deck_id": new_id}


def _token(value: str | None) -> str | None:
    return value if value and re.fullmatch(r"[A-Za-z0-9-]{8,64}", value) else None


@app.post("/api/uploads/{token}/cancel")
def cancel_upload(token: str):
    return {"cancelled": bool(_token(token)) and jobs.cancel_by_token(token)}


@app.post("/api/decks/{deck_id}/fixes")
async def apply_fixes(deck_id: str):
    deck = await asyncio.to_thread(_deck_or_404, deck_id)
    if deck.get("status") != "done":
        raise HTTPException(409, "This deck is still being checked.")
    if not any(v == "accepted" for v in deck["decisions"].values()) and not jobs.chosen_upgrades(
        (await asyncio.to_thread(jobs.list_upgrades, deck_id))["items"]
    ):
        raise HTTPException(400, "Choose at least one fix or redesign before applying.")
    try:
        started = await asyncio.to_thread(jobs.start_fixes, deck_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if started and not jobs.durable():
        task = asyncio.create_task(jobs.run_fixes(deck_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {"state": "running"}


def _artifact_prefix(deck_id: str, deck: dict) -> str | None:
    """Where a review's corrected files live in durable storage.

    Migrated reviews kept their corrected files under the review's own id but
    did not record a prefix; fall back to that layout in durable mode.
    """
    prefix = (deck.get("fix") or {}).get("artifact_prefix")
    if prefix:
        return prefix
    from app import objects

    return deck_id if jobs.durable() and objects.enabled() else None


@app.get("/api/decks/{deck_id}/fixed-slides/{n}.png")
def fixed_slide_png(deck_id: str, n: int):
    deck = _deck_or_404(deck_id)
    if n < 1 or n > 500 or (deck.get("fix") or {}).get("state") != "done":
        raise HTTPException(404, "Corrected slide not available")
    prefix = _artifact_prefix(deck_id, deck)
    if prefix:
        return _fixed_artifact(prefix + f"/fixed_slides/{n}.png", "image/png")
    path = jobs.deck_dir(deck_id) / "fixed_slides" / f"{n}.png"
    if not path.exists():
        raise HTTPException(404, "Corrected slide not available")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@app.get("/api/decks/{deck_id}/fixed.pptx")
def fixed_pptx(deck_id: str):
    deck = _deck_or_404(deck_id)
    name = Path(deck["filename"]).stem + " - corrected.pptx"
    prefix = _artifact_prefix(deck_id, deck)
    if (deck.get("fix") or {}).get("state") == "done" and prefix:
        return _fixed_artifact(
            prefix + "/fixed.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            name,
        )
    path = jobs.deck_dir(deck_id) / "fixed.pptx"
    if not path.exists():
        raise HTTPException(404, "No corrected deck yet")
    name = Path(deck["filename"]).stem + " - corrected.pptx"
    return FileResponse(
        path,
        filename=name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


def _fixed_artifact(key: str, media_type: str, filename: str | None = None):
    from app import objects

    headers = {"Cache-Control": "no-store"}
    if filename:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(
            filename, safe=""
        )
    if objects.enabled() and jobs.durable():
        from azure.core.exceptions import ResourceNotFoundError

        try:
            content = objects.stream(key)
        except ResourceNotFoundError:
            raise HTTPException(404, "Corrected file not available")
        return StreamingResponse(content, media_type=media_type, headers=headers)
    path = jobs.DATA_DIR / key
    if not path.exists():
        raise HTTPException(404, "Corrected file not available")
    return FileResponse(path, media_type=media_type, headers=headers)


@app.post("/api/decks/{deck_id}/cancel")
def cancel_deck(deck_id: str):
    _deck_or_404(deck_id)
    return {"cancelled": jobs.cancel(deck_id)}


@app.get("/api/decks/{deck_id}/slides/{n}.png")
def slide_png(deck_id: str, n: int):
    deck = _deck_or_404(deck_id)
    if n < 1 or n > 500:
        raise HTTPException(404, "Slide not found")
    path = jobs.deck_dir(deck_id) / "slides" / f"{n}.png"
    from app import objects

    if objects.enabled():
        from azure.core.exceptions import ResourceNotFoundError

        try:
            content = objects.stream(deck_id + f"/slides/{n}.png")
        except ResourceNotFoundError:
            raise HTTPException(404, "Slide not rendered")
        return StreamingResponse(
            content,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    if not path.exists():
        raise HTTPException(404, "Slide not rendered")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/decks/{deck_id}/upgrades")
def list_upgrades(deck_id: str):
    if _deck_or_404(deck_id).get("status") != "done":
        raise HTTPException(409, "This deck is still being checked.")
    return jobs.list_upgrades(deck_id)


@app.post("/api/decks/{deck_id}/upgrades/{n}")
async def generate_upgrade(deck_id: str, n: int):
    upgrades = await asyncio.to_thread(list_upgrades, deck_id)
    if not any(c["slide"] == n for c in upgrades["candidates"]):
        raise HTTPException(404, "This slide has no design suggestion.")
    return await asyncio.to_thread(jobs.generate_upgrade, deck_id, n)


class UpgradeChoice(BaseModel):
    choice: Literal["use"] | None


@app.put("/api/decks/{deck_id}/upgrades/{n}")
def choose_upgrade(deck_id: str, n: int, body: UpgradeChoice):
    _deck_or_404(deck_id)
    try:
        return jobs.choose_upgrade(deck_id, n, body.choice)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/decks/{deck_id}/upgrades/{name}.png")
def upgrade_png(deck_id: str, name: str):
    _deck_or_404(deck_id)
    if not re.fullmatch(r"\d{1,3}(-appendix)?", name):
        raise HTTPException(404, "Preview not found")
    from app import objects

    if objects.enabled():
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return StreamingResponse(objects.stream(f"{deck_id}/upgrades/{name}.png"), media_type="image/png",
                                     headers={"Cache-Control": "no-store"})
        except ResourceNotFoundError:
            raise HTTPException(404, "Preview not found")
    path = jobs.deck_dir(deck_id) / "upgrades" / f"{name}.png"
    if not path.exists():
        raise HTTPException(404, "Preview not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.put("/api/decks/{deck_id}/decisions")
def put_decisions(
    deck_id: str, changes: dict[str, Literal["accepted", "todo", "rejected"] | None]
):
    _deck_or_404(deck_id)
    return jobs.update_decisions(deck_id, changes)


@app.get("/api/decks/{deck_id}/export.csv")
def export_csv(deck_id: str):
    deck = _deck_or_404(deck_id)
    findings = (deck.get("result") or {}).get("findings", [])
    # The report is the reviewer's work list: fixes Redline makes, then to-dos they make.
    chosen = {"accepted": "Fix", "todo": "To-do"}
    accepted = [f for f in findings if deck["decisions"].get(f["id"]) in chosen]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "Slide",
            "Severity",
            "Issue",
            "Details",
            "Expected",
            "Actual",
            "Guideline",
            "Suggested fix",
            "Decision",
        ]
    )
    for f in accepted:
        manual_list = f.get("origin") == "ai" and f.get("rule_id") == "BULLET_PARALLEL"
        w.writerow(
            [
                _csv_cell(value)
                for value in [
                    f["slide"],
                    f["severity"],
                    f["title"],
                    f["message"],
                    "Review bullet structure manually" if manual_list else f.get("expected") or "",
                    f.get("actual") or "",
                    f.get("rule_ref") or "",
                    "" if manual_list else f.get("suggested_fix") or "",
                    "Fix" if deck["decisions"][f["id"]] == "accepted" and (f.get("fix") or {}).get("auto") else "To-do",
                ]
            ]
        )
    name = Path(deck["filename"]).stem + " - review.csv"
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(name, safe="")
        },
    )


def _csv_cell(value: str | int) -> str | int:
    """Keep uploaded/provider text literal when opened in spreadsheet software."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


# Built UI: /app/static in the container, frontend/dist locally.
_static = Path(
    os.environ.get(
        "STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
)
if _static.is_dir():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = _static / path
        if path and candidate.is_file() and _static in candidate.resolve().parents:
            return FileResponse(candidate)
        # Always revalidate the shell so a new release is picked up immediately;
        # hashed files under /assets can be cached as usual.
        return FileResponse(_static / "index.html", headers={"Cache-Control": "no-cache"})
