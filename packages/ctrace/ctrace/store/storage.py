"""Original-document storage.

Phase 2 ships a local file store (`data/storage/`, or `CTRACE_STORAGE_DIR`). Supabase Storage with
short-lived signed URLs replaces it in the deployment phase behind the same two methods.
Keys are content-addressed: `<org>/<factory>/<sha256><ext>`, so a stored file can never be overwritten
by different bytes.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from .. import config

EXT = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.getenv("CTRACE_STORAGE_DIR") or config.DATA_DIR / "storage").resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError(f"C-Trace storage key escapes the storage root: {key!r}")
        return p

    def put(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            tmp = p.with_suffix(p.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(p)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        p = self._path(prefix)
        if p.is_dir():
            shutil.rmtree(p)


class DbStorage:
    """Documents in Postgres (`document_blobs`), for serverless hosts with no persistent disk. Uses the caller's
    org-scoped transaction, so RLS applies to the blobs as to every other organisation row."""

    def __init__(self, conn: Any):
        self.conn = conn

    def put(self, key: str, data: bytes) -> str:
        self.conn.execute("insert into document_blobs (storage_key, org_id, data) values (%s, ctrace_current_org(), %s) "
                          "on conflict (storage_key) do nothing", (key, data))
        return key

    def get(self, key: str) -> bytes:
        row = self.conn.execute("select data from document_blobs where storage_key = %s", (key,)).fetchone()
        if row is None:
            raise FileNotFoundError(key)
        return bytes(row["data"])


def document_key(org_id: str, factory_id: str, sha256: str, media_type: str) -> str:
    return f"{org_id}/{factory_id}/{sha256}{EXT.get(media_type, '.bin')}"


def default_storage(conn: Any = None) -> Storage:
    """`CTRACE_STORAGE=db` keeps documents in Postgres (needs the caller's connection); default: local files."""
    if os.getenv("CTRACE_STORAGE", "local") == "db":
        if conn is None:
            raise RuntimeError("CTRACE_STORAGE=db needs a database connection")
        return DbStorage(conn)
    return LocalStorage()
