"""Local, content-addressed artifact store.

Keys have the fixed shape `<task-uuid>/<sha[0:2]>/<sha256>` and are validated with a strict
regex before any filesystem access; the resolved path must also stay under the root, so
`../` traversal, absolute paths and symlink escapes are rejected. The root must not be
served by any web server; content is only returned through the authorised API.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path

_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{2}/[0-9a-f]{64}$"
)


class ArtifactKeyError(ValueError):
    pass


class ArtifactIntegrityError(RuntimeError):
    pass


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.root.resolve()

    def key_for(self, task_id: str | uuid.UUID, digest: str) -> str:
        key = f"{uuid.UUID(str(task_id))}/{digest[:2]}/{digest}"
        self.validate_key(key)
        return key

    @staticmethod
    def validate_key(key: str) -> None:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ArtifactKeyError("Invalid artifact storage key")

    def _path(self, key: str) -> Path:
        self.validate_key(key)
        root = self._ensure_root()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ArtifactKeyError("Artifact key escapes the storage root")
        return path

    def _write(self, key: str, content: bytes) -> None:
        path = self._path(key)
        if path.exists():
            return  # content-addressed: identical bytes already stored
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _read(self, key: str) -> bytes:
        path = self._path(key)
        data = path.read_bytes()
        if sha256_hex(data) != key.rsplit("/", 1)[-1]:
            raise ArtifactIntegrityError("Artifact content does not match its digest")
        return data

    async def put(self, task_id: str, name: str, content: bytes, media_type: str) -> str:
        """Store bytes and return the storage key. `name`/`media_type` live in the DB row."""
        key = self.key_for(task_id, sha256_hex(content))
        await asyncio.to_thread(self._write, key, content)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read, key)
