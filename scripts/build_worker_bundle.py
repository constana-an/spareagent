"""Operator builds a worker-only wheel bundle; friends do not clone/install the backend.

With --api-url (and the one-click options) it also writes `bundle.json` and a
double-clickable `SpareAgent.command`, and zips everything as `SpareAgent-mac.zip`: a
newcomer unzips it, double-clicks and presses one button. The zip contains the signup code,
so share it only privately with people you would let create a sharing group.
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
WHEEL = "spareagent_worker-0.7.0-py3-none-any.whl"


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
    fd = os.open(out / "bundle.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2)
    launcher = out / "SpareAgent.command"
    shutil.copy2(ROOT / "scripts" / "SpareAgent.command", launcher)
    launcher.chmod(0o755)
    archive = out / "SpareAgent-mac.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in (WHEEL, "SHA256SUMS", "bundle.json", "SpareAgent.command"):
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
    args = p.parse_args()
    if args.api_url and not (args.signup_code_file and args.dashboard_url):
        p.error("--api-url needs --signup-code-file and --dashboard-url")

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
    wheel = out / WHEEL
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (out / "SHA256SUMS").write_text(f"{digest}  {wheel.name}\n")
    shutil.copy2(ROOT / "scripts" / "setup-worker-macos.sh", out)
    print(f"Bundle: {out}\nWorker SHA-256: {digest}")
    if args.api_url:
        write_bundle(out, args)


if __name__ == "__main__":
    main()
