"""Safe retrieval of public GitHub repositories at a pinned commit (Week 3).

Threat model: the repository content is attacker-controlled. Nothing in it may influence which
host-side commands run, which hosts are contacted, or which credentials are used.

* Only ``https://github.com/<owner>/<repo>[.git]`` with strict owner/name validation.
* Fetch exactly one commit (``--depth=1``) by full SHA; verify FETCH_HEAD and HEAD == SHA.
* Git runs with an empty HOME, no system/global config, hooks disabled, no credential
  helpers, no askpass/terminal prompts (auth-required repos fail), no redirects, HTTPS-only
  protocol allow-list, no submodule recursion, LFS smudge disabled, fsck on transfer.
* The fetched tree is inspected *before* checkout: submodules (gitlinks/.gitmodules), LFS
  attributes, symlinks, unsafe paths, file-count and byte limits are rejected.
* Fetch size and wall-clock time are enforced while git runs (killed on breach).
* Each checkout lives in a fresh private temporary directory and is removed on cleanup.

Local development/tests may map ``github.com/spareagent-fixtures/*`` to deterministic local
mirrors (``mirror_root``); this is an explicit opt-in transport substitution (D-33).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._@+/-]+$")
FIXTURE_OWNER = "spareagent-fixtures"

SAFE_GIT_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.symlinks=false",
    "core.autocrlf=false",
    "core.askPass=",
    "credential.helper=",
    "http.followRedirects=false",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "submodule.recurse=false",
    "fetch.recurseSubmodules=false",
    "transfer.fsckObjects=true",
    "fetch.fsckObjects=true",
    "filter.lfs.smudge=",
    "filter.lfs.process=",
    "filter.lfs.required=false",
    "advice.detachedHead=false",
    "init.defaultBranch=main",
    "gc.auto=0",
)


class RepositoryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RepoLimits:
    max_fetch_bytes: int = 50 * 1024 * 1024
    max_tree_bytes: int = 20 * 1024 * 1024
    max_files: int = 2000
    fetch_timeout_seconds: float = 60.0
    git_timeout_seconds: float = 30.0


@dataclass
class Checkout:
    path: Path
    commit_sha: str
    owner: str
    repo: str
    file_count: int
    tree_bytes: int
    _root: Path = field(repr=False)
    _env: dict[str, str] = field(repr=False, default_factory=dict)

    def git(self, *args: str, input_text: str | None = None, timeout: float = 30) -> str:
        return run_git(["-C", str(self.path), *args], self._env, timeout, input_text=input_text)

    def cleanup(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def __enter__(self) -> Checkout:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()


def parse_github_url(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or len(url) > 200:
        raise RepositoryError("INVALID_URL", "repository URL must be a string <= 200 chars")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "github.com"
        or parts.netloc != "github.com"  # no userinfo, no port
        or parts.query
        or parts.fragment
    ):
        raise RepositoryError("INVALID_URL", "only https://github.com/<owner>/<repo> is allowed")
    segments = parts.path.strip("/").split("/")
    if len(segments) != 2:
        raise RepositoryError("INVALID_URL", "URL must be https://github.com/<owner>/<repo>")
    owner, repo = segments
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _OWNER.fullmatch(owner) or not _REPO.fullmatch(repo) or repo in (".", ".."):
        raise RepositoryError("INVALID_URL", "invalid GitHub owner or repository name")
    return owner, repo


def validate_sha(sha: str) -> str:
    if not isinstance(sha, str) or not _SHA.fullmatch(sha.lower()):
        raise RepositoryError("INVALID_SHA", "commit must be a full 40-character hex SHA")
    return sha.lower()


def safe_git_env(home: Path, *, allow_file: bool = False) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_ALLOW_PROTOCOL": "file" if allow_file else "https",
        "GIT_PROTOCOL_FROM_USER": "0",
        "LC_ALL": "C",
    }


def run_git(
    args: list[str],
    env: dict[str, str],
    timeout: float,
    *,
    input_text: str | None = None,
    extra_config: tuple[str, ...] = (),
) -> str:
    cmd = ["git"]
    for item in (*SAFE_GIT_CONFIG, *extra_config):
        cmd += ["-c", item]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [*cmd, *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(
            "TIMEOUT", f"git {args[2] if len(args) > 2 else ''} timed out"
        ) from exc
    if proc.returncode != 0:
        raise RepositoryError("GIT_ERROR", proc.stderr.strip()[-500:] or "git failed")
    return proc.stdout


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except FileNotFoundError:
                pass
    return total


def _classify_fetch_error(stderr: str) -> RepositoryError:
    text = stderr.lower()
    if any(
        s in text
        for s in (
            "could not read username",
            "authentication failed",
            "terminal prompts disabled",
            "401",
            "403",
            "repository not found",
        )
    ):
        return RepositoryError("AUTH_REQUIRED", "repository is private, missing or needs auth")
    if "redirect" in text:
        return RepositoryError("REDIRECT_REFUSED", "repository URL redirected; refusing")
    if any(
        s in text
        for s in ("not our ref", "couldn't find remote ref", "no such remote ref", "unadvertised")
    ):
        return RepositoryError("COMMIT_NOT_FOUND", "pinned commit is not available")
    if "does not appear to be a git repository" in text or "not found" in text:
        return RepositoryError("COMMIT_NOT_FOUND", "repository or commit not found")
    return RepositoryError("FETCH_FAILED", stderr.strip()[-300:] or "fetch failed")


def check_tree_path(path: str) -> None:
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or not _SAFE_PATH.fullmatch(path)
        or any(p in ("", ".", "..") for p in parts)
        or any(p.lower() == ".git" for p in parts)
    ):
        raise RepositoryError("UNSAFE_PATH", f"unsafe path in repository: {path[:100]!r}")


class RepositoryManager:
    def __init__(
        self,
        limits: RepoLimits | None = None,
        *,
        mirror_root: Path | None = None,
        workdir_root: Path | None = None,
    ) -> None:
        self.limits = limits or RepoLimits()
        self.mirror_root = Path(mirror_root) if mirror_root else None
        self.workdir_root = Path(workdir_root) if workdir_root else None

    def _source(self, owner: str, repo: str) -> tuple[str, bool]:
        if self.mirror_root is not None and owner == FIXTURE_OWNER:
            mirror = (self.mirror_root / FIXTURE_OWNER / f"{repo}.git").resolve()
            if not mirror.is_relative_to(self.mirror_root.resolve()) or not mirror.is_dir():
                raise RepositoryError("COMMIT_NOT_FOUND", "fixture repository not found")
            return mirror.as_uri(), True
        return f"https://github.com/{owner}/{repo}.git", False

    def _fetch(self, repo_dir: Path, source: str, sha: str, env: dict, extra: tuple) -> None:
        cmd = ["git"]
        for item in (*SAFE_GIT_CONFIG, *extra):
            cmd += ["-c", item]
        cmd += [
            "-C",
            str(repo_dir),
            "fetch",
            "--quiet",
            "--depth=1",
            "--no-tags",
            "--no-recurse-submodules",
            source,
            sha,
        ]
        with tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                cmd, env=env, stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL
            )
            deadline = time.monotonic() + self.limits.fetch_timeout_seconds
            git_dir = repo_dir / ".git"
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    raise RepositoryError("TIMEOUT", "repository fetch timed out")
                if _dir_size(git_dir) > self.limits.max_fetch_bytes:
                    proc.kill()
                    proc.wait()
                    raise RepositoryError("TOO_LARGE", "repository exceeds the fetch size limit")
                time.sleep(0.1)
            if _dir_size(git_dir) > self.limits.max_fetch_bytes:
                raise RepositoryError("TOO_LARGE", "repository exceeds the fetch size limit")
            if proc.returncode != 0:
                err.seek(0)
                raise _classify_fetch_error(err.read().decode("utf-8", "replace"))

    def _inspect_tree(self, repo_dir: Path, env: dict, extra: tuple) -> tuple[int, int]:
        listing = run_git(
            ["-C", str(repo_dir), "ls-tree", "-r", "-l", "-z", "--full-tree", "FETCH_HEAD"],
            env,
            self.limits.git_timeout_seconds,
            extra_config=extra,
        )
        files, total = 0, 0
        for entry in filter(None, listing.split("\0")):
            meta, path = entry.split("\t", 1)
            mode, otype, blob, size = meta.split()
            check_tree_path(path)
            if mode == "160000" or otype == "commit" or path == ".gitmodules":
                raise RepositoryError("SUBMODULES_UNSUPPORTED", "submodules are not supported")
            if mode == "120000":
                raise RepositoryError("SYMLINKS_UNSUPPORTED", "symlinks are not supported")
            if mode not in ("100644", "100755"):
                raise RepositoryError("UNSAFE_PATH", f"unsupported file mode {mode}")
            if path.rsplit("/", 1)[-1] == ".gitattributes":
                attrs = run_git(
                    ["-C", str(repo_dir), "cat-file", "blob", blob],
                    env,
                    self.limits.git_timeout_seconds,
                    extra_config=extra,
                )
                if "filter=lfs" in attrs.replace(" ", ""):
                    raise RepositoryError("LFS_UNSUPPORTED", "Git LFS is not supported")
            files += 1
            total += int(size)
            if files > self.limits.max_files or total > self.limits.max_tree_bytes:
                raise RepositoryError("TOO_LARGE", "repository exceeds file or size limits")
        return files, total

    def checkout(self, url: str, commit_sha: str) -> Checkout:
        owner, repo = parse_github_url(url)
        sha = validate_sha(commit_sha)
        source, is_file = self._source(owner, repo)
        root = Path(tempfile.mkdtemp(prefix="sa-repo-", dir=self.workdir_root))
        os.chmod(root, 0o700)
        try:
            home = root / "home"
            home.mkdir(mode=0o700)
            repo_dir = root / "src"
            env = safe_git_env(home, allow_file=is_file)
            extra = ("protocol.file.allow=always",) if is_file else ()
            run_git(["init", "-q", "--template=", str(repo_dir)], env, 10, extra_config=extra)
            self._fetch(repo_dir, source, sha, env, extra)
            fetched = run_git(
                ["-C", str(repo_dir), "rev-parse", "FETCH_HEAD^{commit}"], env, 10
            ).strip()
            if fetched != sha:
                raise RepositoryError("HEAD_MISMATCH", "fetched commit differs from pinned SHA")
            files, size = self._inspect_tree(repo_dir, env, extra)
            run_git(
                ["-C", str(repo_dir), "checkout", "-q", "--detach", "FETCH_HEAD"],
                env,
                self.limits.git_timeout_seconds,
                extra_config=extra,
            )
            head = run_git(["-C", str(repo_dir), "rev-parse", "HEAD"], env, 10).strip()
            if head != sha:
                raise RepositoryError("HEAD_MISMATCH", "checked-out HEAD differs from pinned SHA")
            return Checkout(repo_dir, sha, owner, repo, files, size, root, env)
        except RepositoryError:
            shutil.rmtree(root, ignore_errors=True)
            raise
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(root, ignore_errors=True)
            raise RepositoryError("FETCH_FAILED", type(exc).__name__) from exc
