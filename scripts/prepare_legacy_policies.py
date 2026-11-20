#!/usr/bin/env python3
"""Prepare a NEW staging copy of legacy reviews with explicit policy provenance.

Never modify an archive or live /data. Pass the rule configuration from the
source runtime, not the current defaults. Existing result/decision bytes stay
unchanged; historical engine versions that were never recorded stay unknown.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import sys
import yaml
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.guidelines import builtin_snapshot
from app.rules.ids import RULES


def prepare(source: Path, destination: Path, rules: Path, source_label: str) -> dict:
    config = yaml.safe_load(rules.read_text())
    if not isinstance(config, dict) or any(key not in RULES or not isinstance(value, dict) for key, value in config.items()):
        raise ValueError("Expected an explicit source-runtime rule configuration")
    # A later-added detector must not silently become part of an older policy.
    for rule in RULES:
        config.setdefault(rule, {"enabled": False})
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    prepared = 0
    for directory in sorted(source.iterdir()):
        if not directory.is_dir() or not re.fullmatch('[a-f0-9]{16}', directory.name):
            continue
        if not (directory / 'status.json').exists():
            continue
        if not (directory / 'deck.pptx').is_file():
            raise ValueError("A review original is missing; do not continue migration")
        target = destination / directory.name
        shutil.copytree(directory, target)
        policy_file = target / 'guideline.json'
        if not policy_file.exists():
            policy = builtin_snapshot()
            policy.update(interpretation='legacy', config=config, migration_source=source_label)
            policy.pop('version')
            policy['version'] = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
            policy_file.write_text(json.dumps(policy))
        status_file = target / 'status.json'
        status = json.loads(status_file.read_text())
        if not status.get('created_at'):
            status['created_at'] = datetime.fromtimestamp(
                status_file.stat().st_mtime, timezone.utc
            ).isoformat(timespec='seconds')
        if not status.get('engine_version'):
            status['engine_version'] = 'legacy-unrecorded'
        status['migration_source'] = source_label
        status_file.write_text(json.dumps(status))
        prepared += 1
    return {'reviews': prepared, 'source_modified': False, 'source_label': source_label}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--rules', type=Path, required=True)
    parser.add_argument('--source-label', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination, args.rules, args.source_label)))
