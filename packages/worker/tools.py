"""Device capabilities and the optional data-tools environment (design §4-§5).

The worker declares what this device can do from its own probes, never from a checkbox:

    code         the Codex executor is configured (always, for this worker)
    doc          stdlib document handling built into the worker (always)
    git          real git (Apple command line tools on macOS)
    python-data  the pinned pandas/matplotlib/openpyxl environment is installed and passes
                 its smoke test

The data environment is installed only when the owner asks for it: every wheel listed in
the package's runtime.json is downloaded from PyPI, checked against its SHA-256 and
installed offline into a private virtual environment under the worker directory.
Codex gets that environment (and the base interpreter it links to) as read-only roots.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.worker.runtime import machine_arch

DATA_TOOL = "python-data"
READY_FILE = ".ready"
MAX_WHEEL_BYTES = 120 * 1024 * 1024


class ToolInstallError(Exception):
    pass


@dataclass(frozen=True)
class ToolAccess:
    """What a Codex run needs to use a tool from inside its sandbox."""

    read_roots: tuple[str, ...]
    path_dirs: tuple[str, ...]


class DataToolEnv:
    def __init__(self, root: Path, pins: dict[str, Any] | None) -> None:
        self.spec = ((pins or {}).get("tools") or {}).get(DATA_TOOL)
        self.home = root / "tools" / f"{DATA_TOOL}-{self.spec['id']}" if self.spec else None
        self.state = "READY" if self.ready() else ("MISSING" if self.spec else "UNAVAILABLE")
        self.detail = ""

    @property
    def python(self) -> Path | None:
        return self.home / "bin" / "python" if self.home else None

    def ready(self) -> bool:
        return bool(self.home and (self.home / READY_FILE).is_file() and self.python.exists())

    def access(self) -> ToolAccess | None:
        if not self.ready():
            return None
        base = Path(sys.base_prefix).resolve()
        return ToolAccess((str(self.home.resolve()), str(base)), (str(self.home / "bin"),))

    def size_bytes(self) -> int:
        if not self.spec:
            return 0
        return sum(w.get("size", 0) for w in self.spec["assets"].get(machine_arch(), []))

    async def install(self, transport=None) -> None:
        """Download, verify and install the pinned wheels; idempotent."""
        if self.spec is None or self.home is None:
            raise ToolInstallError("this package pins no data tools")
        if self.ready():
            self.state = "READY"
            return
        wheels = self.spec["assets"].get(machine_arch())
        if not wheels:
            raise ToolInstallError(f"no data tools pinned for {machine_arch()}")
        self.state, self.detail = "INSTALLING", ""
        self.home.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix=".data-tools-", dir=self.home.parent))
        try:
            files = await self._download(wheels, staging / "wheels", transport)
            env = staging / "env"
            await asyncio.to_thread(self._build, env, files)
            if self.home.exists():
                shutil.rmtree(self.home)
            env.rename(self.home)
            (self.home / READY_FILE).write_text(self.spec["id"])
            self.state = "READY"
        except Exception as exc:
            self.state, self.detail = "FAILED", str(exc)[:300]
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def _download(self, wheels, directory: Path, transport) -> list[Path]:
        import httpx

        directory.mkdir(parents=True)
        out = []
        timeout = httpx.Timeout(60, read=120)
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            for n, wheel in enumerate(wheels, 1):
                self.detail = f"{n}/{len(wheels)} {wheel['name']}"
                name = wheel["url"].rsplit("/", 1)[-1]
                if "/" in name or not name.endswith(".whl"):
                    raise ToolInstallError(f"unexpected wheel name {name!r}")
                digest, size = hashlib.sha256(), 0
                path = directory / name
                with path.open("wb") as fh:
                    async with client.stream("GET", wheel["url"], follow_redirects=True) as r:
                        if r.status_code != 200:
                            raise ToolInstallError(f"{wheel['name']}: HTTP {r.status_code}")
                        async for chunk in r.aiter_bytes(1 << 20):
                            size += len(chunk)
                            if size > MAX_WHEEL_BYTES:
                                raise ToolInstallError(f"{wheel['name']}: too large")
                            digest.update(chunk)
                            fh.write(chunk)
                if digest.hexdigest() != wheel["sha256"]:
                    raise ToolInstallError(f"{wheel['name']}: checksum mismatch")
                out.append(path)
        return out

    def _build(self, env: Path, files: list[Path]) -> None:
        def run(*argv: str) -> None:
            proc = subprocess.run(  # noqa: S603 - fixed argv, local interpreter
                argv, capture_output=True, text=True, timeout=600, check=False
            )
            if proc.returncode != 0:
                raise ToolInstallError((proc.stderr or proc.stdout).strip()[-300:])

        run(sys.executable, "-m", "venv", str(env))
        py = str(env / "bin" / "python")
        run(
            py,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            "--no-index",
            "--no-deps",
            *map(str, files),
        )
        run(py, "-c", self.spec["smoke_test"])


def probe_capabilities(data_env: DataToolEnv, git_check) -> dict[str, Any]:
    return {
        "executor": "codex-subscription",
        "tools": {
            "code": True,
            "doc": True,
            "git": bool(git_check()),
            DATA_TOOL: data_env.ready(),
            # Any device can go online; whether shared tasks may is the owner's choice.
            "network": True,
        },
        "os": f"{platform.system().lower()}-{platform.release()}-{platform.machine()}",
        "cpu_count": os.cpu_count() or 1,
    }
