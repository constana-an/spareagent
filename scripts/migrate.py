"""Apply pending schema migrations: `python scripts/migrate.py`."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.api.config import load_settings  # noqa: E402
from db.migrator import migrate  # noqa: E402


def main() -> int:
    settings = load_settings()
    applied = asyncio.run(migrate(settings.database_url))
    print(f"migrations applied: {applied or 'none (up to date)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
