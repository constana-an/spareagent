"""In-app updates on the device (D-103): check, download, stage. No installed App changes."""

import plistlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from packages.worker.app_update import UpdateError, Updater, unpack_and_verify
from packages.worker.lease_client import TransportError, WorkerApiError

RELEASE = {"version": "0.15.0", "build": 17, "sha256": "abc", "size": 3}


class Client:
    def __init__(self, release=RELEASE, data=b"zip", error=None):
        self.release, self.data, self.error = release, data, error
        self.downloads = []

    async def app_release(self, arch):
        if isinstance(self.error, TransportError):
            raise self.error
        return self.release

    async def download_app_release(self, arch, sha256):
        self.downloads.append((arch, sha256))
        if self.error:
            raise self.error
        return self.data


def updater(tmp_path, client, build=16, stage=None):
    async def staged(archive, destination, build):
        destination.mkdir(parents=True)
        assert archive.read_bytes() == b"zip"
        return destination / "SpareAgent.app"

    return Updater(tmp_path, lambda: client, build, "arm64", stage or staged)


async def test_script_launches_and_unregistered_devices_do_not_update(tmp_path):
    script = Updater(tmp_path, lambda: Client(), None, "arm64")
    await script.check()
    assert script.status()["state"] == "unsupported"
    fresh = Updater(tmp_path, lambda: None, 16, "arm64")
    await fresh.check()
    assert fresh.status()["state"] == "idle"


@pytest.mark.parametrize(
    "release,state",
    [(RELEASE, "available"), (None, "current"), ({**RELEASE, "build": 16}, "current")],
)
async def test_check_offers_only_a_newer_build(tmp_path, release, state):
    u = updater(tmp_path, Client(release))
    await u.check()
    assert u.status()["state"] == state


async def test_prepare_stages_a_verified_copy(tmp_path):
    client = Client()
    u = updater(tmp_path, client)
    await u.check()
    await u.prepare()
    status = u.status()
    assert status["state"] == "ready" and status["version"] == "0.15.0"
    assert status["app_path"] == str(tmp_path / "updates/staged/SpareAgent.app")
    assert client.downloads == [("arm64", "abc")]
    await u.check()  # a staged update is not thrown away by the next check
    assert u.status()["state"] == "ready"


@pytest.mark.parametrize(
    "error,code",
    [
        (WorkerApiError(422, "UPDATE_CHECKSUM_MISMATCH", "x"), "UPDATE_CHECKSUM_MISMATCH"),
        (TransportError("offline"), "download_failed"),
    ],
)
async def test_failed_download_is_reported_and_can_be_retried(tmp_path, error, code):
    client = Client()
    u = updater(tmp_path, client)
    await u.check()
    client.error = error
    await u.prepare()
    assert u.status()["state"] == "failed" and u.status()["error"] == code
    client.error = None
    u.start_prepare()
    await u._job
    assert u.status()["state"] == "ready"


async def test_bad_package_is_not_staged(tmp_path):
    async def reject(archive, destination, build):
        raise UpdateError("signature_invalid")

    u = updater(tmp_path, Client(), stage=reject)
    await u.check()
    await u.prepare()
    assert u.status()["state"] == "failed" and u.status()["error"] == "signature_invalid"
    assert u.status()["app_path"] is None


mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="ditto and codesign")


def app_zip(tmp_path, *, bundle="org.spareagent.desktop", build=17, sign=True) -> Path:
    app = tmp_path / "src/SpareAgent.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    shutil.copyfile("/usr/bin/true", app / "Contents/MacOS/SpareAgent")
    (app / "Contents/MacOS/SpareAgent").chmod(0o755)
    info = {
        "CFBundleIdentifier": bundle,
        "CFBundleVersion": str(build),
        "CFBundleExecutable": "SpareAgent",
        "CFBundlePackageType": "APPL",
    }
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    if sign:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True
        )
    target = tmp_path / "app.zip"
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(target)], check=True)
    return target


@mac_only
async def test_real_unpack_checks_bundle_build_and_signature(tmp_path):
    good = await unpack_and_verify(app_zip(tmp_path / "a"), tmp_path / "a/out", 17)
    assert good.name == "SpareAgent.app" and good.is_dir()
    for kwargs, code in [
        ({"bundle": "com.example.other"}, "not_spareagent"),
        ({"build": 16}, "not_spareagent"),
        ({"sign": False}, "signature_invalid"),
    ]:
        case = tmp_path / code / str(kwargs)
        with pytest.raises(UpdateError, match=code):
            await unpack_and_verify(app_zip(case, **kwargs), case / "out", 17)
    with zipfile.ZipFile(tmp_path / "junk.zip", "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(UpdateError, match="not_spareagent"):
        await unpack_and_verify(tmp_path / "junk.zip", tmp_path / "junk", 17)
