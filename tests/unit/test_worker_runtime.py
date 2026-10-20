"""Pinned Codex download for the one-click package: checksum, layout and activation."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import tarfile

import httpx
import pytest

from packages.worker import runtime
from packages.worker.app import AppError, CodexTools, Controller
from packages.worker.runtime import ManagedCodex, RuntimeSetupError
from tests.unit.test_one_click_app import BUNDLE, FakeClient, FakeCodex

TRIPLE = "aarch64-apple-darwin"


def tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def pins_for(blob: bytes) -> dict:
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()
    asset = {"triple": TRIPLE, "url": "https://registry.example/codex.tgz", "integrity": integrity}
    return {"codex": {"version": "9.9.9", "assets": {"arm64": asset, "x86_64": asset}}}


def managed(tmp_path, blob, pins=None, status=200):
    transport = httpx.MockTransport(lambda req: httpx.Response(status, content=blob))
    return ManagedCodex(tmp_path / "runtime", pins or pins_for(blob), transport=transport)


GOOD = tarball(
    {
        f"package/vendor/{TRIPLE}/bin/codex": b"#!/bin/sh\necho codex\n",
        f"package/vendor/{TRIPLE}/codex-path/rg": b"#!/bin/sh\n",
        "package/package.json": b"{}",
        "package/../../escape": b"x",
    }
)


@pytest.fixture(autouse=True)
def arm(monkeypatch):
    monkeypatch.setattr(runtime.platform, "machine", lambda: "arm64")


async def test_pinned_codex_is_verified_unpacked_and_put_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    m = managed(tmp_path, GOOD)
    assert not m.installed()
    binary = await m.install()
    assert binary == str(m.binary) and m.installed()
    assert os.access(m.binary, os.X_OK)
    assert str(m.path_dir) in os.environ["PATH"].split(os.pathsep)
    assert not (tmp_path / "escape").exists() and not (m.home / "package.json").exists()
    assert not list((tmp_path / "runtime").glob(".codex-*"))  # temp files cleaned up


async def test_checksum_mismatch_installs_nothing(tmp_path):
    m = managed(tmp_path, GOOD, pins=pins_for(b"something else"))
    with pytest.raises(RuntimeSetupError, match="checksum"):
        await m.install()
    assert not m.installed() and not list((tmp_path / "runtime").iterdir())


async def test_http_error_and_bad_layout_are_reported(tmp_path):
    with pytest.raises(RuntimeSetupError, match="HTTP 404"):
        await managed(tmp_path, GOOD, status=404).install()
    odd = tarball({"package/README.md": b"hi"})
    with pytest.raises(RuntimeSetupError, match="layout"):
        await managed(tmp_path, odd).install()


async def test_codex_tools_prefer_path_then_managed_download(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    import packages.worker.app as app_mod

    monkeypatch.setattr(app_mod.shutil, "which", lambda name: None)
    tools = CodexTools("codex", managed=managed(tmp_path, GOOD))
    assert not tools.installed()
    await tools.install()
    assert tools.codex_bin.endswith("/bin/codex")
    again = CodexTools("codex", managed=managed(tmp_path, GOOD))
    assert again.installed() and again.codex_bin.endswith("/bin/codex")

    broken = CodexTools("codex", managed=managed(tmp_path / "b", GOOD, pins=pins_for(b"x")))
    with pytest.raises(AppError) as exc:
        await broken.install()
    assert exc.value.code == "codex_install_failed"


async def test_missing_git_stops_before_login_and_asks_for_apple_tools(tmp_path, monkeypatch):
    asked = []
    import packages.worker.app as app_mod

    monkeypatch.setattr(app_mod, "request_git_install", lambda: asked.append(True))
    codex = FakeCodex(logged_in=False)
    log: list = []
    c = Controller(
        tmp_path,
        BUNDLE,
        codex=codex,
        client_factory=lambda state: FakeClient(log),
        agent_factory=lambda stop: stop.wait(),
        git_check=lambda: False,
    )
    await c.go_online({"device_label": "Fresh Mac", "subscription_type": "PLUS"})
    assert c.snap.phase == "ERROR" and c.snap.error["code"] == "git_missing"
    assert asked == [True] and codex.calls == [] and log == []
