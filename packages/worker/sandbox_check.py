"""No-inference OS sandbox check. Uses harmless markers, never reads credentials."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from packages.worker.codex_runtime import shared_permission_args
from packages.worker.subscription_auth import sanitized_env


def check(codex_bin: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="spareagent-isolation-") as directory:
        root = Path(directory)
        work = root / "checkout"
        work.mkdir()
        (work / "public.txt").write_text("PUBLIC_FIXTURE\n")
        (root / "outside.txt").write_text("PRIVATE_MARKER_NOT_A_CREDENTIAL")
        empty_home = root / "empty-codex-home"
        empty_home.mkdir()
        env = sanitized_env()
        env["CODEX_HOME"] = str(empty_home)
        results = {}
        script = (
            "cat public.txt; "
            "if cat ../outside.txt >/dev/null 2>&1; then echo OUTSIDE_READ_ALLOWED; "
            "else echo OUTSIDE_READ_BLOCKED; fi; "
            "if echo x > written.txt 2>/dev/null; then echo WRITE_ALLOWED; "
            "else echo WRITE_BLOCKED; fi"
        )
        for mode in ("read-only", "workspace-write"):
            run = subprocess.run(  # noqa: S603 - owner-selected local executable, fixed arguments
                [
                    codex_bin,
                    *shared_permission_args(mode),
                    "sandbox",
                    "--permission-profile",
                    "spareagent",
                    "-C",
                    str(work),
                    "/bin/sh",
                    "-c",
                    script,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            results[mode] = {
                "exit_code": run.returncode,
                "checkout_read": "PUBLIC_FIXTURE" in run.stdout,
                "outside_read_blocked": "OUTSIDE_READ_BLOCKED" in run.stdout,
                "checkout_write_allowed": "WRITE_ALLOWED" in run.stdout,
            }
        ok = all(
            r["exit_code"] == 0
            and r["checkout_read"]
            and r["outside_read_blocked"]
            and r["checkout_write_allowed"] == (mode == "workspace-write")
            for mode, r in results.items()
        )
        return {"kind": "REAL_OS_SANDBOX_NO_INFERENCE", "passed": ok, "checks": results}
