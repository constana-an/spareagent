"""Schema: migration from empty DB, immutability, repeatable seed, ORM == SQL."""

import shutil

import asyncpg
import pytest

from db.migrator import MigrationError, asyncpg_dsn, discover, migrate, seed_dev
from db.models import ALL_TABLES
from tests.conftest import TABLES, TEST_DATABASE_URL

pytestmark = pytest.mark.db


async def _conn():
    return await asyncpg.connect(asyncpg_dsn(TEST_DATABASE_URL))


async def test_migration_applied_once_and_rerun_is_noop(migrated_database):
    assert await migrate(TEST_DATABASE_URL) == []
    conn = await _conn()
    try:
        versions = [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")]
    finally:
        await conn.close()
    assert versions == ["0001_initial", "0003_worker_auth"]


def test_seed_files_are_not_schema_migrations():
    assert [m.version for m in discover()] == ["0001_initial", "0003_worker_auth"]
    assert [m.version for m in discover(include_seed=True)] == [
        "0002_seed_dev",
        "0004_seed_dev_week2",
    ]
    assert "BEGIN;" not in discover()[0].sql and "COMMIT;" not in discover()[0].sql


async def test_modified_migration_is_refused(migrated_database, tmp_path):
    from db.migrator import MIGRATIONS_DIR

    for f in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    path = tmp_path / "0003_worker_auth.sql"
    path.write_text(path.read_text() + "\n-- tampered\n")
    with pytest.raises(MigrationError, match="immutable"):
        await migrate(TEST_DATABASE_URL, tmp_path)


async def test_failed_migration_rolls_back_entirely(migrated_database, tmp_path):
    from db.migrator import MIGRATIONS_DIR

    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", tmp_path / "0001_initial.sql")
    (tmp_path / "0099_broken.sql").write_text(
        "BEGIN;\nCREATE TABLE should_not_exist(id int);\nSELECT * FROM missing_table;\nCOMMIT;\n"
    )
    with pytest.raises(asyncpg.PostgresError):
        await migrate(TEST_DATABASE_URL, tmp_path)
    conn = await _conn()
    try:
        assert await conn.fetchval("SELECT to_regclass('should_not_exist')") is None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM schema_migrations WHERE version='0099_broken'"
            )
            == 0
        )
    finally:
        await conn.close()


async def test_seed_is_repeatable(migrated_database):
    conn = await _conn()
    try:
        await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    await seed_dev(TEST_DATABASE_URL)
    await seed_dev(TEST_DATABASE_URL)
    conn = await _conn()
    try:
        counts = [
            await conn.fetchval(f"SELECT count(*) FROM {t}")
            for t in ("users", "resources", "worker_nodes")
        ]
    finally:
        await conn.close()
    assert counts == [3, 2, 1]  # + Week 2 admin and a non-eligible personal resource


async def test_orm_models_match_sql_schema(migrated_database):
    conn = await _conn()
    try:
        rows = await conn.fetch(
            "SELECT table_name, column_name, is_nullable, data_type, character_maximum_length "
            "FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name <> 'schema_migrations'"
        )
    finally:
        await conn.close()
    live: dict[str, dict[str, tuple]] = {}
    for r in rows:
        live.setdefault(r["table_name"], {})[r["column_name"]] = (
            r["is_nullable"] == "YES",
            r["data_type"],
            r["character_maximum_length"],
        )
    type_map = {
        "UUID": "uuid",
        "Text": "text",
        "Boolean": "boolean",
        "Integer": "integer",
        "BigInteger": "bigint",
        "JSONB": "jsonb",
        "TIMESTAMP": "timestamp with time zone",
        "CHAR": "character",
    }
    assert set(live) == {t.name for t in ALL_TABLES}
    for table in ALL_TABLES:
        assert set(live[table.name]) == {c.name for c in table.columns}, table.name
        for col in table.columns:
            nullable, data_type, length = live[table.name][col.name]
            assert nullable == col.nullable, f"{table.name}.{col.name} nullability"
            assert type_map[type(col.type).__name__] == data_type, f"{table.name}.{col.name} type"
            if data_type == "character":
                assert col.type.length == length
