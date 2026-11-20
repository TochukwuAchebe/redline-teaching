"""Operator maintenance for the current workspace.

    python -m app.maintenance keep <review-id> [<review-id> ...]
    python -m app.maintenance drop-guideline <guideline-id>

`keep` keeps the named reviews and removes every other review's metadata (its documents, queue entry
and upload record). `drop-guideline` removes one saved custom guideline. Each removed row is first
saved to a JSON backup, uploaded to Blob Storage as backups/<name> when objects are enabled. Review
files are left in storage, so a removal can be undone. Nothing is removed if a named item doesn't exist.
"""
from __future__ import annotations

import json
import sys
import time

from app import jobs, objects

# (table, condition, suffix appended to the review id)
TABLES = [("documents", "key LIKE ?", "/%"), ("reviews", "id=?", ""), ("queue", "id=?", ""), ("uploads", "job=?", "")]


def keep(ids: set[str]) -> dict:
    s = jobs.state()
    q = s.sql
    with s.transaction() as c:
        present = dict(c.execute(q("SELECT id, filename FROM reviews")).fetchall())
        missing = ids - present.keys()
        if missing:
            raise SystemExit(f"Not found, nothing removed: {sorted(missing)}")
        remove = sorted(present.keys() - ids)
        backup = {i: {t: [list(r) for r in c.execute(q(f"SELECT * FROM {t} WHERE {w}"), (i + e,)).fetchall()]
                      for t, w, e in TABLES} for i in remove}
    path = _backup(s, "pruned", backup)
    with s.transaction() as c:
        for i in remove:
            for t, w, e in TABLES:
                c.execute(q(f"DELETE FROM {t} WHERE {w}"), (i + e,))
        kept = c.execute(q("SELECT id, filename FROM reviews")).fetchall()
    return {"removed": [[i, present[i]] for i in remove], "kept": [list(k) for k in kept], "backup": path.name}


def drop_guideline(guideline_id: str) -> dict:
    s = jobs.state()
    q = s.sql
    key = f"guidelines/{guideline_id}.json"
    with s.transaction() as c:
        row = c.execute(q("SELECT key, payload FROM documents WHERE key=?"), (key,)).fetchone()
    if row is None:
        raise SystemExit(f"Not found, nothing removed: {guideline_id}")
    path = _backup(s, "guideline", {"documents": [list(row)]})
    with s.transaction() as c:
        c.execute(q("DELETE FROM documents WHERE key=?"), (key,))
    return {"removed": json.loads(row[1]).get("name"), "backup": path.name}


def _backup(s, label: str, rows: dict):
    path = s.root / f"{label}-{int(time.time())}.json"
    path.write_text(json.dumps(rows))
    objects.put(path, f"backups/{path.name}")
    return path


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "keep":
        print(json.dumps(keep(set(sys.argv[2:])), indent=1))
    elif len(sys.argv) == 3 and sys.argv[1] == "drop-guideline":
        print(json.dumps(drop_guideline(sys.argv[2]), indent=1))
    else:
        raise SystemExit(__doc__)
