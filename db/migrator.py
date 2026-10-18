"""Minimal, transactional SQL migration runner.

* Applies `db/migrations/NNNN_*.sql` in order, skipping `*_seed_*` fixtures.
* Each file runs inside ONE transaction together with its tracking row, so a failure
  leaves neither a partial schema nor a stale tracking entry. The files' own top-level
  `BEGIN;`/`COMMIT;` lines are stripped because the runner owns the transaction.
* Stores a SHA-256 checksum per migration and refuses to run if a shipped migration
  was modified afterwards (migrations are immutable once applied).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_NAME = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
_TX_LINE = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.IGNORECASE | re.MULTILINE)

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  checksum char(64) NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str
    checksum: str


def asyncpg_dsn(database_url: str) -> str:
    """Translate an SQLAlchemy URL (postgresql+asyncpg://) into a plain libpq DSN."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", database_url)


def strip_transaction_control(sql: str) -> str:
    return _TX_LINE.sub("", sql)


def discover(directory: Path = MIGRATIONS_DIR, *, include_seed: bool = False) -> list[Migration]:
    found = []
    for path in sorted(directory.glob("*.sql")):
        if not _MIGRATION_NAME.match(path.name):
            raise MigrationError(f"Unexpected migration filename: {path.name}")
        is_seed = "_seed_" in path.name
        if is_seed != include_seed:
            continue
        raw = path.read_text(encoding="utf-8")
        found.append(
            Migration(
                version=path.stem,
                path=path,
                sql=strip_transaction_control(raw),
                checksum=hashlib.sha256(raw.encode()).hexdigest(),
            )
        )
    return found


async def migrate(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied in this run."""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    applied_now: list[str] = []
    try:
        await conn.execute(TRACKING_DDL)
        # Serialise concurrent migrators.
        await conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
            applied = {r["version"]: r["checksum"].strip() for r in rows}
            for migration in discover(directory):
                if migration.version in applied:
                    if applied[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"Migration {migration.version} changed after being applied; "
                            "shipped migrations are immutable - add a new numbered migration."
                        )
                    continue
                async with conn.transaction():
                    await conn.execute(migration.sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations(version, checksum) VALUES ($1, $2)",
                        migration.version,
                        migration.checksum,
                    )
                applied_now.append(migration.version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727001)")
    finally:
        await conn.close()
    return applied_now


async def seed_dev(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply development seed fixtures. They are idempotent (ON CONFLICT DO NOTHING)."""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    names = []
    try:
        for seed in discover(directory, include_seed=True):
            async with conn.transaction():
                await conn.execute(seed.sql)
            names.append(seed.version)
    finally:
        await conn.close()
    return names
