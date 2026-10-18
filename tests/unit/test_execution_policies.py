import json

import pytest

from services.execution.catalog import CATALOG, lookup
from services.execution.patch_policy import PatchRejected, inspect_patch
from services.execution.repository_manager import RepositoryError, parse_github_url, validate_sha
from services.execution.sandbox import DockerSandbox, SandboxError, assert_safe_args, workspace_tar
from services.execution.test_report import ReportInvalid, build_report, validate_report

GOOD = (
    b"diff --git a/calc.py b/calc.py\n"
    b"index 1111111..2222222 100644\n"
    b"--- a/calc.py\n"
    b"+++ b/calc.py\n"
    b"@@ -1,2 +1,2 @@\n"
    b" def add(a, b):\n"
    b"-    return a - b\n"
    b"+    return a + b\n"
)


def test_valid_patch():
    summary = inspect_patch(GOOD, frozenset({"calc.py"}))
    assert summary.paths == ("calc.py",) and summary.additions == 1 and summary.deletions == 1


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        (GOOD.replace(b"a/calc.py b/calc.py", b"a/../etc/passwd b/../etc/passwd"), "forbidden"),
        (GOOD.replace(b"a/calc.py b/calc.py", b"a//etc/passwd b//etc/passwd"), "forbidden"),
        (GOOD.replace(b"+++ b/calc.py", b"+++ b/../../root/.ssh/authorized_keys"), "forbidden"),
        (GOOD.replace(b"a/calc.py b/calc.py", b"a/.git/config b/.git/config"), "forbidden"),
        (GOOD.replace(b"a/calc.py b/calc.py", b"a/.github/x.yml b/.github/x.yml"), "forbidden"),
        (GOOD.replace(b"a/calc.py b/calc.py", b"a/.gitattributes b/.gitattributes"), "forbidden"),
        (GOOD.replace(b"a/calc.py b/calc.py", b"a/calc.py b/other.py"), "renames"),
        (GOOD.replace(b"index 1111111..2222222 100644\n", b"new file mode 120000\n"), "mode"),
        (
            GOOD.replace(b"index 1111111..2222222 100644\n", b"old mode 100644\nnew mode 100755\n"),
            "mode",
        ),
        (GOOD + b"GIT binary patch\nliteral 0\n", "binary"),
        (b"not a patch", "not a git patch"),
        (b"diff --git a/x b/x\n" + b"+" * 300_000, "size"),
    ],
)
def test_rejected_patches(patch, reason):
    with pytest.raises(PatchRejected) as info:
        inspect_patch(patch, frozenset({"calc.py"}))
    assert reason in info.value.reason


def test_unexpected_file_rejected():
    other = GOOD.replace(b"calc.py", b"test_calc.py")
    with pytest.raises(PatchRejected, match="unexpected changes"):
        inspect_patch(other, frozenset({"calc.py"}))


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://github.com.evil.com/a/b",
        "https://evil.com/github.com/a/b",
        "https://user:pw@github.com/a/b",
        "https://github.com:443/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b/c",
        "https://github.com/-a/b",
        "https://github.com/a/..",
        "file:///etc/passwd",
        "https://127.0.0.1/a/b",
        "https://169.254.169.254/latest/meta-data",
        "ssh://git@github.com/a/b",
        "git@github.com:a/b.git",
    ],
)
def test_malicious_repository_urls_rejected(url):
    with pytest.raises(RepositoryError) as info:
        parse_github_url(url)
    assert info.value.code == "INVALID_URL"


def test_valid_repository_url():
    assert parse_github_url("https://github.com/octo-cat/repo.git") == ("octo-cat", "repo")
    with pytest.raises(RepositoryError):
        validate_sha("abc")


def test_catalog_lookup_requires_exact_pin():
    recipe = CATALOG[0]
    assert lookup(f"https://github.com/{recipe.repository}", recipe.commit_sha) is recipe
    assert lookup(f"https://github.com/{recipe.repository}.git", recipe.commit_sha.upper())
    assert lookup(f"https://github.com/{recipe.repository}", "0" * 40) is None
    assert lookup("https://github.com/other/repo", recipe.commit_sha) is None


def test_unittest_and_tap_parsing():
    ut = build_report(
        framework="unittest",
        base_commit="a" * 40,
        command=["x"],
        exit_code=1,
        stdout="",
        stderr="Ran 3 tests in 0.01s\n\nFAILED (failures=1, errors=1)\n",
        timed_out=False,
        duration_ms=5,
    )
    assert (ut["total"], ut["passed"], ut["failed"], ut["ok"]) == (3, 1, 2, False)
    tap = build_report(
        framework="node-tap",
        base_commit="a" * 40,
        command=["x"],
        exit_code=0,
        stdout="# tests 3\n# pass 3\n# fail 0\n",
        stderr="",
        timed_out=False,
        duration_ms=5,
    )
    assert (tap["passed"], tap["ok"]) == (3, True)
    validate_report(json.loads(json.dumps(tap)))
    empty = build_report(
        framework="unittest",
        base_commit="a" * 40,
        command=["x"],
        exit_code=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_ms=1,
    )
    assert empty["ok"] is False  # zero tests is never a pass


@pytest.mark.parametrize(
    "report",
    [
        [],
        {"schema_version": 1},
        {
            "schema_version": 2,
            "framework": "x",
            "base_commit": "a",
            "exit_code": 0,
            "passed": 1,
            "failed": 0,
            "total": 1,
            "ok": True,
            "timed_out": False,
        },
        {
            "schema_version": 1,
            "framework": "x",
            "base_commit": "a",
            "exit_code": 0,
            "passed": 5,
            "failed": 0,
            "total": 1,
            "ok": True,
            "timed_out": False,
        },
        {
            "schema_version": 1,
            "framework": "x",
            "base_commit": "a",
            "exit_code": 0,
            "passed": True,
            "failed": 0,
            "total": 1,
            "ok": True,
            "timed_out": False,
        },
    ],
)
def test_invalid_reports(report):
    with pytest.raises(ReportInvalid):
        validate_report(report)


def test_sandbox_argv_is_hardened():
    args = DockerSandbox().run_args("sa-sbx-test", 30)
    joined = " ".join(args)
    for flag in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--user 65534:65534",
        "no-new-privileges:true",
        "--pids-limit=",
        "--memory=",
        "--ipc=none",
    ):
        assert flag in joined
    assert "-v" not in args and "--volume" not in args and "--mount" not in args
    assert "docker.sock" not in joined and "--privileged" not in joined


@pytest.mark.parametrize(
    "extra",
    [
        ["--privileged"],
        ["-v", "/:/host"],
        ["--mount", "type=bind,src=/,dst=/h"],
        ["--network=host"],
        ["--security-opt=seccomp=unconfined"],
        ["--cap-add=SYS_ADMIN"],
        ["/var/run/docker.sock:/var/run/docker.sock"],
        ["--pid=host"],
        ["--device=/dev/kmsg"],
    ],
)
def test_unsafe_docker_arguments_refused(extra):
    args = DockerSandbox().run_args("x", 10)
    with pytest.raises(SandboxError):
        assert_safe_args([*args[:-3], *extra, *args[-3:]])


def test_workspace_tar_rejects_symlinks_and_excludes_git(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    import io
    import tarfile  # noqa: E401

    names = tarfile.open(fileobj=io.BytesIO(workspace_tar(tmp_path, max_bytes=10**6))).getnames()
    assert names == ["a.py"]
    (tmp_path / "escape").symlink_to("/etc/passwd")
    with pytest.raises(SandboxError, match="symlink"):
        workspace_tar(tmp_path, max_bytes=10**6)
    (tmp_path / "escape").unlink()
    (tmp_path / "big").write_bytes(b"0" * 2000)
    with pytest.raises(SandboxError, match="quota"):
        workspace_tar(tmp_path, max_bytes=1000)
