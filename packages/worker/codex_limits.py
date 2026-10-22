"""How much of the owner's ChatGPT plan is left, as the official Codex CLI reports it.

Asks `codex app-server` (JSON-RPC over stdio) for `account/rateLimits/read`. No task is
run. Only each window's length, remaining percentage and reset time are kept: credit
balances and plan details are dropped, and nothing is sent off this device.

`quota_status` turns that into what the menu bar shows. When Codex cannot answer (older
CLI, changed protocol, signed out), it falls back to what finished tasks tell us: a
recent task stopped by the usage limit means the plan is used up, otherwise it is fine.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from datetime import datetime
from typing import Any

from packages.worker.subscription_auth import sanitized_env

PROBE_TIMEOUT_SECONDS = 15.0
MAX_LINE_BYTES = 1024 * 1024
# A reading older than this is not shown as numbers any more.
STALE_SECONDS = 600.0
LOW_PERCENT = 10
# Without numbers, a usage-limit failure counts for one short window.
FALLBACK_WINDOW_SECONDS = 5 * 3600
FINISHED = ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED")


def _window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used, minutes, resets = (
        raw.get("usedPercent"),
        raw.get("windowDurationMins"),
        raw.get("resetsAt"),
    )
    if isinstance(used, bool) or not isinstance(used, int | float):
        return None
    if type(minutes) is not int or minutes <= 0:
        return None
    return {
        "minutes": minutes,
        "remaining_percent": max(0, min(100, round(100 - used))),
        "resets_at": resets if type(resets) is int and resets > 0 else None,
    }


def parse_rate_limits(result: Any) -> dict[str, Any] | None:
    """Keep only the windows of an `account/rateLimits/read` result; None if it has none."""
    limits = result.get("rateLimits") if isinstance(result, dict) else None
    if not isinstance(limits, dict):
        return None
    windows = [w for w in (_window(limits.get(k)) for k in ("primary", "secondary")) if w]
    if not windows:
        return None
    windows.sort(key=lambda w: w["minutes"])
    return {"windows": windows, "reached": bool(limits.get("rateLimitReachedType"))}


async def read_rate_limits(
    codex_bin: str,
    *,
    env: dict[str, str] | None = None,
    limit_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Ask the owner's Codex for the current plan usage. None when it cannot say."""
    try:
        proc = await asyncio.create_subprocess_exec(
            codex_bin,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=sanitized_env(env),
            limit=MAX_LINE_BYTES,
            # npm's `codex` is a wrapper whose child keeps stdout open: stop the whole group.
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError):
        return None
    assert proc.stdin is not None and proc.stdout is not None  # noqa: S101 - PIPE above
    try:
        for message in (
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "spareagent", "version": "1"}},
            },
            {"method": "initialized"},
            {"id": 2, "method": "account/rateLimits/read", "params": None},
        ):
            proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

        async def answer() -> Any:
            while line := await proc.stdout.readline():
                with contextlib.suppress(ValueError):
                    reply = json.loads(line)
                    if isinstance(reply, dict) and reply.get("id") == 2:
                        return reply.get("result")
            return None

        return parse_rate_limits(await asyncio.wait_for(answer(), limit_seconds))
    except (TimeoutError, OSError, ValueError):
        return None
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), 5)


def _level(windows: list[dict[str, Any]], reached: bool) -> str:
    lowest = min(w["remaining_percent"] for w in windows)
    if reached or lowest <= 0:
        return "exhausted"
    return "low" if lowest < LOW_PERCENT else "ok"


def _created(task: dict[str, Any]) -> float:
    with contextlib.suppress(TypeError, ValueError):
        return datetime.fromisoformat(str(task.get("created_at"))).timestamp()
    return 0.0


def quota_status(
    limits: dict[str, Any] | None,
    checked_at: float,
    tasks: list[dict[str, Any]],
    now: float,
) -> dict[str, Any]:
    """`ok` / `low` / `exhausted`, with the windows when a fresh Codex reading exists."""
    if limits and now - checked_at <= STALE_SECONDS:
        return {
            "source": "codex",
            "level": _level(limits["windows"], limits["reached"]),
            "windows": limits["windows"],
            "checked_at": int(checked_at),
        }
    finished = sorted(
        (t for t in tasks if isinstance(t, dict) and t.get("status") in FINISHED),
        key=_created,
        reverse=True,
    )
    last = finished[0] if finished else {}
    hit = (last.get("failure") or {}).get("category") == "USAGE_LIMIT"
    recent = now - _created(last) <= FALLBACK_WINDOW_SECONDS
    return {"source": "fallback", "level": "exhausted" if hit and recent else "ok", "windows": []}
