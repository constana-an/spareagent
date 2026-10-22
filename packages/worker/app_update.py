"""In-app updates (D-103): find, download and stage a newer menu-bar App.

The operator publishes a prebuilt App; registered devices fetch it with their node
credentials. This module only stages a verified copy under the worker's directory. The
menu-bar App asks the owner, stops sharing and swaps itself (with rollback), so the
installed App is never touched here.
"""

from __future__ import annotations

import asyncio
import plistlib
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from packages.worker.lease_client import NodeClient, TransportError, WorkerApiError

BUNDLE_ID = "org.spareagent.desktop"
UPDATES_DIR = "updates"


class UpdateError(Exception):
    pass


async def _run(*args: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    return await proc.wait()


async def unpack_and_verify(archive: Path, destination: Path, build: int) -> Path:
    """Unpack with ditto (as the installers do) and check bundle id, build and signature."""
    if await _run("/usr/bin/ditto", "-x", "-k", str(archive), str(destination)):
        raise UpdateError("unpack_failed")
    app = destination / "SpareAgent.app"
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise UpdateError("not_spareagent") from exc
    if info.get("CFBundleIdentifier") != BUNDLE_ID or str(info.get("CFBundleVersion")) != str(
        build
    ):
        raise UpdateError("not_spareagent")
    if await _run("/usr/bin/codesign", "--verify", "--strict", str(app)):
        raise UpdateError("signature_invalid")
    return app


class Updater:
    """States: unsupported (not started by the App), idle (not registered yet), current,
    available, downloading, ready (staged at `app_path`), failed."""

    def __init__(
        self,
        directory: Path,
        client: Callable[[], NodeClient | None],
        current_build: int | None,
        arch: str,
        stage: Callable[[Path, Path, int], Awaitable[Path]] = unpack_and_verify,
    ) -> None:
        self.directory = directory
        self._client = client
        self.current_build = current_build
        self.arch = arch
        self._stage = stage
        self.state = "unsupported" if current_build is None else "idle"
        self.release: dict[str, Any] | None = None
        self.error: str | None = None
        self.app_path: Path | None = None
        self._job: asyncio.Task | None = None

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_build": self.current_build,
            "version": self.release and self.release["version"],
            "build": self.release and self.release["build"],
            "error": self.error,
            "app_path": str(self.app_path) if self.app_path else None,
        }

    async def check(self) -> None:
        if self.current_build is None or self.state in ("downloading", "ready"):
            return
        client = self._client()
        if client is None:
            self.state = "idle"
            return
        try:
            release = await client.app_release(self.arch)
        except (WorkerApiError, TransportError):
            return  # keep the last answer; the App checks again later
        self.release, self.error = release, None
        newer = release is not None and int(release["build"]) > self.current_build
        self.state = "available" if newer else "current"

    def start_prepare(self) -> None:
        if self.state in ("available", "failed") and (self._job is None or self._job.done()):
            self._job = asyncio.create_task(self.prepare())

    async def prepare(self) -> None:
        client = self._client()
        release = self.release
        if client is None or release is None:
            return
        self.state, self.error = "downloading", None
        updates = self.directory / UPDATES_DIR
        shutil.rmtree(updates, ignore_errors=True)
        updates.mkdir(mode=0o700, parents=True)
        try:
            data = await client.download_app_release(self.arch, release["sha256"])
            archive = updates / f"SpareAgent-{release['build']}.zip"
            archive.write_bytes(data)
            self.app_path = await self._stage(archive, updates / "staged", int(release["build"]))
            self.state = "ready"
        except UpdateError as exc:
            self.state, self.error = "failed", str(exc)
        except WorkerApiError as exc:
            self.state, self.error = "failed", exc.code
        except TransportError:
            self.state, self.error = "failed", "download_failed"
