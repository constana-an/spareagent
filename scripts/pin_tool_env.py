"""Operator-local: pin the data-tools environment (design §4) in scripts/runtime-pins.json.

Resolves pandas, matplotlib and openpyxl for CPython 3.12 on each Mac CPU with pip's
dry-run report (no wheels are downloaded) and records every wheel's URL and SHA-256.
The owner's device later downloads exactly these files, checks each hash and installs
them offline into its own environment. Re-run to move to newer releases, review the diff
and rebuild the package.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PINS = Path(__file__).resolve().parent / "runtime-pins.json"
PACKAGES = ["pandas", "matplotlib", "openpyxl"]
PLATFORMS = {
    "arm64": [f"macosx_{v}_arm64" for v in ("11_0", "12_0", "13_0", "14_0")],
    "x86_64": [f"macosx_{v}_x86_64" for v in ("10_9", "10_13", "11_0", "12_0", "13_0", "14_0")],
}


def resolve(arch: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        argv = [sys.executable, "-m", "pip", "install", "-q", "--dry-run", "--ignore-installed"]
        argv += ["--only-binary=:all:", "--python-version", "3.12", "--implementation", "cp"]
        argv += ["--abi", "cp312", "--target", str(Path(tmp) / "t"), "--report", str(report)]
        for platform in PLATFORMS[arch]:
            argv += ["--platform", platform]
        subprocess.run([*argv, *PACKAGES], check=True)  # noqa: S603 - fixed argv
        data = json.loads(report.read_text())
    wheels = []
    for item in data["install"]:
        info = item["download_info"]
        url = info["url"]
        if not url.startswith("https://files.pythonhosted.org/") or not url.endswith(".whl"):
            raise SystemExit(f"unexpected download source: {url}")
        wheels.append(
            {
                "name": item["metadata"]["name"],
                "version": item["metadata"]["version"],
                "url": url,
                "sha256": info["archive_info"]["hashes"]["sha256"],
            }
        )
    for wheel in wheels:
        wheel["size"] = wheel_size(wheel)
    return sorted(wheels, key=lambda w: w["name"].lower())


def wheel_size(wheel: dict) -> int:
    """Size from PyPI's JSON API, cross-checked against the pinned SHA-256."""
    import urllib.request

    url = f"https://pypi.org/pypi/{wheel['name']}/{wheel['version']}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
        files = json.load(resp)["urls"]
    for f in files:
        if f["url"] == wheel["url"] and f["digests"]["sha256"] == wheel["sha256"]:
            return int(f["size"])
    raise SystemExit(f"{wheel['name']}: wheel not listed by PyPI")


def main() -> None:
    pins = json.loads(PINS.read_text())
    assets = {arch: resolve(arch) for arch in PLATFORMS}
    versions = {w["name"].lower(): w["version"] for w in assets["arm64"]}
    env_id = "-".join(f"{p}{versions[p]}" for p in PACKAGES)
    pins["tools"] = {
        "python-data": {
            "id": env_id,
            "packages": PACKAGES,
            "smoke_test": "import pandas, matplotlib, openpyxl; matplotlib.use('Agg')",
            "assets": assets,
        }
    }
    PINS.write_text(json.dumps(pins, indent=2) + "\n")
    for arch, wheels in assets.items():
        print(
            arch, len(wheels), "wheels:", ", ".join(f"{w['name']} {w['version']}" for w in wheels)
        )


if __name__ == "__main__":
    main()
