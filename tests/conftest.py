"""Shared fixtures.

Integration tests use real PostgreSQL + Redis (compose `test` service): an isolated
database `spareagent_test` (dropped/recreated and migrated once per session) and Redis DB
15. Every test starts from truncated tables plus a small deterministic fixture set.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from sqlalchemy import text

from apps.api.config import Settings
from apps.api.main import create_app
from db.migrator import asyncpg_dsn, migrate
from services.context import AppContext, build_context
from services.execution.local_worker import LocalWorker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://spareagent:spareagent@localhost:55432/spareagent_test",
)
TEST_ADMIN_DATABASE_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql+asyncpg://spareagent:spareagent@localhost:55432/postgres",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")

USER_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
USER_B = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")
PROVIDER = uuid.UUID("cccccccc-0000-4000-8000-00000000000c")
ADMIN = uuid.UUID("dddddddd-0000-4000-8000-00000000000d")
PUBLIC_RESOURCE = uuid.UUID("33333333-3333-4333-8333-333333333333")
PUBLIC_WORKER = uuid.UUID("44444444-4444-4444-8444-444444444444")

TABLES = (
    "audit_logs, usage_records, gateway_requests, artifacts, execution_events, "
    "task_attempts, tasks, worker_nodes, resources, users"
)

VALID_TASK = {
    "task_type": "WRITE_TESTS",
    "repository_url": "https://github.com/example/public-fixture",
    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
    "prompt": "Write two tests for the demo fixture",
    "max_budget_cents": 500,
    "max_duration_seconds": 120,
}


def _db_name(url: str) -> str:
    return url.rsplit("/", 1)[-1]


async def _recreate_and_migrate() -> None:
    name = _db_name(TEST_DATABASE_URL)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*_test", name):
        raise RuntimeError(f"refusing to recreate non-test database {name!r}")
    admin = await asyncpg.connect(asyncpg_dsn(TEST_ADMIN_DATABASE_URL))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    await migrate(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    asyncio.run(_recreate_and_migrate())
    return TEST_DATABASE_URL


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "app_env": "local",
        "dev_auth_only": True,
        "database_url": TEST_DATABASE_URL,
        "redis_url": TEST_REDIS_URL,
        "artifact_root": tmp_path / "artifacts",
        "retry_backoff_base_seconds": 0,
        "sse_poll_interval_seconds": 0.05,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


async def seed_fixtures(ctx: AppContext) -> None:
    async with ctx.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await conn.execute(
            text(
                "INSERT INTO users(id,email,role) VALUES "
                "(:a,'a@test.local','CONSUMER'),(:b,'b@test.local','CONSUMER'),"
                "(:p,'p@test.local','PROVIDER'),(:adm,'admin@test.local','ADMIN')"
            ),
            {"a": USER_A, "b": USER_B, "p": PROVIDER, "adm": ADMIN},
        )
        await conn.execute(
            text(
                "INSERT INTO resources(id,owner_id,resource_type,authorization_status,"
                "public_eligible,license_reference) VALUES "
                "(:r,:p,'MANAGED_API','APPROVED',true,'TEST_MOCK_ONLY')"
            ),
            {"r": PUBLIC_RESOURCE, "p": PROVIDER},
        )
        await conn.execute(
            text(
                "INSERT INTO worker_nodes(id,resource_id,name,status,max_concurrency,"
                "last_heartbeat) VALUES (:w,:r,'mock-test','IDLE',1,:hb)"
            ),
            {"w": PUBLIC_WORKER, "r": PUBLIC_RESOURCE, "hb": datetime.now(UTC)},
        )
    await ctx.redis.flushdb()


@pytest.fixture
async def ctx(migrated_database, settings) -> AsyncIterator[AppContext]:
    context = build_context(settings)
    await seed_fixtures(context)
    try:
        yield context
    finally:
        await context.aclose()


@pytest.fixture
async def app(ctx):
    return create_app(ctx.settings, ctx)


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def headers(user: uuid.UUID, key: str | None = None) -> dict[str, str]:
    h = {"X-Dev-User-ID": str(user)}
    if key is not None:
        h["Idempotency-Key"] = key
    return h


async def create_task(client, user=USER_A, key=None, **overrides) -> httpx.Response:
    body = {**VALID_TASK, **overrides}
    return await client.post(
        "/v1/tasks", json=body, headers=headers(user, key or f"key-{uuid.uuid4().hex}")
    )


async def drain_worker(
    ctx: AppContext, *, rounds: int = 5, nodes: tuple[uuid.UUID, ...] = (PUBLIC_WORKER,)
) -> None:
    worker = LocalWorker(ctx, nodes)
    for _ in range(rounds):
        if not await worker.run_once():
            break


async def fetch_val(ctx: AppContext, sql: str, **params):
    async with ctx.engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def execute(ctx: AppContext, sql: str, **params) -> None:
    async with ctx.engine.begin() as conn:
        await conn.execute(text(sql), params)
