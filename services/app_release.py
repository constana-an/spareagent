"""In-app updates for the owner's Mac App (D-103).

The operator publishes a prebuilt App ZIP per CPU into the release directory with
`scripts/owner_install_link.py publish-update`, which writes `release.json`:

    {"version": "0.15.0", "build": 17,
     "apps": {"arm64": {"file": "SpareAgent-mac-arm64.zip", "sha256": "..."}}}

Registered devices read it and download the ZIP with their node credentials; the App
decides whether the build is newer and asks the owner before replacing itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RELEASE_FILE = "release.json"
ARCHES = ("arm64", "x86_64")


def load_release(directory: Path, arch: str) -> dict[str, Any] | None:
    """The published release for `arch`, or None when there is none or it is incomplete."""
    if arch not in ARCHES:
        return None
    try:
        data = json.loads((directory / RELEASE_FILE).read_text(encoding="utf-8"))
        entry = data["apps"][arch]
        path = directory / entry["file"]
        # Only a plain file name inside the release directory is served.
        if Path(entry["file"]).name != entry["file"] or not path.is_file():
            return None
        return {
            "version": str(data["version"]),
            "build": int(data["build"]),
            "sha256": str(entry["sha256"]),
            "size": path.stat().st_size,
            "path": path,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None
