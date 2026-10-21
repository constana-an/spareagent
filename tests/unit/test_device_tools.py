"""Device side of step C: capability probe, pinned data-tools install, sandbox read roots
and parallel Codex runs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import zipfile

import httpx
import pytest

from packages.worker import runtime as worker_runtime
from packages.worker.codex_runtime import CodexRuntime, CodexRuntimeConfig, shared_permission_args
from packages.worker.tools import DATA_TOOL, DataToolEnv, ToolInstallError, probe_capabilities
from tests.unit.test_codex_subscription import FAKE, codex_home, set_mode  # noqa: F401


def wheel(name: str = "satoolprobe", version: str = "1.0") -> tuple[str, bytes]:
    """A minimal valid pure-Python wheel."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": b"VALUE = 42\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{dist}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = []
    for path, body in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(body)}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(record) + "\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, body in files.items():
            zf.writestr(path, body)
    return f"{name}-{version}-py3-none-any.whl", buf.getvalue()


def pins_for(name: str, content: bytes, sha: str | None = None) -> dict:
    asset = {
        "name": "satoolprobe",
        "version": "1.0",
        "url": f"https://files.example/{name}",
        "sha256": sha or hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }
    spec = {
        "id": "probe-1",
        "packages": ["satoolprobe"],
        "smoke_test": "import satoolprobe; assert satoolprobe.VALUE == 42",
        "assets": {"arm64": [asset], "x86_64": [asset]},
    }
    return {"tools": {DATA_TOOL: spec}}


@pytest.fixture(autouse=True)
def arm(monkeypatch):
    monkeypatch.setattr(worker_runtime.platform, "machine", lambda: "arm64")


def serve(content: bytes, status: int = 200):
    return httpx.MockTransport(lambda req: httpx.Response(status, content=content))


async def test_data_tools_install_offline_from_checked_wheels(tmp_path):
    name, content = wheel()
    env = DataToolEnv(tmp_path / "runtime", pins_for(name, content))
    assert env.state == "MISSING" and env.access() is None and env.size_bytes() == len(content)
    caps = probe_capabilities(env, lambda: False)
    assert caps["tools"] == {
        "code": True,
        "doc": True,
        "git": False,
        DATA_TOOL: False,
        "network": True,
    }
    assert caps["executor"] == "codex-subscription"

    await env.install(transport=serve(content))
    assert env.state == "READY" and env.ready()
    access = env.access()
    assert access.path_dirs == (str(env.home / "bin"),)
    assert str(env.home.resolve()) in access.read_roots
    assert probe_capabilities(env, lambda: True)["tools"][DATA_TOOL] is True
    # A fresh probe (e.g. after the app restarts) sees the installed environment.
    assert DataToolEnv(tmp_path / "runtime", pins_for(name, content)).state == "READY"
    assert not list((tmp_path / "runtime" / "tools").glob(".data-tools-*"))


@pytest.mark.parametrize(
    ("status", "sha", "match"),
    [(200, "0" * 64, "checksum"), (404, None, "HTTP 404")],
)
async def test_failed_install_leaves_nothing_behind(tmp_path, status, sha, match):
    name, content = wheel()
    env = DataToolEnv(tmp_path / "runtime", pins_for(name, content, sha))
    with pytest.raises(ToolInstallError, match=match):
        await env.install(transport=serve(content, status))
    assert env.state == "FAILED" and match.split()[0] in env.detail and not env.ready()
    assert list((tmp_path / "runtime" / "tools").iterdir()) == []


async def test_package_without_tool_pins_offers_no_data_tools(tmp_path):
    env = DataToolEnv(tmp_path, {"codex": {}})
    assert env.state == "UNAVAILABLE" and env.size_bytes() == 0
    with pytest.raises(ToolInstallError):
        await env.install()


def test_sandbox_profile_adds_only_safe_read_roots():
    args = shared_permission_args("workspace-write", ("/Users/a/tools", "/opt/py"))
    profile = args[1]
    assert '"/Users/a/tools"="read"' in profile and '"/opt/py"="read"' in profile
    assert '":workspace_roots"="write"' in profile and "network={enabled=false}" in profile
    for bad in ("relative/path", '/x"="write', "/a/../etc", "/a\nb"):
        with pytest.raises(ValueError):
            shared_permission_args("read-only", (bad,))


async def test_runtime_allows_runs_up_to_the_owner_setting(codex_home, tmp_path):  # noqa: F811
    set_mode(codex_home, "slow")

    async def first_event(rt, run_id):
        gen = rt.run(run_id, "x", tmp_path, timeout_seconds=20)
        item = await gen.__anext__()
        return gen, item

    rt = CodexRuntime(CodexRuntimeConfig(codex_bin=FAKE, max_parallel=2))
    g1, _ = await first_event(rt, "a")
    g2, _ = await first_event(rt, "b")
    g3, refused = await first_event(rt, "c")
    assert refused.error_message == "Codex runtime is already busy"
    for run_id, gen in (("a", g1), ("b", g2)):
        await rt.cancel(run_id)
        async for _ in gen:
            pass
    await g3.aclose()
    g4, item = await first_event(rt, "d")  # slots were released
    assert getattr(item, "error_message", None) != "Codex runtime is already busy"
    await rt.cancel("d")
    async for _ in g4:
        pass
    await asyncio.sleep(0)
