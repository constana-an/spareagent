"""Release safety checks without credentials, Apple uploads, or real inference."""

import importlib
import io
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def build(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("build_macos_app")


def test_release_requires_real_developer_id(build, monkeypatch):
    monkeypatch.setattr(
        build.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="0 valid identities found")
    )
    with pytest.raises(ValueError, match="requires a Developer ID Application"):
        build.require_identity("-")
    with pytest.raises(ValueError, match="not available"):
        build.require_identity("Developer ID Application: Missing (TEAMID)")


def test_runtime_is_signed_before_zip_and_original_archive_is_preserved(
    build, monkeypatch, tmp_path
):
    archive = tmp_path / "original.tar.gz"
    payloads = {
        "python/bin/python3": b"\xcf\xfa\xed\xfebinary",
        "python/lib/module.so": b"\xcf\xfa\xed\xfemodule",
        "python/README": b"text",
    }
    with tarfile.open(archive, "w:gz") as target:
        for name, payload in payloads.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            target.addfile(member, io.BytesIO(payload))
        link = tarfile.TarInfo("python/bin/python")
        link.type, link.linkname = tarfile.SYMTYPE, "python3"
        target.addfile(link)
    original = archive.read_bytes()
    signed = []
    monkeypatch.setattr(build, "sign_code", lambda p, identity: signed.append(p.name))

    def zip_tree(tree, destination):
        assert signed == ["python3", "module.so"]
        assert (tree / "bin/python").is_symlink()
        destination.write_bytes(b"signed ZIP fixture")

    monkeypatch.setattr(build, "zip_app", zip_tree)
    destination, components = build.signed_runtime(archive, tmp_path / "python.zip", "fixture")
    assert destination.read_bytes() == b"signed ZIP fixture"
    assert components == ["python/bin/python3", "python/lib/module.so"]
    assert archive.read_bytes() == original


def test_only_python_interpreter_allows_loading_pinned_native_wheels(build, monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(build.subprocess, "run", lambda command, **kwargs: commands.append(command))
    build.sign_code(tmp_path / "python/bin/python3.12", "identity")
    build.sign_code(tmp_path / "SpareAgent.app", "identity")
    assert "--entitlements" in commands[0] and "runtime" in commands[0]
    assert "--entitlements" not in commands[2] and "runtime" in commands[2]
    assert all("--deep" not in c for c in commands)


def test_source_installer_checksums_source_and_private_resources_without_native_app(
    build, monkeypatch, tmp_path
):
    import hashlib

    app = tmp_path / "SpareAgent.app"
    resources = app / "Contents/Resources"
    resources.mkdir(parents=True)
    (app / "Contents/Info.plist").write_text("plist fixture")
    (resources / "bundle.json").write_text("private fixture")
    (resources / "bundle.json").chmod(0o600)
    (resources / "worker.whl").write_bytes(b"wheel fixture")
    kit = tmp_path / "unpacked"
    monkeypatch.setattr(build, "zip_app", lambda source, target: shutil.copytree(source, kit))
    build.source_installer(app, tmp_path / "private.zip", "arm64")
    assert (kit / "SpareAgent.swift").exists()
    assert not list(kit.rglob("MacOS/SpareAgent"))
    assert (kit / "ARCH").read_text().strip() == "arm64"
    assert (kit / "Resources/bundle.json").stat().st_mode & 0o777 == 0o600
    for line in (kit / "INSTALL_SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((kit / name).read_bytes()).hexdigest() == digest
    assert "Resources/bundle.json" in (kit / "INSTALL_SHA256SUMS").read_text()


@pytest.mark.parametrize("status", ["Accepted", "Invalid", "In Progress"])
def test_stapling_and_gatekeeper_follow_only_accepted_notarization(
    build, monkeypatch, tmp_path, status
):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"id": "submission-id", "status": status}), stderr=""
        )

    monkeypatch.setattr(build.subprocess, "run", run)
    if status == "Accepted":
        build.notarize(tmp_path / "App.app", tmp_path / "App.zip", "profile", tmp_path / "logs")
        assert any(c[:3] == ["xcrun", "stapler", "staple"] for c in commands)
        assert commands[-1][0] == "spctl"
    else:
        with pytest.raises(RuntimeError, match="not accepted"):
            build.notarize(tmp_path / "App.app", tmp_path / "App.zip", "profile", tmp_path / "logs")
        assert not any("stapler" in c or "spctl" in c for c in commands)
    assert json.loads((tmp_path / "logs/submission.json").read_text())["status"] == status
