"""Pinned runtimes for the one-click package, so a Mac without developer tools works.

The operator's package ships `runtime.json` (see `scripts/runtime-pins.json`): the launcher
uses its Python entry, this module its Codex entry. Codex is fetched from the official npm
registry tarball for this CPU, verified against the pinned SHA-512 integrity, and unpacked
into the worker's own directory. Nothing is installed system-wide and no package manager
(npm, Homebrew) is needed. Files fetched this way carry no quarantine attribute.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNTIME_FILE = "runtime.json"
ARCHES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64"}
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024


class RuntimeSetupError(Exception):
    """Download or verification failure; the message is shown to the owner."""


def machine_arch() -> str:
    arch = ARCHES.get(platform.machine().lower())
    if arch is None:
        raise RuntimeSetupError(f"unsupported CPU {platform.machine()}")
    return arch


def load_pins(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_pins(directory: Path, bundle_path: Path | None) -> Path | None:
    for candidate in (
        bundle_path.parent / RUNTIME_FILE if bundle_path else None,
        directory / RUNTIME_FILE,
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _integrity_matches(integrity: str, digest: bytes) -> bool:
    algo, _, b64 = integrity.partition("-")
    return algo == "sha512" and base64.b64decode(b64) == digest


@dataclass
class ManagedCodex:
    """The pinned Codex CLI, kept under `<worker dir>/runtime/`."""

    root: Path
    pins: dict[str, Any]
    transport: Any = None  # tests inject an httpx transport

    @property
    def _asset(self) -> dict[str, Any]:
        codex = self.pins["codex"]
        asset = codex["assets"].get(machine_arch())
        if asset is None:
            raise RuntimeSetupError(f"no Codex build pinned for {machine_arch()}")
        return asset

    @property
    def home(self) -> Path:
        return self.root / f"codex-{self.pins['codex']['version']}-{self._asset['triple']}"

    @property
    def binary(self) -> Path:
        return self.home / "bin" / "codex"

    @property
    def path_dir(self) -> Path:
        """Helper tools (ripgrep) that Codex expects on PATH, as its npm launcher does."""
        return self.home / "codex-path"

    def installed(self) -> bool:
        return self.binary.is_file() and os.access(self.binary, os.X_OK)

    def activate(self) -> str:
        if self.path_dir.is_dir():
            path = os.environ.get("PATH", "")
            if str(self.path_dir) not in path.split(os.pathsep):
                os.environ["PATH"] = f"{self.path_dir}{os.pathsep}{path}"
        return str(self.binary)

    async def install(self) -> str:
        import httpx

        asset = self._asset
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp_name = tempfile.mkstemp(prefix=".codex-", suffix=".tgz", dir=self.root)
        tmp = Path(tmp_name)
        try:
            digest = hashlib.sha512()
            size = 0
            with os.fdopen(fd, "wb") as fh:
                async with (
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(60, read=120), transport=self.transport
                    ) as client,
                    client.stream("GET", asset["url"], follow_redirects=True) as resp,
                ):
                    if resp.status_code != 200:
                        raise RuntimeSetupError(f"download failed: HTTP {resp.status_code}")
                    async for chunk in resp.aiter_bytes(1 << 20):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise RuntimeSetupError("download larger than expected")
                        digest.update(chunk)
                        fh.write(chunk)
            if not _integrity_matches(asset["integrity"], digest.digest()):
                raise RuntimeSetupError("Codex download failed its checksum")
            self._unpack(tmp, asset["triple"])
        finally:
            tmp.unlink(missing_ok=True)
        return self.activate()

    def _unpack(self, archive: Path, triple: str) -> None:
        prefix = f"package/vendor/{triple}/"
        staging = Path(tempfile.mkdtemp(prefix=".codex-unpack-", dir=self.root))
        try:
            with tarfile.open(archive, "r:gz") as tf:
                members = []
                for m in tf.getmembers():
                    if not m.name.startswith(prefix) or not (m.isfile() or m.isdir()):
                        continue
                    m.name = m.name[len(prefix) :]
                    if m.name:
                        members.append(m)
                tf.extractall(staging, members=members, filter="data")
            if not (staging / "bin" / "codex").is_file():
                raise RuntimeSetupError("Codex package has an unexpected layout")
            if self.home.exists():
                shutil.rmtree(self.home)
            staging.rename(self.home)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


def git_available() -> bool:
    """True when a real git exists. On a fresh Mac /usr/bin/git is only a stub that asks to
    install the Apple command line tools, so check for those first."""
    if shutil.which("git") is None:
        return False
    if platform.system() != "Darwin":
        return True
    try:
        return (
            subprocess.run(  # noqa: S603 - fixed argv
                ["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=10, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def request_git_install() -> None:
    """Opens Apple's own installer dialog for the command line tools (includes git)."""
    if platform.system() == "Darwin":
        try:
            subprocess.Popen(  # noqa: S603 - fixed argv
                ["/usr/bin/xcode-select", "--install"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
