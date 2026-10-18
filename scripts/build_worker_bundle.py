"""Operator builds a worker-only wheel bundle; friends do not clone/install the backend."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "output" / "worker-bundle"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(out),
            str(ROOT / "packages" / "worker"),
        ],
        check=True,
    )
    wheel = out / "spareagent_worker-0.6.0-py3-none-any.whl"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (out / "SHA256SUMS").write_text(f"{digest}  {wheel.name}\n")
    shutil.copy2(ROOT / "scripts" / "setup-worker-macos.sh", out)
    print(f"Bundle: {out}\nWorker SHA-256: {digest}")


if __name__ == "__main__":
    main()
