"""Local installer recovery, with fake tools and an isolated Applications directory."""

import hashlib
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac installer")


def manifest(kit):
    (kit / "INSTALL_SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(kit)}\n"
            for p in sorted(kit.rglob("*"))
            if p.is_file() and p.name != "INSTALL_SHA256SUMS"
        )
    )


def executable(path, text):
    path.write_text("#!/bin/bash\n" + text)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def installation(tmp_path):
    kit = tmp_path / "downloaded kit"
    kit.mkdir()
    (kit / "Resources").mkdir()
    (kit / "Resources/bundle.json").write_text("{}")
    (kit / "ARCH").write_text(platform.machine())
    (kit / "SpareAgent.swift").write_text("fixture")
    info = {"CFBundleIdentifier": "org.spareagent.desktop", "CFBundleExecutable": "SpareAgent"}
    (kit / "Info.plist").write_bytes(plistlib.dumps(info))
    applications = tmp_path / "my Applications"
    previous = applications / "SpareAgent.app"
    (previous / "Contents").mkdir(parents=True)
    (previous / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    (previous / "previous.txt").write_text("previous app")
    state = tmp_path / "state"
    state.mkdir()
    (state / "node.json").write_text("existing identity")
    selector = executable(
        tmp_path / "selector", 'if [[ "$1" == --install ]]; then touch "$0.installed"; fi\n'
    )
    compiler = executable(
        tmp_path / "compiler",
        '[[ "$1" == --find ]] && exit 0\n'
        '[[ ! -e "$0.fail" ]] || exit 19\n'
        'while [[ "$1" != -o ]]; do shift; done\n'
        'printf "#!/bin/bash\\nexit 0\\n" > "$2"\nchmod 755 "$2"\n',
    )
    signer = executable(tmp_path / "signer", "exit 0\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    executable(
        tools / "mv",
        '[[ ! -e "$SPAREAGENT_WORKER_HOME/replacement.fail" '
        '|| "$1" != */.spareagent-install-*/SpareAgent.app ]] || exit 27\n'
        'exec /bin/mv "$@"\n',
    )
    script = (ROOT / "scripts/Install-SpareAgent.sh").read_text()
    for original, replacement in (
        ("/usr/bin/xcode-select", selector),
        ("/usr/bin/xcrun", compiler),
        ("/usr/bin/codesign", signer),
    ):
        script = script.replace(original, '"' + replacement + '"')
    (kit / "Install-SpareAgent.sh").write_text(script)
    manifest(kit)
    env = {
        **os.environ,
        "SPAREAGENT_INSTALL_DIR": str(applications),
        "SPAREAGENT_WORKER_HOME": str(state),
        "SPAREAGENT_INSTALL_NO_OPEN": "1",
        "PATH": f"{tools}:{os.environ['PATH']}",
    }

    def run():
        return subprocess.run(
            ["bash", str(kit / "Install-SpareAgent.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    return kit, previous, state, selector, compiler, run


def test_repeat_install_keeps_identity_and_cleans_staging(installation):
    _, app, state, _, _, run = installation
    for _ in range(2):
        result = run()
        assert result.returncode == 0, result.stderr
        assert (app / "Contents/MacOS/SpareAgent").exists()
        assert (app / "Contents/Resources/LOCAL_BUILD.txt").exists()
        assert (state / "node.json").read_text() == "existing identity"
        assert not list(app.parent.glob(".spareagent-install-*"))


@pytest.mark.parametrize(
    "failure", ["checksum", "arch", "compiler", "replacement", "running", "destination"]
)
def test_install_failure_preserves_previous_app_and_identity(installation, failure):
    kit, app, state, _, compiler, run = installation
    if failure == "checksum":
        (kit / "SpareAgent.swift").write_text("tampered")
    elif failure == "arch":
        (kit / "ARCH").write_text("unsupported")
        manifest(kit)
    elif failure == "compiler":
        Path(compiler + ".fail").touch()
    elif failure == "replacement":
        (state / "replacement.fail").touch()
    elif failure == "running":
        (state / "worker.pid").write_text(str(os.getpid()))
    else:
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "org.other.app"})
        )
    result = run()
    assert result.returncode != 0
    assert (app / "previous.txt").read_text() == "previous app"
    assert (state / "node.json").read_text() == "existing identity"
    assert not list(app.parent.glob(".spareagent-install-*"))


def test_missing_apple_tools_requests_install_then_exits_for_retry(installation):
    _, app, _, selector, _, run = installation
    executable(Path(selector), 'if [[ "$1" == -p ]]; then exit 1; fi\ntouch "$0.installed"\n')
    result = run()
    assert result.returncode == 2
    assert Path(selector + ".installed").exists()
    assert (app / "previous.txt").exists()
