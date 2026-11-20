#!/usr/bin/env python3
"""Update an existing, migrated Redline app. No resource or permission creation.

DATABASE_URL is read from the process environment, never command arguments.
The default leaves ingress internal until live validation has passed.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import parse_qs, urlparse


def az(*args):
    result = subprocess.run(["az", *args, "--only-show-errors", "--output", "json"], capture_output=True, text=True)
    if result.returncode:
        # Do not surface raw CLI output from a secret-bearing YAML update.
        raise RuntimeError("Azure operation failed: " + " ".join(args[:2]))
    return json.loads(result.stdout) if result.stdout.strip() else None


def configuration(current: dict, listed: list, image: str, database: str, storage: str) -> dict:
    if not re.fullmatch(r"[a-z0-9.:-]+/[a-z0-9/_.-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Use a tested immutable image digest")
    parsed = urlparse(database)
    query = parse_qs(parsed.query)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or query.get("sslmode") != ["verify-full"]:
        raise ValueError("Database connection must require sslmode=verify-full")
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net", storage):
        raise ValueError("Invalid private Azure storage account URL")
    app = copy.deepcopy(current)
    props = app["properties"]
    containers = props["template"]["containers"]
    if len(containers) != 1:
        raise ValueError("Unexpected multi-container app")
    cfg = props["configuration"]
    secrets = {s["name"]: dict(s) for s in cfg.get("secrets", [])}
    for row in listed:
        secrets.setdefault(row["name"], {}).update(row)
    for name, row in secrets.items():
        if row.get("keyVaultUrl"):
            row.pop("value", None)
        elif name != "database-url" and not isinstance(row.get("value"), str):
            raise ValueError("Cannot preserve an existing secret")
    secrets["database-url"] = {"name": "database-url", "value": database}
    cfg["secrets"] = list(secrets.values())
    cfg["ingress"].update(external=False, allowInsecure=False)
    container = containers[0]
    container["image"] = image
    values = {"DATABASE_URL": {"secretRef": "database-url"},
              "REDLINE_DURABLE_JOBS": {"value": "1"},
              "AZURE_STORAGE_ACCOUNT_URL": {"value": storage},
              "AZURE_STORAGE_CONTAINER": {"value": "reviews"},
              "DATA_DIR": {"value": "/data"}, "COOKIE_SECURE": {"value": "true"},
              "MAX_CONCURRENT_JOBS": {"value": "1"}, "MAX_DB_CONNECTIONS": {"value": "4"}}
    container["env"] = [e for e in container.get("env", []) if e["name"] not in values] + [dict(name=k, **v) for k, v in values.items()]
    env = {e["name"]: e for e in container["env"]}
    if not all(env.get(k, {}).get("secretRef") for k in ("APP_PASSWORD", "SESSION_SECRET")):
        raise ValueError("Expected existing authentication secret references")
    props["template"].setdefault("scale", {}).update(minReplicas=1, maxReplicas=1)
    props["template"].pop("revisionSuffix", None)
    keep = ("configuration", "template", "environmentId", "managedEnvironmentId", "workloadProfileName")
    out = {"name": app["name"], "location": app["location"], "properties": {k: props[k] for k in keep if k in props}}
    if app.get("identity"):
        out["identity"] = app["identity"]
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--migration-receipt", type=Path, required=True)
    parser.add_argument("--storage-url", required=True, help="https://<account>.blob.core.windows.net")
    parser.add_argument("--resource-group", default="rg-redline")
    parser.add_argument("--app", default="redline")
    args = parser.parse_args()
    receipt = json.loads(args.migration_receipt.read_text())
    if receipt.get("verified") is not True or receipt.get("reviews", 0) < 1:
        raise SystemExit("A verified migration receipt is required before activating shared state")
    database = os.environ.get("DATABASE_URL", "")
    current = az("containerapp", "show", "--name", args.app, "--resource-group", args.resource_group)
    listed = az("containerapp", "secret", "list", "--show-values", "--name", args.app, "--resource-group", args.resource_group)
    payload = configuration(current, listed, args.image, database, args.storage_url)
    with tempfile.TemporaryDirectory(prefix="redline-durable-deploy-") as directory:
        path = Path(directory) / "update.yaml"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream)
        az("containerapp", "update", "--name", args.app, "--resource-group", args.resource_group, "--yaml", str(path))
    print("Updated immutable image with shared PostgreSQL/Blob state; ingress remains internal pending live validation.")


if __name__ == "__main__":
    main()
