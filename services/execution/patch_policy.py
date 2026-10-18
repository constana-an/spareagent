"""Static policy for git patches (used by the worker on its own output and, independently,
by the platform verifier on what a worker submitted).

Rejects: path traversal, absolute paths, .git internals, forbidden files (.gitmodules,
.gitattributes, CI config), symlinks/gitlinks/mode changes, renames/copies, binary patches,
files outside the recipe's allowed set, and oversized patches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PATCH_BYTES = 256 * 1024
_SAFE = re.compile(r"^[A-Za-z0-9._@+/-]+$")
FORBIDDEN_FILES = frozenset({".gitmodules", ".gitattributes", ".gitignore", ".git"})
FORBIDDEN_PREFIXES = (".git/", ".github/", ".gitlab/", ".circleci/")


class PatchRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PatchSummary:
    paths: tuple[str, ...]
    additions: int
    deletions: int


def _check_path(path: str) -> str:
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or not _SAFE.fullmatch(path)
        or any(p in ("", ".", "..") for p in parts)
        or any(p.lower() in FORBIDDEN_FILES for p in parts)
        or path.lower().startswith(FORBIDDEN_PREFIXES)
    ):
        raise PatchRejected(f"forbidden path: {path[:120]!r}")
    return path


def inspect_patch(patch: bytes, allowed_paths: frozenset[str] | None = None) -> PatchSummary:
    if len(patch) > MAX_PATCH_BYTES:
        raise PatchRejected("patch exceeds size limit")
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchRejected("patch is not UTF-8 text") from exc
    if "\x00" in text:
        raise PatchRejected("patch contains NUL bytes")
    if not text.startswith("diff --git "):
        raise PatchRejected("not a git patch")
    paths: list[str] = []
    adds = dels = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
            if not m:
                raise PatchRejected("unparseable diff header")
            a, b = m.groups()
            if a != b:
                raise PatchRejected("renames are not allowed")
            paths.append(_check_path(a))
        elif line.startswith(("old mode", "new mode", "deleted file mode", "new file mode")):
            mode = line.rsplit(" ", 1)[-1]
            if mode != "100644" or line.startswith(("old mode", "new mode")):
                raise PatchRejected(f"file mode change not allowed: {line}")
        elif line.startswith(("rename ", "copy ", "similarity index", "dissimilarity index")):
            raise PatchRejected("renames/copies are not allowed")
        elif line.startswith(("GIT binary patch", "Binary files")):
            raise PatchRejected("binary patches are not allowed")
        elif line.startswith(("--- ", "+++ ")):
            target = line[4:]
            if target != "/dev/null":
                if not target.startswith(("a/", "b/")):
                    raise PatchRejected("unexpected file header")
                _check_path(target[2:])
        elif line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    if not paths:
        raise PatchRejected("patch changes no files")
    if len(set(paths)) != len(paths):
        raise PatchRejected("file appears twice in patch")
    if allowed_paths is not None:
        unexpected = sorted(set(paths) - set(allowed_paths))
        if unexpected:
            raise PatchRejected(f"unexpected changes: {unexpected}")
    return PatchSummary(tuple(paths), adds, dels)
