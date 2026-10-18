"""Deterministic local Git fixture repositories (tests + demo).

    python -m services.execution.fixtures /fixtures

Each fixture is built as a bare repository at `<root>/spareagent-fixtures/<name>.git` with a
fixed author, committer and timestamp, so its commit SHA is identical on every machine and can
be pinned in the execution catalog. In local mode the RepositoryManager maps
`https://github.com/spareagent-fixtures/<name>` onto these mirrors (see DECISIONS D-33).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURE_OWNER = "spareagent-fixtures"
_IDENTITY = {
    "GIT_AUTHOR_NAME": "SpareAgent Fixtures",
    "GIT_AUTHOR_EMAIL": "fixtures@spareagent.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "SpareAgent Fixtures",
    "GIT_COMMITTER_EMAIL": "fixtures@spareagent.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}

PYTHON_CALC = {
    "README.md": "# python-calc\nFixture with one intentionally failing function (`add`).\n",
    "calc.py": ("def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"),
    "test_calc.py": (
        "import unittest\n"
        "\n"
        "from calc import add, mul\n"
        "\n"
        "\n"
        "class CalcTest(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n"
        "\n"
        "    def test_add_negative(self):\n"
        "        self.assertEqual(add(-2, -3), -5)\n"
        "\n"
        "    def test_mul(self):\n"
        "        self.assertEqual(mul(4, 5), 20)\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

TS_STRINGS = {
    "README.md": "# ts-strings\nFixture with one intentionally failing function (`capitalize`).\n",
    "package.json": '{\n  "name": "ts-strings",\n  "private": true,\n  "type": "module"\n}\n',
    "src/strings.ts": (
        "export function capitalize(s: string): string {\n"
        "  return s.toLowerCase();\n"
        "}\n"
        "\n"
        "export function shout(s: string): string {\n"
        '  return s.toUpperCase() + "!";\n'
        "}\n"
    ),
    "test/strings.test.ts": (
        'import { test } from "node:test";\n'
        'import assert from "node:assert/strict";\n'
        'import { capitalize, shout } from "../src/strings.ts";\n'
        "\n"
        'test("capitalize upper-cases the first letter", () => {\n'
        '  assert.equal(capitalize("hello"), "Hello");\n'
        "});\n"
        "\n"
        'test("capitalize keeps the rest", () => {\n'
        '  assert.equal(capitalize("wORLD"), "WORLD");\n'
        "});\n"
        "\n"
        'test("shout", () => {\n'
        '  assert.equal(shout("hi"), "HI!");\n'
        "});\n"
    ),
}

# Tests inside this fixture try to reach host secrets and the network. They PASS only if
# the sandbox blocks every attempt, so a successful task demonstrates isolation.
PYTHON_PROBE = {
    "README.md": "# python-probe\nIsolation probe: tests pass only inside a locked-down sandbox.\n",
    "greet.py": "def greet(name):\n    return 'Hello ' + name.upper()\n",
    "test_isolation.py": (
        "import os\n"
        "import socket\n"
        "import unittest\n"
        "\n"
        "from greet import greet\n"
        "\n"
        "SECRET_ENV = ('DATABASE_URL', 'REDIS_URL', 'NODE_TOKEN_PEPPER', 'OPENAI_API_KEY',\n"
        "              'SPAREAGENT_ENROLLMENT_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'DOCKER_HOST')\n"
        "HOST_PATHS = ('/var/run/docker.sock', '/run/docker.sock', '/root/.ssh', '/home',\n"
        "              '/certs', '/var/lib/spareagent', '/var/lib/spareagent-worker',\n"
        "              '/fixtures', '/app')\n"
        "\n"
        "\n"
        "class IsolationTest(unittest.TestCase):\n"
        "    def test_greet(self):\n"
        "        self.assertEqual(greet('ada'), 'Hello Ada')\n"
        "\n"
        "    def test_no_secret_environment(self):\n"
        "        leaked = [k for k in SECRET_ENV if k in os.environ]\n"
        "        self.assertEqual(leaked, [])\n"
        "\n"
        "    def test_no_host_paths(self):\n"
        "        visible = [p for p in HOST_PATHS if os.path.exists(p)]\n"
        "        self.assertEqual(visible, [])\n"
        "\n"
        "    def test_network_blocked(self):\n"
        "        for host, port in (('1.1.1.1', 53), ('api', 8000), ('postgres', 5432)):\n"
        "            with self.assertRaises(OSError):\n"
        "                socket.create_connection((host, port), timeout=1)\n"
        "\n"
        "    def test_root_filesystem_read_only(self):\n"
        "        with self.assertRaises(OSError):\n"
        "            open('/etc/spareagent-escape', 'w')\n"
        "\n"
        "    def test_not_root(self):\n"
        "        self.assertNotEqual(os.getuid(), 0)\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

# Hostile fixtures for RepositoryManager tests (never in the executable catalog).
EVIL_SUBMODULE = {
    "README.md": "submodule fixture\n",
    ".gitmodules": '[submodule "lib"]\n\tpath = lib\n\turl = https://github.com/example/x\n',
}
EVIL_LFS = {
    "README.md": "lfs fixture\n",
    ".gitattributes": "*.bin filter=lfs diff=lfs merge=lfs -text\n",
    "data.bin": "version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n",
}
EVIL_LARGE = {
    "README.md": "large fixture\n",
    # deterministic, poorly compressible content (hex of a counter hash chain)
    "blob.txt": "".join(f"{(i * 2654435761) % 4294967296:08x}" for i in range(400_000)),
}
PYTHON_TIMEOUT = {
    "slow.py": "def answer():\n    return 41\n",
    "test_slow.py": (
        "import unittest\n\nfrom slow import answer\n\n\n"
        "class SlowTest(unittest.TestCase):\n"
        "    def test_forever(self):\n"
        "        while answer() != 42:\n"
        "            pass\n"
    ),
}

FIXTURES: dict[str, dict[str, str]] = {
    "python-calc": PYTHON_CALC,
    "python-test-target": {
        **PYTHON_CALC,
        "calc.py": PYTHON_CALC["calc.py"].replace("return a - b", "return a + b"),
    },
    "ts-strings": TS_STRINGS,
    "python-probe": PYTHON_PROBE,
    "python-timeout": PYTHON_TIMEOUT,
    "evil-submodule": EVIL_SUBMODULE,
    "evil-lfs": EVIL_LFS,
    "evil-large": EVIL_LARGE,
    "evil-symlink": {"README.md": "symlink fixture\n"},  # + symlink added in build
}


def _git(args: list[str], cwd: Path) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
        **_IDENTITY,
    }
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build_fixture(root: Path, name: str) -> str:
    """Build one bare fixture repository; returns its (deterministic) commit SHA."""
    files = FIXTURES[name]
    target = root / FIXTURE_OWNER / f"{name}.git"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        _git(["init", "-q", "--template=", "."], work)
        for rel, content in files.items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o644)
        _git(["add", "-A"], work)
        if name == "evil-symlink":
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=work,
                input="/etc/passwd",
                capture_output=True,
                text=True,
                check=True,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(work)},
            ).stdout.strip()
            _git(["update-index", "--add", "--cacheinfo", f"120000,{blob},secrets"], work)
        if name == "evil-submodule":
            _git(
                ["update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},lib"],
                work,
            )
        _git(["commit", "-q", "--no-verify", "-m", f"fixture: {name}"], work)
        sha = _git(["rev-parse", "HEAD"], work)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "-q", "--bare", "--template=", str(work), str(target)], Path(tmp))
    return sha


def build_all(root: Path) -> dict[str, str]:
    return {name: build_fixture(root, name) for name in FIXTURES}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/fixtures")
    for fixture, commit in build_all(out).items():
        print(f"{fixture} {commit}")
