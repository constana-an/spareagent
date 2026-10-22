"""Native launcher failure recovery, with fake local Python and no downloads/inference."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher uses plutil")


def test_native_launcher_rolls_back_a_failed_worker_upgrade(tmp_path):
    bundle, base = tmp_path / "bundle", tmp_path / "app home"
    bundle.mkdir()
    shutil.copyfile(ROOT / "scripts/SpareAgent.command", bundle / "SpareAgent.command")
    pins = json.loads((ROOT / "scripts/runtime-pins.json").read_text())
    (bundle / "runtime.json").write_text(json.dumps(pins))
    (bundle / "bundle.json").write_text("{}")
    wheel = bundle / "spareagent_worker-0.12.0-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel, never installed")
    (bundle / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
            for p in (wheel, bundle / "runtime.json")
        )
    )
    sha = pins["python"]["assets"][platform.machine()]["sha256"]
    python = base / f"python-{sha[:16]}" / "bin/python3"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/bash\nmkdir -p "$3/bin"\n'
        'printf "#!/bin/bash\\nexit 17\\n" > "$3/bin/python"\n'
        'chmod 755 "$3/bin/python"\n'
    )
    python.chmod(0o755)
    worker = base / "worker"
    worker.mkdir()
    (worker / "original.txt").write_text("previous installation")
    (worker / ".package-sha256").write_text("previous stamp")
    result = subprocess.run(
        ["bash", str(bundle / "SpareAgent.command")],
        env={**os.environ, "SPAREAGENT_NATIVE_APP": "1", "SPAREAGENT_APP_HOME": str(base)},
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 17
    assert (worker / "original.txt").read_text() == "previous installation"
    assert (worker / ".package-sha256").read_text() == "previous stamp"
    assert not (worker / "bin/python").exists()
    assert not list(base.glob(".previous-worker-*"))


def test_incomplete_native_package_fails_without_waiting_for_terminal_input(tmp_path):
    launcher = tmp_path / "SpareAgent.command"
    shutil.copyfile(ROOT / "scripts/SpareAgent.command", launcher)
    result = subprocess.run(
        ["bash", str(launcher)],
        env={**os.environ, "SPAREAGENT_NATIVE_APP": "1"},
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 1
    assert "安装包不完整" in result.stderr.decode()
