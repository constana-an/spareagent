"""Operator builds a worker-only wheel bundle; friends do not clone/install the backend.

With --api-url (and the one-click options) it also writes `bundle.json` and a
double-clickable `SpareAgent.command`, and zips everything as `SpareAgent-mac.zip`: a
newcomer unzips it, double-clicks and presses one button. The zip contains the signup code,
so share it only privately with people you would let create a sharing group.

The package needs nothing preinstalled on the Mac: it carries the worker's pure-Python
dependency wheels and `runtime.json` (copied from `scripts/runtime-pins.json`), which pins
the official Python build the launcher downloads and the Codex CLI build the app downloads,
each with its checksum. SHA256SUMS covers every shipped file the launcher installs from.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEEL = "spareagent_worker-0.12.0-py3-none-any.whl"


def fetch_dependency_wheels(out: Path, cache: Path | None = None) -> None:
    """The worker's dependencies as wheels, so the launcher installs fully offline."""
    wheels = out / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            *(["--no-index", "--find-links", str(cache)] if cache else []),
            "--dest",
            str(wheels),
            str(out / WHEEL),
        ],
        check=True,
    )
    (wheels / WHEEL).unlink(missing_ok=True)
    native = [p.name for p in wheels.glob("*.whl") if not p.name.endswith("-none-any.whl")]
    if native:
        raise SystemExit(f"dependency wheels must be pure Python, got {native}")


def parse_network(spec: str) -> dict:
    """`id:Label=API_URL,DASHBOARD_URL`, e.g. `lan:局域网=https://10.0.0.16:18443,https://10.0.0.16:13443`."""
    head, _, urls = spec.partition("=")
    net_id, _, label = head.partition(":")
    api_url, _, dashboard_url = urls.partition(",")
    if not (
        net_id.isidentifier()
        and api_url.startswith("https://")
        and dashboard_url.startswith("https://")
    ):
        raise SystemExit(f"--network must look like id:Label=https://API,https://DASHBOARD: {spec}")
    return {
        "id": net_id,
        "label": label or net_id,
        "api_url": api_url,
        "dashboard_url": dashboard_url,
    }


def write_bundle(out: Path, args: argparse.Namespace) -> None:
    code = Path(args.signup_code_file).read_text(encoding="utf-8").strip()
    if len(code) < 16:
        raise SystemExit("signup code must be at least 16 characters")
    bundle = {
        "api_url": args.api_url,
        "dashboard_url": args.dashboard_url,
        "terms_version": args.terms_version,
        "signup_code": code,
        "ca_pem": Path(args.ca_file).read_text(encoding="utf-8") if args.ca_file else None,
    }
    if args.network:
        bundle["networks"] = [parse_network(n) for n in args.network]
        # The first network stays the default for older apps that read only these two.
        bundle["api_url"] = bundle["networks"][0]["api_url"]
        bundle["dashboard_url"] = bundle["networks"][0]["dashboard_url"]
    fd = os.open(out / "bundle.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2)
    launcher = out / "SpareAgent.command"
    shutil.copy2(ROOT / "scripts" / "SpareAgent.command", launcher)
    launcher.chmod(0o755)
    deps = sorted(p.relative_to(out).as_posix() for p in (out / "wheels").glob("*.whl"))
    checked = [WHEEL, *deps, "runtime.json"]
    (out / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256((out / n).read_bytes()).hexdigest()}  {n}\n" for n in checked)
    )
    archive = out / "SpareAgent-mac.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in (*checked, "SHA256SUMS", "bundle.json", "SpareAgent.command"):
            info = zipfile.ZipInfo(f"SpareAgent/{name}")
            info.external_attr = (0o755 if name.endswith(".command") else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (out / name).read_bytes())
    os.chmod(archive, 0o600)
    print(f"One-click package (private: contains the signup code): {archive}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url", help="worker API URL, e.g. https://192.168.1.20:18443")
    p.add_argument("--dashboard-url", help="dashboard origin, e.g. https://192.168.1.20:13443")
    p.add_argument("--signup-code-file", help="file holding SELF_SERVE_SIGNUP_CODE")
    p.add_argument("--terms-version", default="2026-10-experimental-v1")
    p.add_argument("--ca-file", help="public CA certificate (ca.pem) for a private HTTPS server")
    p.add_argument("--output-dir", type=Path, default=ROOT / "output/worker-bundle")
    p.add_argument(
        "--dependency-cache", type=Path, help="resolve dependencies from these local wheels"
    )
    p.add_argument("--no-build-isolation", action="store_true")
    p.add_argument(
        "--network",
        action="append",
        help="a way to reach the server, repeatable; the app lets the owner pick one or "
        "picks the first that answers: id:Label=https://API,https://DASHBOARD",
    )
    args = p.parse_args()
    if args.network and not args.api_url:
        args.api_url, _, args.dashboard_url = args.network[0].partition("=")[2].partition(",")
    if args.api_url and not (args.signup_code_file and args.dashboard_url):
        p.error("--api-url needs --signup-code-file and --dashboard-url")

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # setuptools reuses packages/worker/build: stale copies of moved or deleted modules
    # would otherwise ship in the wheel.
    shutil.rmtree(ROOT / "packages" / "worker" / "build", ignore_errors=True)
    for old in out.glob("spareagent_worker-*.whl"):
        old.unlink()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            *(["--no-build-isolation"] if args.no_build_isolation else []),
            "--wheel-dir",
            str(out),
            str(ROOT / "packages" / "worker"),
        ],
        check=True,
    )
    wheel = out / WHEEL
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (out / "SHA256SUMS").write_text(f"{digest}  {wheel.name}\n")
    shutil.copy2(ROOT / "scripts" / "setup-worker-macos.sh", out)
    print(f"Bundle: {out}\nWorker SHA-256: {digest}")
    if args.api_url:
        fetch_dependency_wheels(out, args.dependency_cache)
        shutil.copy2(ROOT / "scripts" / "runtime-pins.json", out / "runtime.json")
        write_bundle(out, args)


if __name__ == "__main__":
    main()
