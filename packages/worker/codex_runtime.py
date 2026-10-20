"""Owner-controlled Codex runtime: drives the official Codex CLI (`codex exec --json`).

Runs on the account owner's device only. The child process:

* inherits an allow-listed environment with no API keys / base URLs (see
  `subscription_auth.sanitized_env`), so it can only use the owner's own saved login;
* is started with `-c forced_login_method="chatgpt"`, Codex's own documented control that
  makes it exit instead of using any non-ChatGPT credential;
* runs in its own process group so cancellation and timeouts kill the whole tree;
* has its JSONL event stream parsed into typed events; raw text is redacted before it is
  surfaced anywhere, and credential files are never read.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from packages.common.observability import redact
from packages.worker.subscription_auth import sanitized_env

log = logging.getLogger("spareagent.worker.codex")

SandboxMode = Literal["read-only", "workspace-write"]
MAX_LINE_BYTES = 1024 * 1024
KILL_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class CodexUsage:
    """Token counts as reported by Codex for one turn. Not a quota or remaining balance."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0


@dataclass(frozen=True)
class CodexEvent:
    kind: Literal["thread", "progress", "message", "completed", "failed"]
    message: str = ""
    thread_id: str | None = None
    usage: CodexUsage | None = None
    error_class: str | None = None  # AUTH | USAGE_LIMIT | RUNTIME
    reset_hint: str | None = None


@dataclass
class CodexRunResult:
    thread_id: str | None = None
    final_message: str = ""
    usage: CodexUsage = field(default_factory=CodexUsage)
    error_class: str | None = None
    error_message: str = ""
    reset_hint: str | None = None
    returncode: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    completed_turns: int = 0

    @property
    def succeeded(self) -> bool:
        return (
            self.error_class is None
            and not self.cancelled
            and not self.timed_out
            and self.returncode == 0
            and self.completed_turns > 0
        )


_AUTH_ERROR = re.compile(
    r"(?i)(\b401\b|unauthori[sz]ed|not\s+logged\s+in|log\s*in\s+again|logged\s+out|"
    r"(access|refresh)\s+token.*(expired|revoked|invalid)|session\s+expired|"
    r"forced_login_method)"
)
_LIMIT_ERROR = re.compile(
    r"(?i)(usage\s+limit|rate[\s_-]?limit|\b429\b|too\s+many\s+requests|quota|"
    r"limit\s+reached|insufficient\s+credits)"
)
_RESET_HINT = re.compile(r"(?i)((?:try\s+again|resets?)\s+(?:in|at|after)\s+[^.\n]{1,60})")


def classify_error(message: str) -> tuple[str, str | None]:
    if _AUTH_ERROR.search(message):
        return "AUTH", None
    if _LIMIT_ERROR.search(message):
        hint = _RESET_HINT.search(message)
        return "USAGE_LIMIT", (redact(hint.group(1))[:80] if hint else None)
    return "RUNTIME", None


def _int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def parse_event(raw: dict[str, Any]) -> CodexEvent | None:
    """Map one `codex exec --json` line to a CodexEvent. Unknown types are ignored."""
    etype = raw.get("type")
    if etype == "thread.started":
        tid = raw.get("thread_id")
        return CodexEvent("thread", thread_id=redact(str(tid))[:128] if tid else None)
    if etype == "turn.started":
        return CodexEvent("progress", "Codex turn started")
    if etype == "turn.completed":
        u = raw.get("usage")
        if not isinstance(u, dict) or any(
            type(u.get(k)) is not int or u[k] < 0 for k in ("input_tokens", "output_tokens")
        ):
            return CodexEvent("failed", "Invalid Codex usage event", error_class="RUNTIME")
        return CodexEvent(
            "completed",
            usage=CodexUsage(
                _int(u.get("input_tokens")),
                _int(u.get("cached_input_tokens")),
                _int(u.get("output_tokens")),
                _int(u.get("reasoning_output_tokens")),
            ),
        )
    if etype in ("turn.failed", "error"):
        err = raw.get("error")
        message = (err.get("message") if isinstance(err, dict) else None) or raw.get("message")
        message = str(message or etype)
        error_class, reset = classify_error(message)
        return CodexEvent(
            "failed", redact(message)[:500], error_class=error_class, reset_hint=reset
        )
    if etype in ("item.started", "item.completed"):
        item = raw.get("item")
        if not isinstance(item, dict):
            return None
        itype = str(item.get("type") or item.get("item_type") or "item")
        if etype == "item.completed" and itype in ("agent_message", "assistant_message"):
            return CodexEvent("message", redact(str(item.get("text") or ""))[:20000])
        # Progress carries the item kind only: commands/paths can contain sensitive data.
        verb = "started" if etype == "item.started" else "completed"
        return CodexEvent("progress", f"{itype} {verb}")
    return None


@dataclass(frozen=True)
class CodexRuntimeConfig:
    codex_bin: str = "codex"
    sandbox: SandboxMode = "read-only"
    model: str | None = None
    extra_env: dict[str, str] | None = None  # test hook; still passed through sanitization
    # The owner's concurrency setting (1-3): runs beyond it are refused, never queued here.
    max_parallel: int = 1


def _toml_path(path: str) -> str:
    if not path.startswith("/") or any(c in path for c in '"\\\n\r\t') or "/../" in path:
        raise ValueError(f"unsafe tool path {path!r}")
    return f'"{path}"="read"'


def shared_permission_args(sandbox: SandboxMode, read_roots: tuple[str, ...] = ()) -> list[str]:
    """`read_roots` are extra read-only directories (pinned tool environments)."""
    access = "read" if sandbox == "read-only" else "write"
    extra = "".join("," + _toml_path(p) for p in read_roots)
    profile = (
        'permissions={spareagent={filesystem={":minimal"="read",'
        f'":workspace_roots"="{access}"{extra}'
        "},network={enabled=false}}}"
    )
    return [
        "-c",
        profile,
        "-c",
        'default_permissions="spareagent"',
        "-c",
        'approval_policy="never"',
        "-c",
        'model_provider="openai"',
        "-c",
        "allow_login_shell=false",
        "-c",
        "agents.enabled=false",
        "-c",
        "mcp_servers={}",
        "-c",
        "apps._default.enabled=false",
        "-c",
        'web_search="disabled"',
    ]


class CodexRuntime:
    """One owner, one device. Holds no credentials: Codex uses its own saved login."""

    def __init__(self, config: CodexRuntimeConfig | None = None) -> None:
        self.config = config or CodexRuntimeConfig()
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._running = 0

    def argv(
        self,
        sandbox: SandboxMode | None = None,
        workdir: Path | None = None,
        read_roots: tuple[str, ...] = (),
    ) -> list[str]:
        cfg = self.config
        args = [
            cfg.codex_bin,
            "-c",
            'forced_login_method="chatgpt"',
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
        ]
        if sandbox is not None and workdir is not None:
            # Shared prompts get filesystem reads scoped to their checkout, not the owner's
            # home. The Codex parent still uses its own login; its tools cannot read it.
            args += shared_permission_args(sandbox, read_roots)
        else:
            args += ["--sandbox", cfg.sandbox]
        if cfg.model:
            args += ["--model", cfg.model]
        args.append("-")  # prompt is read from stdin, never interpolated into argv
        return args

    def _env(self, path_dirs: tuple[str, ...] = ()) -> dict[str, str]:
        base = dict(os.environ)
        if self.config.extra_env:
            base.update(self.config.extra_env)
        env = sanitized_env(base)
        if path_dirs:
            env["PATH"] = os.pathsep.join([*path_dirs, env.get("PATH", "")])
        return env

    async def run(
        self,
        run_id: str,
        prompt: str,
        workdir: Path,
        *,
        timeout_seconds: float,
        sandbox: SandboxMode | None = None,
        read_roots: tuple[str, ...] = (),
        path_dirs: tuple[str, ...] = (),
    ) -> AsyncIterator[CodexEvent | CodexRunResult]:
        """Yields CodexEvents, then exactly one CodexRunResult. `sandbox` can only choose
        between Codex's read-only and workspace-write modes (never full access)."""
        result = CodexRunResult()
        if self._running >= max(1, self.config.max_parallel):
            result.error_class, result.error_message = "RUNTIME", "Codex runtime is already busy"
            yield result
            return
        if not 0 < timeout_seconds <= 3600:
            result.error_class, result.error_message = "RUNTIME", "Invalid task timeout"
            yield result
            return
        self._running += 1
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.argv(sandbox, workdir, read_roots),
                cwd=str(workdir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env(path_dirs),
                start_new_session=True,
                limit=MAX_LINE_BYTES,
            )
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            self._running -= 1
            result.error_class = "RUNTIME"
            result.error_message = f"codex CLI not runnable: {type(exc).__name__}"
            yield result
            return
        self._procs[run_id] = proc
        stderr_task = asyncio.create_task(self._drain(proc.stderr))
        try:
            assert proc.stdin is not None and proc.stdout is not None  # noqa: S101
            async with asyncio.timeout(timeout_seconds):
                proc.stdin.write(prompt.encode())
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await proc.stdin.drain()
                proc.stdin.close()
                async for line in proc.stdout:
                    event = self._parse_line(line)
                    if event is None:
                        continue
                    self._fold(result, event)
                    yield event
                result.returncode = await proc.wait()
        except TimeoutError:
            result.timed_out = True
            await self._kill(proc)
        except asyncio.CancelledError:
            await self._kill(proc)
            raise
        except Exception as exc:  # malformed/oversized streams fail closed
            result.error_class = "RUNTIME"
            result.error_message = f"Invalid Codex stream: {type(exc).__name__}"
            await self._kill(proc)
        finally:
            await self._kill(proc)

            self._procs.pop(run_id, None)
            self._running -= 1
            stderr = await self._finish(stderr_task)
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            result.cancelled = True
        if result.returncode not in (0, None) and result.error_class is None:
            error_class, reset = classify_error(stderr)
            result.error_class = error_class
            result.reset_hint = reset
            result.error_message = (
                redact(stderr.strip())[-500:] or f"codex exited {result.returncode}"
            )
        yield result

    async def cancel(self, run_id: str) -> None:
        proc = self._procs.get(run_id)
        if proc is not None:
            self._cancelled.add(run_id)
            await self._kill(proc)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _parse_line(line: bytes) -> CodexEvent | None:
        try:
            raw = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return None
        return parse_event(raw) if isinstance(raw, dict) else None

    @staticmethod
    def _fold(result: CodexRunResult, event: CodexEvent) -> None:
        if event.kind == "thread":
            result.thread_id = event.thread_id
        elif event.kind == "message":
            result.final_message = event.message
        elif event.kind == "completed" and event.usage is not None:
            result.completed_turns += 1
            u, n = result.usage, event.usage
            result.usage = CodexUsage(
                u.input_tokens + n.input_tokens,
                u.cached_input_tokens + n.cached_input_tokens,
                u.output_tokens + n.output_tokens,
                u.reasoning_output_tokens + n.reasoning_output_tokens,
            )
        elif event.kind == "failed":
            result.error_class = event.error_class
            result.error_message = event.message
            result.reset_hint = event.reset_hint

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        data = await stream.read(64 * 1024)
        while await stream.read(64 * 1024):
            pass  # keep the pipe from blocking; only the head is kept
        return data.decode(errors="replace")

    @staticmethod
    async def _finish(task: asyncio.Task[str]) -> str:
        try:
            return await asyncio.wait_for(task, KILL_GRACE_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            return ""

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        # Signal the group even after its leader exits: background descendants may remain.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                await asyncio.wait_for(proc.wait(), KILL_GRACE_SECONDS)
            except TimeoutError:
                continue
            # The leader exited; immediately reap any surviving descendants with SIGKILL.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), KILL_GRACE_SECONDS)
