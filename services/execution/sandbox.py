"""Hardened container sandbox for running trusted test commands on untrusted code (Week 3).

Each execution gets a fresh, short-lived container:

* ``--network none`` (no host network, no egress), ``--ipc none``
* ``--read-only`` root filesystem; writable ``/workspace`` and ``/tmp`` are size-capped tmpfs
* non-root user ``65534:65534``, ``--cap-drop ALL``, ``no-new-privileges``, default seccomp
* CPU, memory (swap disabled), PID, open-file, file-size and core-dump limits
* **no bind mounts or volumes at all**: the workspace is streamed in as a tar archive via
  ``docker exec -i`` (so no host path, home directory, SSH key, cloud credential or the
  Docker socket can ever be mounted), and no host environment is inherited
* wall-clock timeout and cancellation both force-remove the container; the container's own
  ``sleep`` command bounds its lifetime even if the orchestrator dies; stale sandboxes are
  reaped by label.

This is defence in depth, NOT a hard multi-tenant boundary: containers share the host kernel.
Untrusted community execution stays disabled until a reviewed stronger runtime (gVisor/
Kata/microVM, rootless or userns-remapped daemon) is in place. See docs/DECISIONS.md D-36.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from services.observability import redact

log = logging.getLogger(__name__)

LABEL = "spareagent.sandbox"
SANDBOX_USER = "65534:65534"
FORBIDDEN_FLAGS = (
    "--privileged",
    "-v",
    "--volume",
    "--mount",
    "--volumes-from",
    "--device",
    "--cap-add",
    "--pid",
    "--userns",
    "--uts",
    "--security-opt=seccomp=unconfined",
    "--network=host",
    "docker.sock",
)
MAX_OUTPUT_BYTES = 64 * 1024


class SandboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class SandboxLimits:
    cpus: str = "1.0"
    memory_mb: int = 512
    pids: int = 128
    workspace_mb: int = 64
    tmp_mb: int = 16
    nofile: int = 256
    max_file_mb: int = 32
    timeout_seconds: float = 60.0


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool
    oom_killed: bool
    duration_ms: int
    container: str
    removed: bool = True
    notes: list[str] = field(default_factory=list)


def _truncate(data: bytes) -> str:
    text = data[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")
    if len(data) > MAX_OUTPUT_BYTES:
        text += "\n[output truncated]"
    return redact(text)


def workspace_tar(root: Path, *, max_bytes: int) -> bytes:
    """Archive a checkout (excluding .git) for streaming into the sandbox.

    Only regular files and directories are allowed; anything else (symlinks, devices, FIFOs)
    aborts. Ownership and permissions are normalised."""
    buf = io.BytesIO()
    total = 0
    root = root.resolve()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(dirnames) + sorted(filenames):
                full = Path(dirpath) / name
                rel = full.relative_to(root).as_posix()
                st = os.lstat(full)
                info = tarfile.TarInfo(rel)
                info.uid = info.gid = 65534
                info.uname = info.gname = "nobody"
                info.mtime = 0
                if full.is_symlink():
                    raise SandboxError("UNSAFE_WORKSPACE", f"symlink in workspace: {rel}")
                if full.is_dir():
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                elif full.is_file():
                    total += st.st_size
                    if total > max_bytes:
                        raise SandboxError("WORKSPACE_TOO_LARGE", "workspace exceeds quota")
                    info.size = st.st_size
                    info.mode = 0o644
                    with open(full, "rb") as fh:
                        tar.addfile(info, fh)
                else:
                    raise SandboxError("UNSAFE_WORKSPACE", f"special file in workspace: {rel}")
    return buf.getvalue()


class DockerSandbox:
    def __init__(
        self,
        image: str = "spareagent-sandbox:dev",
        limits: SandboxLimits | None = None,
        *,
        docker_bin: str = "docker",
        require_userns: bool = False,
    ) -> None:
        self.image = image
        self.limits = limits or SandboxLimits()
        self.docker_bin = docker_bin
        self.require_userns = require_userns
        # The Docker CLI gets only what it needs to reach the daemon; never our secrets.
        self._cli_env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "HOME")
        }
        self._active: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- docker plumbing
    def _docker(
        self, *args: str, input_bytes: bytes | None = None, timeout: float = 30
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.docker_bin, *args],
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
                env=self._cli_env,
            )
        except FileNotFoundError as exc:
            raise SandboxError("DOCKER_UNAVAILABLE", "docker CLI not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError("DOCKER_TIMEOUT", f"docker {args[0]} timed out") from exc

    def run_args(self, name: str, lifetime_seconds: int) -> list[str]:
        """The exact `docker run` argv. Pure and unit-tested against FORBIDDEN_FLAGS."""
        lim = self.limits
        args = [
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            f"{LABEL}=1",
            "--label",
            f"{LABEL}.created={int(time.time())}",
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--user",
            SANDBOX_USER,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            f"--pids-limit={lim.pids}",
            f"--memory={lim.memory_mb}m",
            f"--memory-swap={lim.memory_mb}m",
            f"--cpus={lim.cpus}",
            "--ulimit",
            f"nofile={lim.nofile}:{lim.nofile}",
            "--ulimit",
            f"fsize={lim.max_file_mb * 1024 * 1024}",
            "--ulimit",
            "core=0",
            "--tmpfs",
            f"/workspace:rw,exec,nosuid,nodev,size={lim.workspace_mb}m,uid=65534,gid=65534,mode=0755",
            "--tmpfs",
            # container-internal tmpfs, not a host temp path
            f"/tmp:rw,noexec,nosuid,nodev,size={lim.tmp_mb}m,uid=65534,gid=65534,mode=1777",  # noqa: S108
            "--hostname",
            "sandbox",
            "--log-driver=none",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--workdir",
            "/workspace",
            "--stop-timeout=1",
            self.image,
            "sleep",
            str(lifetime_seconds),
        ]
        assert_safe_args(args)
        return args

    def _remove(self, name: str) -> bool:
        proc = self._docker("rm", "--force", "--volumes", name, timeout=30)
        if proc.returncode != 0 and b"No such container" not in proc.stderr:
            log.error("sandbox removal failed", extra={"container": name})
            return False
        return True

    # ---------------------------------------------------------------- public API
    def health(self) -> dict:
        """Explicit health check: daemon reachable, image present, hardening effective."""
        checks: dict[str, object] = {}
        info = self._docker("info", "--format", "{{json .SecurityOptions}}")
        if info.returncode != 0:
            raise SandboxError("DOCKER_UNAVAILABLE", "docker daemon not reachable")
        sec = info.stdout.decode().strip()
        checks["rootless"] = "rootless" in sec
        checks["userns_remap"] = "userns" in sec
        checks["seccomp"] = "seccomp" in sec
        if self.require_userns and not (checks["rootless"] or checks["userns_remap"]):
            raise SandboxError("USERNS_REQUIRED", "daemon is neither rootless nor userns-remapped")
        if self._docker("image", "inspect", self.image).returncode != 0:
            raise SandboxError("IMAGE_MISSING", f"sandbox image {self.image} not found")
        probe = (
            "import os,socket,json\n"
            "r={'uid':os.getuid()}\n"
            "try:\n socket.create_connection(('1.1.1.1',53),timeout=1);r['net']=True\n"
            "except OSError:\n r['net']=False\n"
            "try:\n open('/probe','w');r['rootfs_writable']=True\n"
            "except OSError:\n r['rootfs_writable']=False\n"
            "r['docker_sock']=os.path.exists('/var/run/docker.sock')\n"
            "st=dict(l.split(':',1) for l in open('/proc/self/status'))\n"
            "r['nnp']=st['NoNewPrivs'].strip()\n"
            "r['caps']=st['CapEff'].strip()\n"
            "print(json.dumps(r))\n"
        )
        result = self.execute_script(probe, timeout=20)
        try:
            observed = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise SandboxError("UNHEALTHY", f"probe failed: {result.stderr[:200]}") from exc
        checks.update(observed)
        ok = (
            observed["uid"] != 0
            and observed["net"] is False
            and observed["rootfs_writable"] is False
            and observed["docker_sock"] is False
            and observed["nnp"] == "1"
            and int(observed["caps"], 16) == 0
            and result.removed
        )
        if not ok:
            raise SandboxError("UNHEALTHY", f"sandbox hardening not effective: {checks}")
        return {"ok": True, "image": self.image, "checks": checks}

    def execute_script(self, script: str, timeout: float = 20) -> SandboxResult:
        """Run a trusted Python snippet in an empty sandbox (health checks, tests)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "probe.py").write_text(script)
            return self.execute(Path(tmp), ["python3", "probe.py"], timeout=timeout)

    def cancel(self, run_id: str) -> None:
        with self._lock:
            event = self._active.get(run_id)
        if event:
            event.set()

    def execute(
        self,
        workspace: Path,
        command: list[str],
        *,
        run_id: str | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SandboxResult:
        """Copy `workspace` into a fresh sandbox, run trusted argv `command`, always clean up."""
        if not command or not all(isinstance(c, str) for c in command):
            raise SandboxError("INVALID_COMMAND", "command must be a non-empty argv list")
        timeout = min(timeout or self.limits.timeout_seconds, self.limits.timeout_seconds)
        run_id = run_id or secrets.token_hex(6)
        name = f"sa-sbx-{secrets.token_hex(6)}"
        cancel = cancel_event or threading.Event()
        with self._lock:
            self._active[run_id] = cancel
        archive = workspace_tar(workspace, max_bytes=self.limits.workspace_mb * 1024 * 1024)
        started = time.monotonic()
        created = False
        try:
            run = self._docker(*self.run_args(name, int(timeout) + 30), timeout=60)
            if run.returncode != 0:
                raise SandboxError("START_FAILED", run.stderr.decode()[-300:])
            created = True
            load = self._docker(
                "exec",
                "-i",
                name,
                "tar",
                "-x",
                "-C",
                "/workspace",
                "-f",
                "-",
                input_bytes=archive,
                timeout=60,
            )
            if load.returncode != 0:
                raise SandboxError("WORKSPACE_LOAD_FAILED", load.stderr.decode()[-300:])
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                [self.docker_bin, "exec", "--workdir", "/workspace", name, *command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=self._cli_env,
            )
            timed_out = cancelled = False
            out_chunks: dict[str, bytes] = {}

            def _reader(stream, key):
                out_chunks[key] = stream.read(MAX_OUTPUT_BYTES + 1)
                stream.read()  # drain so the child never blocks on a full pipe

            readers = [
                threading.Thread(target=_reader, args=(proc.stdout, "out"), daemon=True),
                threading.Thread(target=_reader, args=(proc.stderr, "err"), daemon=True),
            ]
            for t in readers:
                t.start()
            deadline = started + timeout
            while proc.poll() is None:
                if cancel.is_set():
                    cancelled = True
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    break
                time.sleep(0.05)
            if timed_out or cancelled:
                self._docker("kill", name, timeout=15)
                created = not self._remove(name)
                proc.kill()
            proc.wait(timeout=15)
            for t in readers:
                t.join(timeout=5)
            oom = False
            if not (timed_out or cancelled):
                state = self._docker("inspect", "--format", "{{.State.OOMKilled}}", name)
                oom = state.stdout.strip() == b"true"
            return SandboxResult(
                exit_code=proc.returncode if not (timed_out or cancelled) else -1,
                stdout=_truncate(out_chunks.get("out", b"")),
                stderr=_truncate(out_chunks.get("err", b"")),
                timed_out=timed_out,
                cancelled=cancelled,
                oom_killed=oom or proc.returncode == 137,
                duration_ms=int((time.monotonic() - started) * 1000),
                container=name,
            )
        finally:
            removed = True
            if created:
                removed = self._remove(name)
            with self._lock:
                self._active.pop(run_id, None)
            if not removed:  # pragma: no cover - surfaced loudly, reaper retries
                log.error("sandbox container left behind", extra={"container": name})

    def list_containers(self) -> list[str]:
        proc = self._docker("ps", "-a", "--filter", f"label={LABEL}=1", "--format", "{{.Names}}")
        return [n for n in proc.stdout.decode().split() if n]

    def reap_stale(self, max_age_seconds: float = 600) -> int:
        """Remove sandboxes left over by a crashed orchestrator."""
        proc = self._docker(
            "ps",
            "-a",
            "--filter",
            f"label={LABEL}=1",
            "--format",
            '{{.Names}} {{.Label "' + LABEL + '.created"}}',
        )
        removed = 0
        now = time.time()
        for line in proc.stdout.decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit() and now - int(parts[1]) > max_age_seconds:
                removed += int(self._remove(parts[0]))
        return removed


def assert_safe_args(args: list[str]) -> None:
    for arg in args:
        for bad in FORBIDDEN_FLAGS:
            if arg == bad or arg.startswith(bad + "=") or (bad == "docker.sock" and bad in arg):
                raise SandboxError("UNSAFE_CONFIG", f"forbidden docker argument: {arg}")
    if "--network=none" not in args or "--read-only" not in args:
        raise SandboxError("UNSAFE_CONFIG", "sandbox must run without network and read-only")
