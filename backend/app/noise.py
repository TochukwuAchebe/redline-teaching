"""Noise policy: keep the reviewer's list short and decisive. Task C1, see docs/TASKS.md.

1. Drop exact duplicates of the same finding occurrence.
2. Preserve independent occurrences and distinct remediation evidence.
3. Group the same rule + expected value appearing on 3+ slides under one group_id.
4. Order by severity, then slide.
"""
import hashlib
from collections import defaultdict

from app.model.types import Deck, Finding

SEVERITY_RANK = {"error": 0, "warning": 1, "suggestion": 2}
GROUP_MIN_SLIDES = 3


def apply_noise_policy(findings: list[Finding], deck: Deck | None = None) -> list[Finding]:
    seen: dict[tuple, Finding] = {}
    for f in findings:
        # element_id alone is too coarse: a table can contain several cells
        # with independent findings under the same rule. Their IDs carry that
        # occurrence identity. Different rules are not assumed equivalent.
        key = (f.id, f.rule_id, f.slide, f.element_id, f.expected, f.actual,
               f.quote, f.message, tuple(f.bbox.model_dump().values()) if f.bbox else None)
        best = seen.get(key)
        if best is None or SEVERITY_RANK[f.severity] < SEVERITY_RANK[best.severity] or (
            f.severity == best.severity and f.confidence > best.confidence
        ):
            seen[key] = f
    kept = list(seen.values())

    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in kept:
        groups[(f.rule_id, f.expected or "", f.origin)].append(f)
    for (rule_id, _, _), members in groups.items():
        if len({m.slide for m in members}) >= GROUP_MIN_SLIDES:
            digest = hashlib.sha1("|".join(sorted(m.id for m in members)).encode()).hexdigest()[:8]
            gid = f"{rule_id}:{digest}"
            for m in members:
                m.group_id = gid

    kept.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.slide, f.rule_id))
    return kept
