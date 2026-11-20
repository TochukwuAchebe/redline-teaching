"""Private durable objects via Azure Blob; local files remain the development adapter."""

from __future__ import annotations
import os
import hashlib
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=4)
def _container(url: str, name: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient(
        url,
        credential=DefaultAzureCredential(),
        connection_timeout=10,
        read_timeout=60,
        retry_total=3,
        max_single_put_size=4 * 1024 * 1024,
        max_block_size=4 * 1024 * 1024,
    ).get_container_client(name)


def enabled() -> bool:
    # Durable objects belong to durable mode. The legacy single-container mode
    # uses the same account variable for its Blob mirror (app/storage.py).
    return bool(os.environ.get("AZURE_STORAGE_ACCOUNT_URL")) and os.environ.get(
        "REDLINE_DURABLE_JOBS", ""
    ).lower() in {"1", "true"}


def put(path: Path, key: str) -> None:
    if not enabled():
        return
    container = _container(
        os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        os.environ.get("AZURE_STORAGE_CONTAINER", "reviews"),
    )
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    from azure.core.exceptions import ResourceNotFoundError

    try:
        if (
            container.get_blob_client(key).get_blob_properties().metadata.get("sha256")
            == digest
        ):
            return
    except ResourceNotFoundError:
        pass
    with path.open("rb") as data:
        container.upload_blob(
            key,
            data,
            overwrite=True,
            max_concurrency=1,
            metadata={"sha256": digest},
            validate_content=True,
        )


def checksum(key: str) -> str | None:
    container = _container(
        os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        os.environ.get("AZURE_STORAGE_CONTAINER", "reviews"),
    )
    return container.get_blob_client(key).get_blob_properties().metadata.get("sha256")


def stream(key: str):
    container = _container(
        os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        os.environ.get("AZURE_STORAGE_CONTAINER", "reviews"),
    )
    return container.download_blob(key, max_concurrency=1).chunks()


def discard_unregistered(key: str) -> None:
    """Remove only a newly generated object that never became a registered review."""
    if not enabled():
        return
    from azure.core.exceptions import ResourceNotFoundError

    container = _container(
        os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        os.environ.get("AZURE_STORAGE_CONTAINER", "reviews"),
    )
    try:
        container.delete_blob(key)
    except ResourceNotFoundError:
        pass


def fetch(path: Path, key: str) -> Path:
    if path.exists():
        return path
    if not enabled():
        return path
    from azure.core.exceptions import ResourceNotFoundError

    container = _container(
        os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        os.environ.get("AZURE_STORAGE_CONTAINER", "reviews"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each concurrent request owns a temp file; rename only a complete object.
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as out:
            container.download_blob(key, max_concurrency=1).readinto(out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except ResourceNotFoundError:
        pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path
