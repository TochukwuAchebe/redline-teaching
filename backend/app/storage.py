"""Durable storage: Azure Blob Storage is the source of truth; local disk is a working copy.

Every file the app writes for a review (the uploaded deck, status, results, decisions,
fix reports, slide images, corrected decks) is mirrored to one blob container under
`<deck_id>/<path>`. When a review is not on local disk (a restart, a new replica),
it is restored from Blob on demand. Without configuration the app runs on local disk only.

Configuration (environment):
  AZURE_STORAGE_ACCOUNT_URL   https://<account>.blob.core.windows.net  (uses managed identity)
  AZURE_STORAGE_CONNECTION_STRING   alternative, e.g. for the local Azurite emulator
  REDLINE_BLOB_CONTAINER      container name (default "redline")
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("redline.storage")

# Small files a history listing needs; images and decks are restored only when a review opens.
META_FILES = ("status.json", "result.json", "decisions.json", "fix.json", "guideline.json")


class BlobMirror:
    def __init__(self, container_client, root: Path):
        self.container = container_client
        self.root = root

    @classmethod
    def from_env(cls, root: Path) -> "BlobMirror | None":
        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
        if not (conn or url):
            return None
        from azure.storage.blob import BlobServiceClient

        if conn:
            service = BlobServiceClient.from_connection_string(conn)
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url=url, credential=DefaultAzureCredential())
        container = service.get_container_client(os.environ.get("REDLINE_BLOB_CONTAINER", "redline"))
        try:
            container.create_container()
        except Exception as exc:  # already exists, or no create permission: both are fine if it exists
            if type(exc).__name__ != "ResourceExistsError":
                log.info("blob container not created (%s); assuming it exists", type(exc).__name__)
        return cls(container, root)

    def _name(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def upload(self, path: Path) -> None:
        with path.open("rb") as data:
            self.container.upload_blob(self._name(path), data, overwrite=True)

    def upload_dir(self, directory: Path) -> None:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.name.endswith(".part"):
                self.upload(path)

    def delete_prefix(self, prefix: str) -> None:
        for blob in self.container.list_blobs(name_starts_with=prefix):
            self.container.delete_blob(blob.name)

    def _download(self, name: str) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        with tmp.open("wb") as out:
            self.container.download_blob(name).readinto(out)
        tmp.replace(target)

    def restore(self, deck_id: str, meta_only: bool = False) -> bool:
        """Copy a review back to local disk. True if anything was found."""
        found = False
        for blob in self.container.list_blobs(name_starts_with=f"{deck_id}/"):
            found = True
            if meta_only and blob.name.split("/", 1)[1] not in META_FILES:
                continue
            if not (self.root / blob.name).exists():
                self._download(blob.name)
        return found

    def deck_ids(self) -> set[str]:
        return {p.name.rstrip("/") for p in self.container.walk_blobs(delimiter="/") if p.name.rstrip("/").isalnum()}


_mirror: BlobMirror | None | bool = False  # False = not yet configured


def mirror(root: Path) -> BlobMirror | None:
    global _mirror
    if _mirror is False:
        try:
            _mirror = BlobMirror.from_env(root)
        except Exception:
            log.exception("blob storage unavailable; continuing on local disk only")
            _mirror = None
        if _mirror:
            log.info("durable storage: Azure Blob container %s", _mirror.container.container_name)
    return _mirror or None


def reset() -> None:
    """Forget the configured mirror (tests switch storage per case)."""
    global _mirror
    _mirror = False
