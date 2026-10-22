"""The in-app update swap (D-103): replace the App after it quits, or roll back."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GONE = 999999  # above macOS/Linux default pid limits: an App that already quit


def setup(tmp_path, signature_ok=True):
    target = tmp_path / "my Applications/SpareAgent.app"
    target.mkdir(parents=True)
    (target / "version").write_text("old")
    staged = tmp_path / "updates/staged/SpareAgent.app"
    staged.mkdir(parents=True)
    (staged / "version").write_text("new")
    signer = tmp_path / "codesign"
    signer.write_text(f"#!/bin/bash\nexit {0 if signature_ok else 1}\n")
    signer.chmod(0o755)
    script = (ROOT / "scripts/swap-app.sh").read_text().replace("/usr/bin/codesign", str(signer))
    return target, staged, script


def run(script, *args):
    return subprocess.run(
        ["bash", "-c", script, "swap", *map(str, args)],
        env={**os.environ, "SPAREAGENT_SWAP_NO_OPEN": "1"},
        capture_output=True,
        timeout=20,
    )


def test_swaps_in_the_staged_app_and_removes_the_backup(tmp_path):
    target, staged, script = setup(tmp_path)
    # The quitting App: detached, so it is reaped (not a zombie) once it exits.
    pid = int(subprocess.check_output(["bash", "-c", "sleep 0.5 >/dev/null 2>&1 & echo $!"]))
    assert run(script, pid, staged, target).returncode == 0
    assert (target / "version").read_text() == "new"
    assert not staged.exists()
    assert not list(target.parent.glob(".SpareAgent-previous-*"))


def test_rolls_back_when_the_new_app_does_not_verify(tmp_path):
    target, staged, script = setup(tmp_path, signature_ok=False)
    assert run(script, GONE, staged, target).returncode == 1
    assert (target / "version").read_text() == "old"
    assert not list(target.parent.glob(".SpareAgent-previous-*"))


def test_missing_staged_app_changes_nothing(tmp_path):
    target, staged, script = setup(tmp_path)
    (staged / "version").unlink()
    staged.rmdir()
    assert run(script, GONE, staged, target).returncode == 1
    assert (target / "version").read_text() == "old"
