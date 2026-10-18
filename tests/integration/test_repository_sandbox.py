"""RepositoryManager against deterministic Git fixtures (+ optional real GitHub) and the
hardened Docker sandbox against hostile workloads."""

import os
import threading
import time
from pathlib import Path

import pytest

from services.execution.repository_manager import RepoLimits, RepositoryError, RepositoryManager
from services.execution.sandbox import SandboxLimits
from tests.conftest import PY_SHA, PY_URL

FIX = "https://github.com/spareagent-fixtures/"
network = pytest.mark.skipif(
    os.environ.get("SPAREAGENT_NETWORK_TESTS") != "1", reason="set SPAREAGENT_NETWORK_TESTS=1"
)


# ------------------------------------------------------------------ repository checkout
def test_pinned_commit_checkout(fixture_root):
    manager = RepositoryManager(mirror_root=fixture_root)
    with manager.checkout(PY_URL, PY_SHA) as co:
        assert co.commit_sha == PY_SHA
        assert co.git("rev-parse", "HEAD").strip() == PY_SHA
        assert sorted(p.name for p in co.path.iterdir() if p.name != ".git") == [
            "README.md",
            "calc.py",
            "test_calc.py",
        ]
        # hooks are disabled and the checkout has no remote config to abuse
        assert co.git("config", "--get", "core.hooksPath").strip() == "/dev/null"
        assert not any((co.path / ".git" / "hooks").glob("*"))
        root = co._root
    assert not root.exists()  # temporary directory removed on cleanup


@pytest.mark.parametrize(
    ("name", "sha", "code"),
    [
        ("python-calc", "0" * 40, "COMMIT_NOT_FOUND"),
        ("does-not-exist", "0" * 40, "COMMIT_NOT_FOUND"),
        ("evil-symlink", "02970983f1e41f5f7a4528180b6f53df7bf4cd55", "SYMLINKS_UNSUPPORTED"),
        ("evil-submodule", "56c724f886e139d9eb49f7d832f0e7a793c7782c", "SUBMODULES_UNSUPPORTED"),
        ("evil-lfs", "981496e46521e5f9ebd377debaf00b5b0afa432d", "LFS_UNSUPPORTED"),
    ],
)
def test_rejected_repositories(fixture_root, name, sha, code):
    with pytest.raises(RepositoryError) as info:
        RepositoryManager(mirror_root=fixture_root).checkout(FIX + name, sha)
    assert info.value.code == code


def test_oversized_repository(fixture_root):
    limits = RepoLimits(max_fetch_bytes=300_000)
    with pytest.raises(RepositoryError) as info:
        RepositoryManager(limits, mirror_root=fixture_root).checkout(
            FIX + "evil-large", "7da8911ab76eadf4978f2967732267df042cb1db"
        )
    assert info.value.code == "TOO_LARGE"
    limits = RepoLimits(max_tree_bytes=1000)
    with pytest.raises(RepositoryError) as info:
        RepositoryManager(limits, mirror_root=fixture_root).checkout(
            FIX + "evil-large", "7da8911ab76eadf4978f2967732267df042cb1db"
        )
    assert info.value.code == "TOO_LARGE"


def test_fixture_mirrors_only_for_fixture_owner(fixture_root):
    # Without a mirror root (production) fixture URLs go to github.com and are never local.
    manager = RepositoryManager(mirror_root=fixture_root)
    assert manager._source("spareagent-fixtures", "python-calc")[1] is True
    assert manager._source("octocat", "Hello-World") == (
        "https://github.com/octocat/Hello-World.git",
        False,
    )
    assert RepositoryManager()._source("spareagent-fixtures", "python-calc")[1] is False


def test_host_git_config_and_credentials_ignored(fixture_root, tmp_path, monkeypatch):
    """A poisoned ~/.gitconfig (credential helper, insteadOf redirect) has no effect."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        '[credential]\n\thelper = "!echo password=stolen"\n'
        '[url "file:///nonexistent/"]\n\tinsteadOf = file://\n'
        "[core]\n\thooksPath = /tmp/evil-hooks\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_shouldneverbeused0000000000000000000")
    with RepositoryManager(mirror_root=fixture_root).checkout(PY_URL, PY_SHA) as co:
        assert co.git("config", "--get", "core.hooksPath").strip() == "/dev/null"
        assert "GITHUB_TOKEN" not in co._env and co._env["HOME"] != str(home)


@network
def test_real_github_pinned_checkout():
    sha = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
    with RepositoryManager().checkout("https://github.com/octocat/Hello-World", sha) as co:
        assert co.git("rev-parse", "HEAD").strip() == sha


@network
def test_real_github_unavailable_commit_and_private_repo():
    with pytest.raises(RepositoryError) as info:
        RepositoryManager().checkout("https://github.com/octocat/Hello-World", "1" * 40)
    assert info.value.code == "COMMIT_NOT_FOUND"
    with pytest.raises(RepositoryError) as info:
        RepositoryManager().checkout(
            "https://github.com/constana-an/definitely-not-a-repo-xyz", "1" * 40
        )
    assert info.value.code == "AUTH_REQUIRED"


# ------------------------------------------------------------------ sandbox
def test_sandbox_health(sandbox):
    health = sandbox.health()
    checks = health["checks"]
    assert checks["uid"] == 65534 and checks["net"] is False
    assert checks["rootfs_writable"] is False and checks["docker_sock"] is False
    assert checks["nnp"] == "1" and int(checks["caps"], 16) == 0


def test_sandbox_cannot_see_host_secrets(sandbox, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret:secret@db/x")
    monkeypatch.setenv("NODE_TOKEN_PEPPER", "pepper-secret-value-123")
    script = (
        "import os, json\n"
        "paths = ['/var/run/docker.sock', '/root/.ssh', '/home', '/certs', '/app',\n"
        "         '/var/lib/spareagent', '/fixtures', '/run/secrets']\n"
        "print(json.dumps({'env': sorted(os.environ),\n"
        "  'visible': [p for p in paths if os.path.exists(p)],\n"
        "  'environ1': open('/proc/1/environ').read().count('DATABASE_URL')}))\n"
    )
    result = sandbox.execute_script(script)
    import json

    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert "DATABASE_URL" not in data["env"] and "NODE_TOKEN_PEPPER" not in data["env"]
    assert data["visible"] == [] and data["environ1"] == 0
    assert "secret" not in result.stdout


def test_sandbox_network_disabled(sandbox):
    script = (
        "import socket\n"
        "for target in [('1.1.1.1', 53), ('8.8.8.8', 443), ('host.docker.internal', 80),\n"
        "               ('postgres', 5432), ('169.254.169.254', 80)]:\n"
        "    try:\n"
        "        socket.create_connection(target, timeout=1); print('OPEN', target)\n"
        "    except OSError:\n"
        "        print('blocked', target[0])\n"
        "print([i for i in socket.if_nameindex()])\n"
    )
    out = sandbox.execute_script(script).stdout
    assert "OPEN" not in out and out.count("blocked") == 5
    assert "eth0" not in out  # only loopback exists


def test_sandbox_filesystem_limits(sandbox):
    script = (
        "import os\n"
        "try:\n open('/etc/x','w'); print('ROOT_WRITABLE')\n"
        "except OSError: print('ro-root')\n"
        "chunk = b'0' * (1024 * 1024)\n"
        "try:\n"
        "    with open('/workspace/fill', 'wb') as f:\n"
        "        for _ in range(200): f.write(chunk)\n"
        "    print('NO_QUOTA')\n"
        "except OSError: print('quota-enforced')\n"
    )
    out = sandbox.execute_script(script).stdout
    assert "ro-root" in out and "quota-enforced" in out


def test_sandbox_timeout_and_cleanup(sandbox):
    before = set(sandbox.list_containers())
    started = time.monotonic()
    result = sandbox.execute_script("while True: pass", timeout=2)
    assert result.timed_out and result.exit_code == -1
    assert time.monotonic() - started < 15
    assert result.container not in sandbox.list_containers()
    assert set(sandbox.list_containers()) <= before


def test_sandbox_cancellation_and_cleanup(sandbox, tmp_path):
    (tmp_path / "s.py").write_text("import time\ntime.sleep(60)\n")
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    result = sandbox.execute(Path(tmp_path), ["python3", "s.py"], cancel_event=cancel)
    assert result.cancelled and result.duration_ms < 15000
    assert result.container not in sandbox.list_containers()


def test_sandbox_memory_and_process_limits(sandbox):
    from services.execution.sandbox import DockerSandbox

    small = DockerSandbox(sandbox.image, SandboxLimits(memory_mb=96, pids=24))
    oom = small.execute_script("x = bytearray(300 * 1024 * 1024)\nprint('ALLOCATED')")
    assert oom.oom_killed and "ALLOCATED" not in oom.stdout
    forks = small.execute_script(
        "import os, time\nn = 0\n"
        "try:\n"
        "    while n < 500:\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(3); os._exit(0)\n"
        "        n += 1\n"
        "except OSError:\n"
        "    pass\n"
        "print('forked', n)\n"
    )
    assert int(forks.stdout.split()[-1]) < 30


def test_reaper_removes_stale_sandboxes(sandbox):
    name = "sa-sbx-stale-test"
    sandbox._docker(*sandbox.run_args(name, 60))
    try:
        assert name in sandbox.list_containers()
        assert sandbox.reap_stale(max_age_seconds=-1) >= 1
        assert name not in sandbox.list_containers()
    finally:
        sandbox._remove(name)
