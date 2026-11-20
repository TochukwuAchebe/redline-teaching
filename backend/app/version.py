"""Content-derived runtime identity; no fabricated release identifiers."""

from functools import lru_cache
import hashlib
from pathlib import Path


@lru_cache(maxsize=1)
def engine_version() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml", ".md"}:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()
