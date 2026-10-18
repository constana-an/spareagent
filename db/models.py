"""SQLAlchemy ORM models mirroring db/migrations/0001_initial.sql exactly.

The SQL migration is the source of truth: these models never create tables. A test
(`tests/integration/test_migrations.py`) compares them column-by-column against the
live schema produced by the migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, BigInteger, Boolean, ForeignKey, Identity, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = TIMESTAMP(timezone=True)
UUID_PK = {"primary_key": True, "server_default": text("gen_random_uuid()")}


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CONSUMER'"))
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'PENDING'")
    )
    public_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    license_reference: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'us-east-1'"))
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class WorkerNode(Base):
    __tablename__ = "worker_nodes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'OFFLINE'"))
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    active_tasks: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_heartbeat: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    repository_url: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(CHAR(40), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    resource_scope: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'PUBLIC_MARKETPLACE'")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CREATED'"))
    max_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False
    )
    worker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("worker_nodes.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, server_default=text("gen_random_uuid()")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    leased_until: Mapped[datetime] = mapped_column(TZ, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    finalization_key: Mapped[str | None] = mapped_column(Text, unique=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id")
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    task_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id")
    )
    gateway_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gateway_requests.id")
    )
    provider_request_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    estimated_cost_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    verified_cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class GatewayRequest(Base):
    __tablename__ = "gateway_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    model_alias: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


ALL_TABLES = [m.__table__ for m in Base.__subclasses__()]
