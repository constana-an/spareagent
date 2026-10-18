"""Local ChatGPT-subscription authentication checks for the owner's Codex runtime.

Everything here runs on the account owner's device. SpareAgent never reads, copies,
uploads or forwards Codex credentials: it only asks the official Codex CLI which login
method is active (`codex login status`) and builds a child-process environment from which
every API-billing credential has been removed.

Fail closed: anything other than a positively identified ChatGPT login is refused.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from enum import StrEnum

# Environment variables that would switch Codex (or a child tool) to separately billed
# API usage, or inject credentials from outside the owner's own Codex login. They are
# never passed to the Codex child process (no API-key substitution, no paid fallback).
FORBIDDEN_ENV = frozenset(
    {
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_ORG_ID",
        "OPENAI_ORGANIZATION",
        "OPENAI_PROJECT_ID",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AZURE_OPENAI_API_KEY",
    }
)
# The only variables the child inherits. CODEX_HOME points at the owner's own login.
PASSTHROUGH_ENV = ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "SHELL")

STATUS_TIMEOUT_SECONDS = 15.0


class AuthMethod(StrEnum):
    CHATGPT = "CHATGPT"
    API_KEY = "API_KEY"
    NOT_LOGGED_IN = "NOT_LOGGED_IN"
    UNAVAILABLE = "UNAVAILABLE"  # codex binary missing / crashed / timed out
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AuthStatus:
    method: AuthMethod
    detail: str  # redacted, human readable; never contains credential material

    @property
    def eligible(self) -> bool:
        return self.method == AuthMethod.CHATGPT


class SubscriptionAuthError(Exception):
    """Valid ChatGPT subscription authentication is not available. Never retried by
    switching credentials or accounts."""

    def __init__(self, status: AuthStatus) -> None:
        super().__init__(f"ChatGPT subscription login unavailable: {status.method.value}")
        self.status = status


_NOT_LOGGED_IN = re.compile(r"(?i)\bnot\s+logged\s+in\b")
_API_KEY = re.compile(r"(?i)\bapi[\s_-]?key\b")
_CHATGPT = re.compile(r"(?i)\blogged\s+in\s+(?:using|with|via)\s+chatgpt\b")


def classify_login_status(output: str, returncode: int) -> AuthStatus:
    """Classify `codex login status` output. Only an explicit ChatGPT login is eligible;
    an API-key login is refused even if a ChatGPT login also exists."""
    text = "Login status not recognized"
    if _NOT_LOGGED_IN.search(output):
        return AuthStatus(AuthMethod.NOT_LOGGED_IN, text)
    if _API_KEY.search(output):
        return AuthStatus(AuthMethod.API_KEY, "Logged in with an API key (refused)")
    if returncode == 0 and _CHATGPT.search(output):
        return AuthStatus(AuthMethod.CHATGPT, "Logged in using ChatGPT")
    if returncode != 0:
        return AuthStatus(AuthMethod.NOT_LOGGED_IN, text or f"exit {returncode}")
    return AuthStatus(AuthMethod.UNKNOWN, text)


def sanitized_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Allow-listed environment for the Codex child process: no API keys, no base URLs."""
    source = os.environ if base is None else base
    env = {k: source[k] for k in PASSTHROUGH_ENV if k in source}
    assert not (FORBIDDEN_ENV & env.keys())  # noqa: S101 - structural invariant
    return env


def forbidden_env_present(base: dict[str, str] | None = None) -> list[str]:
    """Names (never values) of API-billing variables present in the owner's shell."""
    source = os.environ if base is None else base
    return sorted(k for k in FORBIDDEN_ENV if source.get(k))


async def probe_login(codex_bin: str, *, env: dict[str, str] | None = None) -> AuthStatus:
    """Ask the official Codex CLI which login method is active. Reads no credential files."""
    try:
        proc = await asyncio.create_subprocess_exec(
            codex_bin,
            "login",
            "status",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=sanitized_env(env),
        )
    except (FileNotFoundError, PermissionError) as exc:
        return AuthStatus(AuthMethod.UNAVAILABLE, f"codex CLI not runnable: {type(exc).__name__}")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), STATUS_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return AuthStatus(AuthMethod.UNAVAILABLE, "codex login status timed out")
    return classify_login_status(out.decode(errors="replace"), proc.returncode or 0)


async def require_chatgpt_login(codex_bin: str, *, env: dict[str, str] | None = None) -> AuthStatus:
    status = await probe_login(codex_bin, env=env)
    if not status.eligible:
        raise SubscriptionAuthError(status)
    return status
