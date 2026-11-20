"""Transactional metadata and leased job queue: SQLite locally, PostgreSQL in Azure.

No credentials or document text are logged. Each operation owns its connection;
queue claims and decision patches are transactions, not read/replace races.
"""

from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterator
from functools import lru_cache
import re

_TABLES = re.compile(r"\b(documents|queue|reviews|uploads|queue_claim|reviews_order)\b")


def _workspace_prefix() -> str:
    """Optional workspace name: a separate set of tables in the same database.

    Lets one deployment start a clean review store (for example for a hand-over)
    without new database credentials, while earlier reviews stay untouched.
    """
    name = os.environ.get("REDLINE_WORKSPACE", "").strip()
    if not name:
        return ""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,29}", name):
        raise ValueError("REDLINE_WORKSPACE must be lower-case letters, digits or underscores")
    return name + "_"


class _Cursor:
    """Routes every statement through the workspace's table names."""

    def __init__(self, cursor: Any, prefix: str):
        self._cursor = cursor
        self._prefix = prefix

    def execute(self, sql: str, *args: Any) -> "_Cursor":
        self._cursor.execute(_TABLES.sub(lambda m: self._prefix + m.group(1), sql), *args)
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


@lru_cache(maxsize=8)
def _pool(url: str) -> Any:
    from psycopg_pool import ConnectionPool

    maximum = int(os.environ.get("MAX_DB_CONNECTIONS", "4"))
    if not 1 <= maximum <= 16:
        raise ValueError("MAX_DB_CONNECTIONS must be between 1 and 16")
    pool = ConnectionPool(
        url,
        min_size=0,
        max_size=maximum,
        timeout=10,
        max_idle=60,
        num_workers=1,
        open=True,
        kwargs={"connect_timeout": 10, "application_name": "redline"},
    )
    import atexit

    atexit.register(pool.close)
    return pool


class State:
    def __init__(self, root: Path):
        self.root = root
        self.url = os.environ.get("DATABASE_URL", "")
        self.postgres = self.url.startswith(("postgresql://", "postgres://"))
        self.prefix = _workspace_prefix()
        if self.url and not self.postgres:
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL; omit for local SQLite"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        if os.environ.get("AZURE_STORAGE_ACCOUNT_URL") and not self.postgres:
            raise ValueError(
                "Azure objects require PostgreSQL shared metadata; local SQLite is not a multi-replica store"
            )
        with self.transaction() as c:
            if self.postgres:
                c.execute("SELECT pg_advisory_xact_lock(7732400)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS documents (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS queue (id TEXT PRIMARY KEY, state TEXT NOT NULL, owner TEXT, expires DOUBLE PRECISION NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL DEFAULT 0)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS queue_claim ON queue (state, expires)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, filename TEXT NOT NULL, created TEXT NOT NULL)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS reviews_order ON reviews (created,id)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS uploads (token TEXT PRIMARY KEY, job TEXT, cancelled INTEGER NOT NULL DEFAULT 0, expires DOUBLE PRECISION NOT NULL)"
            )

    def cancel_token(self, token: str) -> str | None:
        with self.transaction() as c:
            c.execute(
                self.sql("DELETE FROM uploads WHERE expires<? AND job IS NULL"),
                (time.time(),),
            )
            c.execute(
                self.sql(
                    "INSERT INTO uploads (token,cancelled,expires) VALUES (?,1,?) ON CONFLICT(token) DO UPDATE SET cancelled=1,expires=excluded.expires"
                ),
                (token, time.time() + 86400),
            )
            return c.execute(
                self.sql("SELECT job FROM uploads WHERE token=?"), (token,)
            ).fetchone()[0]

    def cancel_job(self, job: str) -> bool:
        with self.transaction() as c:
            c.execute(
                self.sql("SELECT id FROM queue WHERE id=?")
                + (" FOR UPDATE" if self.postgres else ""),
                (job,),
            ).fetchone()
            old = self._get_in(c, job + "/status.json")
            if old.get("status") not in ("queued", "rendering", "analysing"):
                return False
            self._put(c, job + "/status.json", {**old, "status": "cancelled"})
            c.execute(
                self.sql(
                    "UPDATE queue SET state='finished',owner=NULL,expires=0 WHERE id=?"
                ),
                (job,),
            )
            return True

    def register(self, job: str, status: dict, guideline: dict) -> str:
        """Metadata, upload idempotency and queue insertion commit together."""
        with self.transaction() as c:
            if self.postgres:
                c.execute("SELECT pg_advisory_xact_lock(7732401)")
            token = status.get("upload_token")
            if token:
                c.execute(
                    self.sql(
                        "INSERT INTO uploads (token,expires) VALUES (?,?) ON CONFLICT(token) DO NOTHING"
                    ),
                    (token, time.time() + 86400),
                )
                old = c.execute(
                    self.sql("SELECT job,cancelled,expires FROM uploads WHERE token=?")
                    + (" FOR UPDATE" if self.postgres else ""),
                    (token,),
                ).fetchone()
                if old[0]:
                    previous = self._get_in(c, old[0] + "/status.json")
                    if previous.get("content_sha256") != status.get("content_sha256"):
                        raise ValueError(
                            "This upload token was already used for another file. Please choose the file again."
                        )
                    return old[0]
                if old[1] and old[2] > time.time():
                    status = {**status, "status": "cancelled"}
                c.execute(
                    self.sql("UPDATE uploads SET job=? WHERE token=?"), (job, token)
                )
            maximum = int(os.environ.get("MAX_QUEUED_JOBS", "100"))
            if maximum < 1 or maximum > 10000:
                raise ValueError("MAX_QUEUED_JOBS must be between 1 and 10000")
            queued = c.execute(
                "SELECT count(*) FROM queue WHERE state IN ('pending','running')"
            ).fetchone()[0]
            if status["status"] != "cancelled" and queued >= maximum:
                raise ValueError(
                    "The review queue is full. Please try again after a review finishes."
                )
            self._put(c, job + "/status.json", status)
            self._put(c, job + "/guideline.json", guideline)
            c.execute(
                self.sql(
                    "INSERT INTO queue (id,state,created) VALUES (?, ?,?) ON CONFLICT(id) DO NOTHING"
                ),
                (
                    job,
                    "finished" if status["status"] == "cancelled" else "pending",
                    time.time(),
                ),
            )
            return job

    def start_fix(self, review: str) -> bool:
        import uuid

        key = review + "/fix.json"
        with self.transaction() as c:
            if self.postgres:
                c.execute("SELECT pg_advisory_xact_lock(7732401)")
            c.execute(
                self.sql(
                    "INSERT INTO documents (key,payload) VALUES (?,?) ON CONFLICT(key) DO NOTHING"
                ),
                (key, "{}"),
            )
            old = self._get_in(c, key)
            if old.get("state") == "running":
                return False
            decisions = self._get_in(c, review + "/decisions.json")
            result = self._get_in(c, review + "/result.json")
            accepted = [
                f
                for f in result.get("findings", [])
                if decisions.get(f["id"]) == "accepted"
            ]
            upgrades = {
                k: v for k, v in (self._get_in(c, review + "/upgrades.json").get("items") or {}).items()
                if v.get("choice") == "use" and v.get("proposal")
            }
            if not accepted and not upgrades:
                raise ValueError("Choose a fix or a redesign before applying")
            count = c.execute(
                "SELECT count(*) FROM queue WHERE state IN ('pending','running')"
            ).fetchone()[0]
            if count >= int(os.environ.get("MAX_QUEUED_JOBS", "100")):
                raise ValueError("The review queue is full. Try again shortly.")
            task = "fix-" + uuid.uuid4().hex
            self._put(
                c,
                "tasks/" + task,
                {
                    "kind": "fix",
                    "review": review,
                    "accepted": accepted,
                    "upgrades": upgrades,
                    "guideline": self._get_in(c, review + "/guideline.json"),
                },
            )
            self._put(c, key, {**old, "state": "running", "active_task": task})
            c.execute(
                self.sql("INSERT INTO queue (id,state,created) VALUES (?,'pending',?)"),
                (task, time.time()),
            )
            return True

    def publish_fix(self, task: str, owner: str, result: dict) -> bool:
        with self.transaction() as c:
            lease = c.execute(
                self.sql("SELECT owner,expires FROM queue WHERE id=?")
                + (" FOR UPDATE" if self.postgres else ""),
                (task,),
            ).fetchone()
            info = self._get_in(c, "tasks/" + task)
            key = info["review"] + "/fix.json"
            old = self._get_in(c, key)
            if (
                not lease
                or lease[0] != owner
                or lease[1] <= time.time()
                or old.get("active_task") != task
            ):
                return False
            self._put(
                c,
                key,
                {**result, "version": old.get("version", 0) + 1, "active_task": task},
            )
            c.execute(
                self.sql("UPDATE queue SET state='finished',expires=0 WHERE id=?"),
                (task,),
            )
            return True

    def fail_task(self, task: str, owner: str) -> None:
        info = self.get("tasks/" + task)
        if info and info.get("kind") == "fix":
            self.publish_fix(
                task,
                owner,
                {
                    "state": "error",
                    "error": "The corrected deck could not be completed. Please try again.",
                },
            )
        else:
            self.progress(
                task,
                owner,
                status="error",
                error="This review could not be completed. Check the deck again to retry.",
            )
            self.finish(task, owner)

    def sql(self, text: str) -> str:
        return text.replace("?", "%s") if self.postgres else text

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        if self.postgres:
            with _pool(self.url).connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        yield _Cursor(cursor, self.prefix) if self.prefix else cursor
            return
        conn = sqlite3.connect(self.root / "state.sqlite3", timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.cursor()
            yield _Cursor(cursor, self.prefix) if self.prefix else cursor
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, key: str, default: Any = None) -> Any:
        with self.transaction() as c:
            row = c.execute(
                self.sql("SELECT payload FROM documents WHERE key=?"), (key,)
            ).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        with self.transaction() as c:
            self._put(c, key, value)

    def _put(self, c: Any, key: str, value: Any) -> None:
        c.execute(
            self.sql(
                "INSERT INTO documents (key,payload) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload"
            ),
            (key, json.dumps(value)),
        )
        if key.endswith("/status.json") and key.count("/") == 1:
            c.execute(
                self.sql(
                    "INSERT INTO reviews (id,filename,created) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET filename=excluded.filename,created=excluded.created"
                ),
                (
                    key.split("/")[0],
                    value.get("filename", "deck.pptx"),
                    value.get("created_at", ""),
                ),
            )

    def history(self, limit: int, offset: int, query: str) -> tuple[list[str], int]:
        condition = (
            "POSITION(? IN lower(filename))>0"
            if self.postgres
            else "instr(lower(filename),?)>0"
        )
        with self.transaction() as c:
            total = c.execute(
                self.sql("SELECT count(*) FROM reviews WHERE " + condition),
                (query.casefold(),),
            ).fetchone()[0]
            ids = [
                r[0]
                for r in c.execute(
                    self.sql(
                        "SELECT id FROM reviews WHERE "
                        + condition
                        + " ORDER BY created DESC,id DESC LIMIT ? OFFSET ?"
                    ),
                    (query.casefold(), limit, offset),
                ).fetchall()
            ]
            return ids, total

    def mutate(self, key: str, fn: Callable[[dict], dict]) -> dict:
        with self.transaction() as c:
            c.execute(
                self.sql(
                    "INSERT INTO documents (key,payload) VALUES (?,?) ON CONFLICT(key) DO NOTHING"
                ),
                (key, "{}"),
            )
            row = c.execute(
                self.sql("SELECT payload FROM documents WHERE key=?")
                + (" FOR UPDATE" if self.postgres else ""),
                (key,),
            ).fetchone()
            result = fn(json.loads(row[0]))
            self._put(c, key, result)
            return result

    def keys(self, suffix: str) -> list[str]:
        with self.transaction() as c:
            return [
                r[0]
                for r in c.execute(
                    self.sql("SELECT key FROM documents WHERE key LIKE ? ORDER BY key"),
                    ("%" + suffix,),
                ).fetchall()
            ]

    def enqueue(self, job: str) -> None:
        with self.transaction() as c:
            c.execute(
                self.sql(
                    "INSERT INTO queue (id,state,created) VALUES (?,'pending',?) ON CONFLICT(id) DO NOTHING"
                ),
                (job, time.time()),
            )

    def claim(self, owner: str, lease: float = 60) -> str | None:
        now = time.time()
        with self.transaction() as c:
            row = c.execute(
                self.sql(
                    "SELECT id,attempts FROM queue WHERE state='pending' OR (state='running' AND expires<?) ORDER BY created,id LIMIT 1"
                )
                + (" FOR UPDATE SKIP LOCKED" if self.postgres else ""),
                (now,),
            ).fetchone()
            if not row:
                return None
            job, attempts = row
            if attempts >= 5:
                c.execute(
                    self.sql("UPDATE queue SET state='failed' WHERE id=?"), (job,)
                )
                task = self._get_in(c, "tasks/" + job)
                if task.get("kind") == "fix":
                    key = task["review"] + "/fix.json"
                    old = self._get_in(c, key)
                    if old.get("active_task") == job:
                        self._put(
                            c,
                            key,
                            {
                                **old,
                                "state": "error",
                                "error": "Correction stopped after repeated worker interruptions. Please try again.",
                            },
                        )
                else:
                    old = self._get_in(c, job + "/status.json")
                    if old.get("status") != "cancelled":
                        self._put(
                            c,
                            job + "/status.json",
                            {
                                **old,
                                "status": "error",
                                "error": "Processing stopped after repeated worker interruptions. Check this deck again to retry.",
                            },
                        )
                return None
            c.execute(
                self.sql(
                    "UPDATE queue SET state='running',owner=?,expires=?,attempts=attempts+1 WHERE id=?"
                ),
                (owner, now + lease, job),
            )
            return job

    def _get_in(self, c: Any, key: str) -> dict:
        row = c.execute(
            self.sql("SELECT payload FROM documents WHERE key=?")
            + (" FOR UPDATE" if self.postgres else ""),
            (key,),
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def heartbeat(self, job: str, owner: str, lease: float = 60) -> bool:
        with self.transaction() as c:
            c.execute(
                self.sql(
                    "UPDATE queue SET expires=? WHERE id=? AND owner=? AND state='running' AND expires>?"
                ),
                (time.time() + lease, job, owner, time.time()),
            )
            return c.rowcount == 1

    def progress(self, job: str, owner: str, **fields) -> bool:
        with self.transaction() as c:
            row = c.execute(
                self.sql("SELECT owner,expires FROM queue WHERE id=?")
                + (" FOR UPDATE" if self.postgres else ""),
                (job,),
            ).fetchone()
            old = self._get_in(c, job + "/status.json")
            if (
                not row
                or row[0] != owner
                or row[1] <= time.time()
                or old.get("status") == "cancelled"
            ):
                return False
            self._put(c, job + "/status.json", {**old, **fields})
            return True

    def finish(self, job: str, owner: str) -> bool:
        with self.transaction() as c:
            c.execute(
                self.sql(
                    "UPDATE queue SET state='finished',expires=0 WHERE id=? AND owner=? AND state='running'"
                ),
                (job, owner),
            )
            return c.rowcount == 1

    def publish(self, job: str, owner: str, result: dict) -> bool:
        """Fenced publication: a cancelled job or expired worker cannot finish late."""
        with self.transaction() as c:
            row = c.execute(
                self.sql("SELECT owner,expires FROM queue WHERE id=?")
                + (" FOR UPDATE" if self.postgres else ""),
                (job,),
            ).fetchone()
            status = self._get_in(c, job + "/status.json")
            if (
                not row
                or row[0] != owner
                or row[1] <= time.time()
                or status.get("status") == "cancelled"
            ):
                return False
            self._put(c, job + "/result.json", result)
            self._put(
                c,
                job + "/status.json",
                {
                    **status,
                    "status": "done",
                    "slide_count": result["slide_count"],
                    "rendered": result["slide_count"],
                },
            )
            c.execute(
                self.sql("UPDATE queue SET state='finished',expires=0 WHERE id=?"),
                (job,),
            )
            return True
