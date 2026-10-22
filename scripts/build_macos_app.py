"""Build a private Mac App, optionally Developer ID-signed and notarized."""

import argparse
import contextlib
import hashlib
import json
import platform
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from build_worker_bundle import ROOT, WHEEL

MACHO_MAGICS = {
    bytes.fromhex(m)
    for m in (
        "feedface",
        "cefaedfe",
        "feedfacf",
        "cffaedfe",
        "cafebabe",
        "bebafeca",
        "cafebabf",
        "bfbafeca",
    )
}


def macho_files(root: Path) -> list[Path]:
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as source:
                if source.read(4) in MACHO_MAGICS:
                    found.append(path)
    return found


def sign_code(path: Path, identity: str) -> None:
    # The interpreter loads the pinned native wheels used by optional data tools.
    entitlements = (
        ["--entitlements", str(ROOT / "apps/macos/Python.entitlements")]
        if path.parent.name == "bin" and path.name.startswith("python")
        else []
    )
    subprocess.run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp",
            "--options",
            "runtime",
            *entitlements,
            str(path),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--verify", "--strict", str(path)], check=True)


def zip_app(source: Path, target: Path) -> None:
    subprocess.run(
        ["ditto", "--norsrc", "--noextattr", "-c", "-k", "--keepParent", str(source), str(target)],
        check=True,
    )
    target.chmod(0o600)


def signed_runtime(archive: Path, destination: Path, identity: str) -> tuple[Path, list[str]]:
    # ZIP keeps nested executables visible to Apple's notarization scanner.
    with tempfile.TemporaryDirectory(prefix="spareagent-sign-python-") as temp:
        tree = Path(temp)
        with tarfile.open(archive) as source:
            source.extractall(tree, filter="data")
        python = tree / "python"
        binaries = macho_files(python)
        if not binaries:
            raise ValueError("Python archive contains no Mach-O executables")
        for binary in binaries:
            sign_code(binary, identity)
        zip_app(python, destination)
        return destination, [str(p.relative_to(tree)) for p in binaries]


def require_identity(identity: str) -> None:
    if not identity.startswith("Developer ID Application:"):
        raise ValueError("Release signing requires a Developer ID Application identity")
    result = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        check=True,
        capture_output=True,
        text=True,
    )
    if f'"{identity}"' not in result.stdout:
        raise ValueError("Developer ID Application identity not available in the keychain")


def notarize(app: Path, archive: Path, profile: str, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "xcrun",
            "notarytool",
            "submit",
            str(archive),
            "--keychain-profile",
            profile,
            "--wait",
            "--timeout",
            "10m",
            "--output-format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    receipt = report_dir / "submission.json"
    receipt.write_text(result.stdout)
    receipt.chmod(0o600)
    if result.returncode:
        raise RuntimeError(f"Notarization did not complete; receipt: {receipt}. {result.stderr}")
    submission = json.loads(result.stdout)
    subprocess.run(
        [
            "xcrun",
            "notarytool",
            "log",
            submission["id"],
            "--keychain-profile",
            profile,
            str(report_dir / "notary-log.json"),
        ],
        check=True,
    )
    if submission.get("status") != "Accepted":
        raise RuntimeError(f"Notarization was not accepted; inspect {report_dir}")
    subprocess.run(["xcrun", "stapler", "staple", str(app)], check=True)
    subprocess.run(["xcrun", "stapler", "validate", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    subprocess.run(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)], check=True)


VERSION_FILE = ROOT / "apps/macos/version.json"
LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
    "/Support/lsregister"
)


def forget_app(app: Path) -> None:
    """Drop a build copy from Launch Services so it never shows up in Launchpad."""
    if LSREGISTER.exists():
        subprocess.run([str(LSREGISTER), "-u", str(app)], capture_output=True, check=False)


def app_version(path: Path = VERSION_FILE) -> tuple[str, int]:
    """The App's version and build number. In-app updates compare the build, so raise it
    (and the version) in this one file before publishing a new App."""
    data = json.loads(path.read_text())
    version, build = data.get("version"), data.get("build")
    parts = version.split(".") if isinstance(version, str) else []
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        raise ValueError(f"{path.name}: version must look like 1.2.3")
    if type(build) is not int or build < 1:
        raise ValueError(f"{path.name}: build must be a positive whole number")
    return version, build


def check_digest(path: Path, expected: str) -> None:
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum mismatch: {path.name}")


def source_installer(app: Path, destination: Path, arch: str) -> None:
    kit = app.parent / f"SpareAgent-Local-Install-{arch}"
    kit.mkdir()
    shutil.copytree(app / "Contents/Resources", kit / "Resources")
    shutil.copyfile(app / "Contents/Info.plist", kit / "Info.plist")
    shutil.copyfile(ROOT / "apps/macos/SpareAgent.swift", kit / "SpareAgent.swift")
    shutil.copyfile(ROOT / "scripts/Install-SpareAgent.sh", kit / "Install-SpareAgent.sh")
    shutil.copyfile(ROOT / "docs/LOCAL_APP_INSTALL.md", kit / "README.md")
    (kit / "Install-SpareAgent.sh").chmod(0o755)
    (kit / "ARCH").write_text(arch + "\n")
    (kit / "INSTALL_SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(kit).as_posix()}\n"
            for p in sorted(kit.rglob("*"))
            if p.is_file()
        )
    )
    zip_app(kit, destination)
    print(
        f"Private local-build installer: {destination}\nNo Developer ID or Apple upload required."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, default=ROOT / "output/worker-bundle")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/macos-app")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    parser.add_argument(
        "--python-archive", type=Path, help="optional previously downloaded archive"
    )
    parser.add_argument("--sign-identity", help="exact Developer ID Application identity name")
    parser.add_argument(
        "--source-installer",
        action="store_true",
        help="package Swift source for local App installation",
    )
    parser.add_argument(
        "--notary-profile", help="upload to Apple using this notarytool keychain profile"
    )
    args = parser.parse_args()
    if args.source_installer and (args.sign_identity or args.notary_profile):
        parser.error("--source-installer cannot be combined with signing/notarization")
    if args.notary_profile and not args.sign_identity:
        parser.error("--notary-profile requires --sign-identity")
    if args.sign_identity:
        try:
            require_identity(args.sign_identity)
        except ValueError as exc:
            parser.error(str(exc))
    source, out = args.bundle_dir.resolve(), args.output_dir.resolve()
    for name in ("bundle.json", "runtime.json", "SHA256SUMS", WHEEL):
        if not (source / name).is_file():
            parser.error(f"Missing {name}; first build the private worker bundle")
    checked = []
    for line in (source / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        path = source / name.strip()
        if not path.resolve().is_relative_to(source):
            raise ValueError("Bundle checksum path leaves its directory")
        check_digest(path, digest)
        checked.append(name.strip())
    pins = json.loads((source / "runtime.json").read_text())
    asset = pins["python"]["assets"][args.arch]
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = args.python_archive or cache / f"python-{args.arch}.tar.gz"
    if not archive.exists():
        partial = archive.with_suffix(".part")
        subprocess.run(
            [
                "curl",
                "-fL",
                "--proto",
                "=https",
                "--tlsv1.2",
                "--retry",
                "3",
                "-o",
                str(partial),
                asset["url"],
            ],
            check=True,
        )
        check_digest(partial, asset["sha256"])
        partial.replace(archive)
    check_digest(archive, asset["sha256"])
    version, build = app_version()
    # Only the ZIP is kept. Loose .app copies get registered by Launch Services and show up
    # as extra SpareAgent icons in Launchpad, so drop any an earlier build left here.
    for old in out.glob("SpareAgent*.app"):
        forget_app(old)
        shutil.rmtree(old, ignore_errors=True)

    with contextlib.ExitStack() as cleanup:
        build_dir = cleanup.enter_context(
            tempfile.TemporaryDirectory(prefix="spareagent-app-build-")
        )
        app = Path(build_dir) / "SpareAgent.app"
        # Launching the self-test registers the App with Launch Services; forget both copies
        # before the folder is deleted, or Launchpad keeps listing them.
        cleanup.callback(forget_app, Path(build_dir) / "archive-check/SpareAgent.app")
        cleanup.callback(forget_app, app)
        shutil.rmtree(app, ignore_errors=True)
        macos, resources = app / "Contents/MacOS", app / "Contents/Resources"
        macos.mkdir(parents=True)
        resources.mkdir()
        for name in (*checked, "bundle.json"):
            target = resources / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        (resources / "bundle.json").chmod(0o600)
        shutil.copyfile(ROOT / "scripts/SpareAgent.command", resources / "SpareAgent.command")
        shutil.copyfile(ROOT / "scripts/swap-app.sh", resources / "swap-app.sh")  # D-103
        runtime_name = f"python-{args.arch}.tar.gz"
        signed_binaries = []
        if args.sign_identity:
            runtime_name = f"python-{args.arch}.zip"
            runtime, signed_binaries = signed_runtime(
                archive, resources / runtime_name, args.sign_identity
            )
            pins["python"]["assets"][args.arch]["bundled_sha256"] = hashlib.sha256(
                runtime.read_bytes()
            ).hexdigest()
            (resources / "runtime.json").write_text(json.dumps(pins, indent=2))
        else:
            shutil.copyfile(archive, resources / runtime_name)
        manifests = [*checked, runtime_name]
        (resources / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256((resources / name).read_bytes()).hexdigest()}  {name}\n"
                for name in manifests
            )
        )
        with (app / "Contents/Info.plist").open("wb") as target:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "org.spareagent.desktop",
                    "CFBundleName": "SpareAgent",
                    "CFBundleDisplayName": "SpareAgent",
                    "CFBundleExecutable": "SpareAgent",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": version,
                    "CFBundleVersion": str(build),
                    "LSMinimumSystemVersion": "13.0",
                    "LSUIElement": True,
                    "NSHighResolutionCapable": True,
                },
                target,
            )
        if args.source_installer:
            source_installer(app, out / f"SpareAgent-local-install-{args.arch}.zip", args.arch)
            return
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-swift-version",
                "5",
                "-O",
                "-target",
                f"{args.arch}-apple-macos13.0",
                "-module-cache-path",
                str(cache / "swift"),
                str(ROOT / "apps/macos/SpareAgent.swift"),
                "-o",
                str(macos / "SpareAgent"),
            ],
            check=True,
        )
        if args.arch == platform.machine():
            subprocess.run([str(macos / "SpareAgent"), "--self-test"], check=True)
        # Cloud-synced build directories can add Finder metadata which codesign rejects.
        # Remove only these build-artifact attributes; retain quarantine/security attributes.
        for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
            subprocess.run(["xattr", "-dr", attribute, str(app)], capture_output=True, check=False)
        if args.sign_identity:
            sign_code(app, args.sign_identity)
        else:
            subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
        suffix = "-notarized" if args.notary_profile else "-signed" if args.sign_identity else ""
        candidate = Path(build_dir) / "submission.zip"
        zip_app(app, candidate)
        if args.notary_profile:
            notarize(app, candidate, args.notary_profile, out / f"notary-{args.arch}")
            zip_app(app, candidate)  # Include the stapled ticket in the final ZIP.
        extracted = Path(build_dir) / "archive-check"
        subprocess.run(["ditto", "-x", "-k", str(candidate), str(extracted)], check=True)
        subprocess.run(
            ["codesign", "--verify", "--strict", str(extracted / "SpareAgent.app")], check=True
        )
        if args.notary_profile:
            subprocess.run(
                ["xcrun", "stapler", "validate", str(extracted / "SpareAgent.app")], check=True
            )
        zip_path = out / f"SpareAgent-mac-{args.arch}{suffix}.zip"
        shutil.copyfile(candidate, zip_path)
        zip_path.chmod(0o600)
        if args.sign_identity:
            (out / f"signed-components-{args.arch}.json").write_text(
                json.dumps([*signed_binaries, "SpareAgent.app/Contents/MacOS/SpareAgent"], indent=2)
            )
        status = (
            "Developer ID signed and notarized; stapled ticket validated."
            if args.notary_profile
            else "Developer ID signed; not notarized."
            if args.sign_identity
            else "Ad-hoc signing only; Developer ID notarization is not included."
        )
        print(f"Private ZIP (App {version}, build {build}): {zip_path}\n{status}")


if __name__ == "__main__":
    main()
