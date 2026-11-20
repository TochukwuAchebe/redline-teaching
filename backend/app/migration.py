"""Explicit, repeatable legacy-file migration; never sends deck text to AI.

Run while writes to the legacy app are paused. Originals/previews are copied
first; each review's metadata is committed atomically after object verification.
"""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from app import jobs, objects
from app.guidelines import builtin_snapshot


def migrate(source: Path, engine_source: str) -> dict:
    if not jobs.durable():
        raise ValueError("Enable durable state before migration")
    store = jobs.state()
    if store.get("system/live"):
        raise ValueError(
            "Durable service already activated; do not overwrite it with a legacy snapshot"
        )
    reviews = []
    files = 0
    size = 0
    for directory in sorted(source.iterdir()):
        if not directory.is_dir() or not re.fullmatch("[a-f0-9]{16}", directory.name):
            continue
        status_path = directory / "status.json"
        if not status_path.exists():
            continue
        job = directory.name
        status = json.loads(status_path.read_text())
        if not (directory / "deck.pptx").exists():
            raise ValueError(
                "A review original is missing; migration must not discard it"
            )
        policy_path = directory / "guideline.json"
        if policy_path.exists():
            policy = json.loads(policy_path.read_text())
        else:
            policy = builtin_snapshot()
            policy["interpretation"] = "legacy"
            import yaml
            from app.rules.ids import RULES

            frozen = Path(__file__).parent / "config/acme-legacy-491e8a4.yaml"
            policy["config"] = yaml.safe_load(frozen.read_text())
            for rule in RULES:
                policy["config"].setdefault(rule, {"enabled": False})
            material = {k: v for k, v in policy.items() if k not in ("version",)}
            policy["version"] = hashlib.sha256(
                json.dumps(material, sort_keys=True).encode()
            ).hexdigest()
        status = {
            **status,
            "created_at": status.get("created_at") or datetime.fromtimestamp(
                status_path.stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds"),
            "guideline": {k: policy[k] for k in ("id", "name", "version")},
            "engine_version": status.get("engine_version") or engine_source,
        }
        fingerprints = {}
        for path in [
            directory / "deck.pptx",
            *sorted((directory / "slides").glob("*.png")),
            *([directory / "fixed.pptx"] if (directory / "fixed.pptx").exists() else []),
            *sorted((directory / "fixed_slides").glob("*.png")),
            *sorted((directory / "fixes").rglob("*.pptx")),
            *sorted((directory / "fixes").rglob("*.png")),
        ]:
            key = job + "/" + path.relative_to(directory).as_posix()
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if not objects.enabled():
                import shutil

                target = jobs.DATA_DIR / key
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.resolve() != path.resolve():
                    shutil.copyfile(path, target)
            if objects.enabled():
                objects.put(path, key)
                if objects.checksum(key) != digest:
                    raise RuntimeError("Durable object checksum mismatch")
            fingerprints[key] = digest
            files += 1
            size += path.stat().st_size
        documents = {"status.json": status, "guideline.json": policy}
        for name in ("result.json", "decisions.json", "fix.json"):
            path = directory / name
            if path.exists():
                documents[name] = json.loads(path.read_text())
        fix = documents.get("fix.json")
        if fix is not None:
            if fix.get("state") == "done":
                # Corrected files were copied under the review's own id above.
                fix.setdefault("artifact_prefix", job)
            elif fix.get("state") == "running":
                # No task is migrated with it; let the reviewer apply fixes again.
                documents["fix.json"] = {"state": "none"}
        with store.transaction() as c:
            for name, data in documents.items():
                store._put(c, job + "/" + name, data)
        for name, data in documents.items():
            if store.get(job + "/" + name) != data:
                raise RuntimeError("Metadata migration verification failed")
        if status.get("status") in jobs.ACTIVE:
            store.enqueue(job)
        reviews.append(
            {
                "id": job,
                "original_and_previews_sha256": hashlib.sha256(
                    json.dumps(fingerprints, sort_keys=True).encode()
                ).hexdigest(),
                "decisions_sha256": hashlib.sha256(
                    json.dumps(
                        documents.get("decisions.json", {}), sort_keys=True
                    ).encode()
                ).hexdigest(),
            }
        )
    return {
        "reviews": len(reviews),
        "files": files,
        "bytes": size,
        "verified": True,
        "review_receipts": reviews,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--engine-source", required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.engine_source)))
