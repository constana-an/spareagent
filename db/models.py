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
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)  # 0003


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    repository_url: Mapped[str | None] = mapped_column(Text)  # 0011: code kinds only
    commit_sha: Mapped[str | None] = mapped_column(CHAR(40))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    resource_scope: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'PUBLIC_MARKETPLACE'")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CREATED'"))
    max_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    execution_profile: Mapped[str] = mapped_column(  # 0005
        Text, nullable=False, server_default=text("'MOCK'")
    )
    spec: Mapped[dict[str, Any]] = mapped_column(  # 0011
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    kind_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


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
    source: Mapped[str] = mapped_column(  # 0005
        Text, nullable=False, server_default=text("'WORKER'")
    )
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    name: Mapped[str | None] = mapped_column(Text)  # 0012: DELIVERABLE file name


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


class EnrollmentToken(Base):
    """0003: admin/provider-issued, single-use registration secret (digest only)."""

    __tablename__ = "enrollment_tokens"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id"), nullable=False
    )
    issued_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZ)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("worker_nodes.id")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))


class NodeCredential(Base):
    """0003: long-lived NODE_KEY (refresh) and short-lived ACCESS tokens (digest only)."""

    __tablename__ = "node_credentials"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    worker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("worker_nodes.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("node_credentials.id")
    )
    secret_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)


class VerificationResult(Base):
    """0005: platform-side verification outcome, separate from worker-reported results."""

    __tablename__ = "verification_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    base_commit_sha: Mapped[str | None] = mapped_column(CHAR(40))  # 0012: null for documents
    patch_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    worker_reported: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    verified_result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    verifier_id: Mapped[str] = mapped_column(Text, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    claimed_until: Mapped[datetime | None] = mapped_column(TZ)
    started_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(TZ)


# ---------------------------------------------------------------- 0007 experimental scope
def _fk(target: str, *, nullable: bool = False, **kw: Any) -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target), nullable=nullable, **kw)


def _jsonb(default: str) -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text(f"'{default}'::jsonb"))


def _created() -> Any:
    return mapped_column(TZ, nullable=False, server_default=text("now()"))


class ExperimentalGroup(Base):
    __tablename__ = "experimental_groups"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users.id")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    sharing_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    max_members: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    max_concurrent_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    max_tasks_per_day: Mapped[int | None] = mapped_column(
        Integer().evaluates_none(), nullable=True, server_default=text("5")
    )
    max_task_duration_seconds: Mapped[int | None] = mapped_column(
        Integer().evaluates_none(), nullable=True, server_default=text("900")
    )
    allowed_task_types: Mapped[list[str]] = _jsonb('["CODE_REVIEW","WRITE_TESTS","CODE_FIX"]')
    created_at: Mapped[datetime] = _created()
    disabled_at: Mapped[datetime | None] = mapped_column(TZ)
    # 0010: self-serve groups are owned by one provider and joined with an invite code.
    owner_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    invite_code_digest: Mapped[str | None] = mapped_column(CHAR(64), unique=True)
    invite_code_rotated_at: Mapped[datetime | None] = mapped_column(TZ)


class ExperimentalGroupMember(Base):
    __tablename__ = "experimental_group_members"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    group_id: Mapped[uuid.UUID] = _fk("experimental_groups.id")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'INVITED'"))
    invited_by: Mapped[uuid.UUID] = _fk("users.id")
    invited_at: Mapped[datetime] = _created()
    joined_at: Mapped[datetime | None] = mapped_column(TZ)
    removed_at: Mapped[datetime | None] = mapped_column(TZ)
    terms_version: Mapped[str | None] = mapped_column(Text)
    acknowledgements: Mapped[dict[str, Any]] = _jsonb("{}")
    access_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 0016: owner-set caps for this member per rolling 24 h (NULL = group/device caps only)
    max_tasks_per_day: Mapped[int | None] = mapped_column(Integer)
    max_minutes_per_day: Mapped[int | None] = mapped_column(Integer)


class ExperimentalAuthorization(Base):
    __tablename__ = "experimental_authorizations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    group_id: Mapped[uuid.UUID] = _fk("experimental_groups.id")
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_source: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_reference: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_scope: Mapped[dict[str, Any]] = _jsonb("{}")
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'UNVERIFIED'")
    )
    integration_route_covered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    verified_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(TZ)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_by: Mapped[uuid.UUID] = _fk("users.id")
    created_at: Mapped[datetime] = _created()
    integration_route: Mapped[str | None] = mapped_column(Text)  # 0008
    max_participants: Mapped[int | None] = mapped_column(Integer)  # 0008


class SubscriptionConnection(Base):
    __tablename__ = "subscription_connections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    owner_id: Mapped[uuid.UUID] = _fk("users.id")
    worker_id: Mapped[uuid.UUID] = _fk("worker_nodes.id", unique=True)
    provider_name: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'openai-codex'")
    )
    subscription_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'UNKNOWN'")
    )
    auth_method: Mapped[str] = mapped_column(Text, nullable=False)
    auth_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'VALID'"))
    device_label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CONNECTED'"))
    last_auth_check_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()
    owner_check_passed_at: Mapped[datetime | None] = mapped_column(TZ)  # 0008
    no_purchased_credits_at: Mapped[datetime | None] = mapped_column(TZ)  # 0008


class SubscriptionSharingPreference(Base):
    __tablename__ = "subscription_sharing_preferences"
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscription_connections.id"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = _fk("experimental_groups.id")
    sharing_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'DISABLED'")
    )
    consent_terms_version: Mapped[str | None] = mapped_column(Text)
    consent_accepted_at: Mapped[datetime | None] = mapped_column(TZ)
    max_concurrent_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    max_tasks_per_day: Mapped[int | None] = mapped_column(
        Integer().evaluates_none(), nullable=True, server_default=text("5")
    )
    max_task_duration_seconds: Mapped[int | None] = mapped_column(
        Integer().evaluates_none(), nullable=True, server_default=text("900")
    )
    allowed_task_types: Mapped[list[str]] = _jsonb('["CODE_REVIEW","WRITE_TESTS","CODE_FIX"]')
    allowed_tools: Mapped[list[str]] = _jsonb(
        '["code","doc","git","network","python-data"]'
    )  # 0015
    require_owner_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    advance_consent_requester_ids: Mapped[list[str]] = _jsonb("[]")
    # 0010: standing approval for tasks from active members of the owner's group.
    auto_approve_members: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    allow_paid_credits: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    paused_at: Mapped[datetime | None] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    updated_at: Mapped[datetime] = _created()


class SubscriptionUsageObservation(Base):
    __tablename__ = "subscription_usage_observations"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = _fk("subscription_connections.id")
    task_attempt_id: Mapped[uuid.UUID | None] = _fk("task_attempts.id", nullable=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    capacity_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'UNKNOWN'")
    )
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reset_hint: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = _created()


class ExperimentalTaskConsent(Base):
    __tablename__ = "experimental_task_consents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    task_id: Mapped[uuid.UUID] = _fk("tasks.id", unique=True)
    group_id: Mapped[uuid.UUID] = _fk("experimental_groups.id")
    requester_id: Mapped[uuid.UUID] = _fk("users.id")
    connection_id: Mapped[uuid.UUID] = _fk("subscription_connections.id")
    owner_id: Mapped[uuid.UUID] = _fk("users.id")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    consent_source: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


class ExperimentalDispatchAudit(Base):
    __tablename__ = "experimental_dispatch_audit"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = _fk("tasks.id")
    group_id: Mapped[uuid.UUID] = _fk("experimental_groups.id")
    connection_id: Mapped[uuid.UUID | None] = _fk("subscription_connections.id", nullable=True)
    authorization_id: Mapped[uuid.UUID | None] = _fk(
        "experimental_authorizations.id", nullable=True
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reasons: Mapped[list[str]] = _jsonb("[]")
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[uuid.UUID | None] = _fk("task_attempts.id", nullable=True)
    created_at: Mapped[datetime] = _created()


class FriendsLoginInvite(Base):
    __tablename__ = "friends_login_invites"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    token_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


class FriendsSession(Base):
    __tablename__ = "friends_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    token_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


class TaskKind(Base):  # 0011
    __tablename__ = "task_kinds"
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    needs_repository: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DEVELOPING'"))
    acceptance_report: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _created()
    updated_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)


class TaskUpload(Base):  # 0011
    __tablename__ = "task_uploads"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    owner_id: Mapped[uuid.UUID] = _fk("users.id")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    extension: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    task_id: Mapped[uuid.UUID | None] = _fk("tasks.id", nullable=True)
    position: Mapped[int | None] = mapped_column(Integer)


ALL_TABLES = [m.__table__ for m in Base.__subclasses__()]
