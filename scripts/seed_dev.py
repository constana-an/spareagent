"""Load development fixtures (repeatable): `python scripts/seed_dev.py`. Local only."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.api.config import load_settings  # noqa: E402
from db.migrator import seed_dev  # noqa: E402


def main() -> int:
    settings = load_settings()
    if settings.app_env != "local":
        print("refusing to seed: development fixtures require APP_ENV=local", file=sys.stderr)
        return 2
    applied = asyncio.run(seed_dev(settings.database_url))
    print(f"seed fixtures applied: {applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
