"""Original-document storage.

Phase 2 ships a local file store (`data/storage/`, or `CTRACE_STORAGE_DIR`). Supabase Storage with
short-lived signed URLs replaces it in the deployment phase behind the same two methods.
Keys are content-addressed: `<org>/<factory>/<sha256><ext>`, so a stored file can never be overwritten
by different bytes.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

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


def document_key(org_id: str, factory_id: str, sha256: str, media_type: str) -> str:
    return f"{org_id}/{factory_id}/{sha256}{EXT.get(media_type, '.bin')}"


def default_storage() -> Storage:
    return LocalStorage()
