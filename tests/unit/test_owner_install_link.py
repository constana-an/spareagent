"""One-line install: the operator's link tool and the owner's install.sh."""

import hashlib
import json
import os
import platform
import plistlib
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import owner_install_link as tool  # noqa: E402

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
API = "https://host.example.ts.net:8444"
INFO = {"CFBundleIdentifier": "org.spareagent.desktop", "CFBundleExecutable": "SpareAgent"}


def app_zip(path: Path, info: dict = INFO) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SpareAgent.app/Contents/Info.plist", plistlib.dumps(info))
        archive.writestr("SpareAgent.app/Contents/MacOS/SpareAgent", "#!/bin/bash\nexit 0\n")
        archive.writestr("SpareAgent.app/Contents/Resources/bundle.json", "{}")
    return path


def make(root, zip_path, label="小明", days=7, now=NOW, arch="arm64"):
    return tool.create(root, API, zip_path, arch, label, days, now)


def test_create_writes_a_bound_script_and_lists_it(tmp_path):
    root = tmp_path / "install"
    zip_path = app_zip(tmp_path / "app.zip")
    link = make(root, zip_path)
    (folder,) = [p for p in root.iterdir()]
    assert len(folder.name) == 32 and not folder.name.startswith(".")
    assert link["command"] == f"curl -fsSL {API}/install/{folder.name}/install.sh | bash"
    script = (folder / "install.sh").read_text()
    assert "@@" not in script
    assert f'zip_url="{API}/install/{folder.name}/SpareAgent-arm64.zip"' in script
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() in script
    assert (folder / "SpareAgent-arm64.zip").read_bytes() == zip_path.read_bytes()
    assert {oct(p.stat().st_mode & 0o777) for p in folder.iterdir()} == {"0o644"}
    assert [x["label"] for x in tool.read_links(root)] == ["小明"]


def test_revoke_by_label_or_folder_and_prune_expired(tmp_path):
    root = tmp_path / "install"
    zip_path = app_zip(tmp_path / "app.zip")
    make(root, zip_path, "a", days=1)
    make(root, zip_path, "b", days=7)
    make(root, zip_path, "c", days=7)
    folder_c = next(x["dir"] for x in tool.read_links(root) if x["label"] == "c")
    assert [x["label"] for x in tool.revoke(root, folder_c.name)] == ["c"]
    assert [x["label"] for x in tool.prune(root, NOW + timedelta(days=2))] == ["a"]
    assert [x["label"] for x in tool.revoke(root, "b")] == ["b"]
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "case",
    ["source-installer zip", "missing zip", "duplicate label", "http url", "url with path", "days"],
)
def test_create_refuses_bad_input(tmp_path, case):
    root = tmp_path / "install"
    zip_path = app_zip(tmp_path / "app.zip")
    kwargs = {}
    if case == "source-installer zip":
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("SpareAgent-Local-Install-arm64/SpareAgent.swift", "")
    elif case == "missing zip":
        zip_path = tmp_path / "absent.zip"
    elif case == "duplicate label":
        make(root, zip_path)
    elif case == "days":
        kwargs["days"] = 31
    api = {"http url": "http://host:8444", "url with path": API + "/v1"}.get(case, API)
    with pytest.raises(tool.LinkError):
        tool.create(root, api, zip_path, "arm64", "小明", kwargs.get("days", 7), NOW)
    if case != "duplicate label":
        assert not root.exists() or list(root.iterdir()) == []


def test_cli_create_reads_operator_api_url(tmp_path, capsys):
    private = tmp_path / "private dir"
    private.mkdir()
    (private / "operator.json").write_text(json.dumps({"api_url": API}))
    zip_path = app_zip(tmp_path / "app.zip")
    assert (
        tool.main(
            [
                "--private-dir",
                str(private),
                "create",
                "--label",
                "x",
                "--app-zip",
                str(zip_path),
                "--arch",
                "arm64",
            ]
        )
        == 0
    )
    assert f"curl -fsSL {API}/install/" in capsys.readouterr().out
    assert tool.main(["--private-dir", str(private), "revoke", "nobody"]) == 1


# --- install.sh, with fake curl/codesign and an isolated Applications directory ---

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="Mac installer")


def executable(path, text):
    path.write_text("#!/bin/bash\n" + text)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def owner(tmp_path):
    arch = platform.machine()
    root = tmp_path / "install"
    make(root, app_zip(tmp_path / "served.zip"), arch=arch)
    (folder,) = list(root.iterdir())
    served = folder / f"SpareAgent-{arch}.zip"
    # curl "downloads" the link's ZIP from disk; a marker file makes it fail.
    curl = executable(
        tmp_path / "curl",
        '[[ ! -e "$0.fail" ]] || exit 22\n'
        'while [[ "$1" != -o ]]; do shift; done\n'
        f'cp "{served}" "$2"\n',
    )
    signer = executable(tmp_path / "codesign", '[[ ! -e "$0.fail" ]] || exit 1\n')
    script = (folder / "install.sh").read_text()
    for original, replacement in (("/usr/bin/curl", curl), ("/usr/bin/codesign", signer)):
        script = script.replace(original, '"' + replacement + '"')
    applications = tmp_path / "my Applications"
    previous = applications / "SpareAgent.app"
    (previous / "Contents").mkdir(parents=True)
    (previous / "Contents/Info.plist").write_bytes(plistlib.dumps(INFO))
    (previous / "previous.txt").write_text("previous app")
    state = tmp_path / "state"
    state.mkdir()
    (state / "node.json").write_text("existing identity")
    tools = tmp_path / "tools"
    tools.mkdir()
    executable(
        tools / "mv",
        '[[ ! -e "$SPAREAGENT_WORKER_HOME/replacement.fail" '
        '|| "$1" != */unpacked/SpareAgent.app ]] || exit 27\n'
        'exec /bin/mv "$@"\n',
    )
    o = {
        "served": served,
        "app": previous,
        "state": state,
        "curl": curl,
        "signer": signer,
        "script": script,
        "env": {
            **os.environ,
            "SPAREAGENT_INSTALL_DIR": str(applications),
            "SPAREAGENT_WORKER_HOME": str(state),
            "SPAREAGENT_INSTALL_NO_OPEN": "1",
            "PATH": f"{tools}:{os.environ['PATH']}",
        },
    }
    # The way owners run it: the script arrives on stdin.
    o["run"] = lambda: subprocess.run(
        ["bash"], input=o["script"], env=o["env"], capture_output=True, text=True, timeout=20
    )
    return o


def unchanged(o):
    assert (o["app"] / "previous.txt").read_text() == "previous app"
    assert (o["state"] / "node.json").read_text() == "existing identity"
    assert not list(o["app"].parent.glob(".spareagent-install-*"))


@mac_only
def test_install_twice_keeps_identity_and_cleans_staging(owner):
    for _ in range(2):
        result = owner["run"]()
        assert result.returncode == 0, result.stderr
        app = owner["app"]
        assert (app / "Contents/MacOS/SpareAgent").exists()
        assert not (app / "previous.txt").exists()
        assert (owner["state"] / "node.json").read_text() == "existing identity"
        assert not list(app.parent.glob(".spareagent-install-*"))


@mac_only
@pytest.mark.parametrize(
    "failure", ["download", "checksum", "signature", "not spareagent", "replacement", "running"]
)
def test_failure_keeps_previous_app_and_identity(owner, failure):
    if failure == "download":
        Path(owner["curl"] + ".fail").touch()
    elif failure == "checksum":
        owner["served"].write_bytes(owner["served"].read_bytes() + b"tampered")
    elif failure == "signature":
        Path(owner["signer"] + ".fail").touch()
    elif failure == "not spareagent":
        # A correctly checksummed ZIP that holds some other app.
        old = hashlib.sha256(owner["served"].read_bytes()).hexdigest()
        app_zip(owner["served"], {**INFO, "CFBundleIdentifier": "com.example.other"})
        new = hashlib.sha256(owner["served"].read_bytes()).hexdigest()
        owner["script"] = owner["script"].replace(old, new)
    elif failure == "replacement":
        (owner["state"] / "replacement.fail").touch()
    elif failure == "running":
        (owner["state"] / "worker.pid").write_text(str(os.getpid()))
    result = owner["run"]()
    assert result.returncode != 0
    unchanged(owner)


@mac_only
def test_wrong_cpu_link_is_refused_before_download(owner):
    other = "x86_64" if platform.machine() == "arm64" else "arm64"
    owner["script"] = owner["script"].replace(
        f'want_arch="{platform.machine()}"', f'want_arch="{other}"'
    )
    Path(owner["curl"] + ".fail").touch()  # would fail differently if it got that far
    result = owner["run"]()
    assert result.returncode != 0 and "处理器" in result.stderr
    unchanged(owner)


def test_script_never_touches_quarantine_or_gatekeeper():
    text = (ROOT / "scripts/install-remote.sh").read_text()
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("xattr", "quarantine", "spctl", "--noextattr"):
        assert forbidden not in body
