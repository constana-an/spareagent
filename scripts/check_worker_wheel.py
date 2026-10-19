"""CI: install the actual wheel in an empty venv outside the repository and test its CLI."""

import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main():
    wheel = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="spareagent-wheel-") as tmp:
        root = Path(tmp)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv" / "bin" / "python"
        cli = root / "venv" / "bin" / "spareagent-worker"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            cwd=root,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.util; "
                "from packages.worker.cli import main; "
                "from packages.provider_sdk.codex_subscription import CodexSubscriptionAdapter; "
                "assert all(importlib.util.find_spec(p) is None for p in "
                "('apps', 'services', 'db', 'sqlalchemy', 'redis', 'fastapi', 'asyncpg'))",
            ],
            cwd=root,
            check=True,
        )
        subprocess.run([str(cli), "--help"], cwd=root, check=True, capture_output=True)
        # No installed Codex/auth required. No source imports and no model command possible.
        env = {**os.environ, "SPAREAGENT_WORKER_HOME": str(root / "state")}
        env.pop("PYTHONPATH", None)
        run = subprocess.run(
            [str(cli), "pilot-check", "--codex-bin", "/no/installed/codex"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(run.stdout)
        if run.returncode != 1 or report["inference_executed"] is not False:
            raise RuntimeError("installed worker failed the no-inference preflight contract")
        # Exercise the actual macOS installer on macOS CI, with a harmless CLI sentinel.
        if sys.platform == "darwin":
            import hashlib

            shutil_bin = root / "fake-codex"
            shutil_bin.write_text("#!/bin/sh\nexit 99\n")
            shutil_bin.chmod(0o700)
            env.update(
                SPAREAGENT_PYTHON=sys.executable,
                SPAREAGENT_CODEX_BIN=str(shutil_bin),
                SPAREAGENT_WORKER_INSTALL_DIR=str(root / "macos-worker"),
            )
            setup = Path(__file__).resolve().parent / "setup-worker-macos.sh"
            subprocess.run(
                ["bash", str(setup), str(wheel), hashlib.sha256(wheel.read_bytes()).hexdigest()],
                cwd=root,
                env=env,
                check=True,
            )
        print("PASS: isolated wheel; no backend packages; no-inference CLI; macOS setup checked")


if __name__ == "__main__":
    main()
