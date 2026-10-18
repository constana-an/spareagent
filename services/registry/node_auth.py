"""Remote worker enrollment and node credentials (Week 2).

Credential model:
* Enrollment token (``sa_enr_``): issued by an ADMIN for any public-type resource, or by a
  PROVIDER for their own admin-APPROVED resource. Single use, short TTL.
* Node key (``sa_nk_``): long-lived per-node refresh secret returned once at registration;
  only exchangeable for access tokens. Rotatable and revocable.
* Access token (``sa_at_``): short-lived bearer for every node endpoint, scoped to exactly
  one worker id. Invalid once its node key is rotated/revoked or the worker is revoked.

Only HMAC-SHA256(pepper, secret) digests are persisted. Consumer/dev authentication is never
accepted as node authentication, and nothing here can change resource eligibility.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditLog, EnrollmentToken, NodeCredential, Resource, TaskAttempt, WorkerNode
from services.context import AppContext
from services.errors import DomainError, forbidden, not_found
from services.scheduler.policy import PERSONAL_SUBSCRIPTION, PUBLIC_RESOURCE_TYPES
from services.tasks.service import Principal

log = logging.getLogger(__name__)

ENROLLMENT_PREFIX = "sa_enr_"
NODE_KEY_PREFIX = "sa_nk_"
ACCESS_PREFIX = "sa_at_"
MAX_CAPABILITIES_BYTES = 4096


def utcnow() -> datetime:
    return datetime.now(UTC)


def unauthorized(code: str, message: str) -> DomainError:
    return DomainError(401, code, message)


@dataclass(frozen=True)
class NodePrincipal:
    worker_id: uuid.UUID
    resource_id: uuid.UUID
    credential_id: uuid.UUID


@dataclass(frozen=True)
class IssuedSecret:
    id: uuid.UUID
    secret: str
    expires_at: datetime


@dataclass(frozen=True)
class Registration:
    worker_id: uuid.UUID
    node_key: IssuedSecret
    access_token: IssuedSecret


class NodeAuthService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._pepper = ctx.settings.node_token_pepper.get_secret_value().encode()

    # ------------------------------------------------------------------ helpers
    def digest(self, secret: str) -> str:
        return hmac.new(self._pepper, secret.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _new_secret(prefix: str) -> str:
        return prefix + secrets.token_urlsafe(32)

    async def _issue(
        self,
        session: AsyncSession,
        worker_id: uuid.UUID,
        kind: str,
        ttl_seconds: int,
        parent_id: uuid.UUID | None = None,
    ) -> IssuedSecret:
        prefix = NODE_KEY_PREFIX if kind == "NODE_KEY" else ACCESS_PREFIX
        secret = self._new_secret(prefix)
        now = utcnow()
        cred = NodeCredential(
            id=uuid.uuid4(),
            worker_id=worker_id,
            kind=kind,
            parent_id=parent_id,
            secret_hash=self.digest(secret),
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        session.add(cred)
        await session.flush()
        return IssuedSecret(cred.id, secret, cred.expires_at)

    async def _issue_pair(self, session: AsyncSession, worker_id: uuid.UUID) -> Registration:
        s = self.ctx.settings
        node_key = await self._issue(session, worker_id, "NODE_KEY", s.node_key_ttl_seconds)
        access = await self._issue(
            session, worker_id, "ACCESS", s.node_access_token_ttl_seconds, parent_id=node_key.id
        )
        return Registration(worker_id, node_key, access)

    @staticmethod
    def _audit(
        session: AsyncSession, action: str, target_id: Any, actor: uuid.UUID | None, **meta: Any
    ) -> None:
        session.add(
            AuditLog(
                actor_id=actor,
                action=action,
                target_type="worker_node" if "worker" in action else "resource",
                target_id=str(target_id),
                metadata_=meta,
            )
        )

    @staticmethod
    def _bearer(authorization: str | None, prefix: str) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise unauthorized("NODE_AUTH_REQUIRED", "Node bearer credential required")
        token = authorization[7:].strip()
        if not token.startswith(prefix) or len(token) > 200:
            raise unauthorized("NODE_AUTH_INVALID", "Invalid node credential")
        return token

    # ------------------------------------------------------------------ enrollment
    async def create_enrollment_token(
        self, principal: Principal, resource_id: uuid.UUID, ttl_seconds: int
    ) -> IssuedSecret:
        if ttl_seconds > self.ctx.settings.enrollment_token_max_ttl_seconds:
            raise DomainError(422, "VALIDATION_ERROR", "ttl_seconds exceeds the maximum")
        async with self.ctx.sessionmaker() as session, session.begin():
            resource = await session.get(Resource, resource_id)
            if resource is None:
                raise not_found("RESOURCE_NOT_FOUND", "Resource not found")
            if resource.resource_type == PERSONAL_SUBSCRIPTION:
                raise DomainError(
                    422,
                    "POLICY_VIOLATION",
                    "PERSONAL_SUBSCRIPTION resources can never host marketplace workers",
                )
            if resource.resource_type not in PUBLIC_RESOURCE_TYPES:
                raise DomainError(422, "POLICY_VIOLATION", "Unsupported resource type")
            if principal.role == "ADMIN":
                pass
            elif principal.role == "PROVIDER":
                if resource.owner_id != principal.user_id:
                    raise not_found("RESOURCE_NOT_FOUND", "Resource not found")
                if resource.authorization_status != "APPROVED":
                    raise forbidden(
                        "RESOURCE_NOT_APPROVED",
                        "Resource must be approved by an administrator before enrolling nodes",
                    )
            else:
                raise forbidden("PROVIDER_OR_ADMIN_REQUIRED", "Only providers or admins enroll")
            secret = self._new_secret(ENROLLMENT_PREFIX)
            now = utcnow()
            token = EnrollmentToken(
                id=uuid.uuid4(),
                token_hash=self.digest(secret),
                resource_id=resource.id,
                issued_by=principal.user_id,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            session.add(token)
            self._audit(
                session,
                "enrollment_token.create",
                resource.id,
                principal.user_id,
                token=str(token.id),
            )
        return IssuedSecret(token.id, secret, token.expires_at)

    async def register(
        self,
        authorization: str | None,
        *,
        resource_id: uuid.UUID,
        name: str,
        capabilities: dict[str, Any],
        max_concurrency: int,
    ) -> Registration:
        secret = self._bearer(authorization, ENROLLMENT_PREFIX)
        if max_concurrency > self.ctx.settings.max_node_concurrency:
            raise DomainError(
                422,
                "VALIDATION_ERROR",
                f"max_concurrency exceeds {self.ctx.settings.max_node_concurrency}",
            )
        if len(json.dumps(capabilities)) > MAX_CAPABILITIES_BYTES:
            raise DomainError(422, "VALIDATION_ERROR", "capabilities too large")
        now = utcnow()
        async with self.ctx.sessionmaker() as session, session.begin():
            token = (
                await session.execute(
                    select(EnrollmentToken)
                    .where(EnrollmentToken.token_hash == self.digest(secret))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                token is None
                or token.used_at is not None
                or token.revoked_at is not None
                or token.expires_at <= now
            ):
                # One indistinguishable answer: no oracle for used/expired/unknown tokens.
                raise unauthorized("ENROLLMENT_INVALID", "Enrollment token invalid or expired")
            if token.resource_id != resource_id:
                raise forbidden("ENROLLMENT_SCOPE", "Enrollment token is for a different resource")
            resource = await session.get(Resource, resource_id)
            if resource is None or resource.resource_type == PERSONAL_SUBSCRIPTION:
                raise DomainError(422, "POLICY_VIOLATION", "Resource cannot host workers")
            worker = WorkerNode(
                id=uuid.uuid4(),
                resource_id=resource_id,
                name=name,
                status="OFFLINE",
                capabilities=capabilities,
                max_concurrency=max_concurrency,
            )
            session.add(worker)
            await session.flush()
            token.used_at = now
            token.worker_id = worker.id
            issued = await self._issue_pair(session, worker.id)
            self._audit(session, "worker.register", worker.id, None, enrollment=str(token.id))
        log.info("worker registered", extra={"worker_id": str(issued.worker_id)})
        return issued

    # ------------------------------------------------------------------ authentication
    async def authenticate_access(self, authorization: str | None) -> NodePrincipal:
        secret = self._bearer(authorization, ACCESS_PREFIX)
        now = utcnow()
        async with self.ctx.sessionmaker() as session:
            row = (
                await session.execute(
                    select(NodeCredential, WorkerNode)
                    .join(WorkerNode, WorkerNode.id == NodeCredential.worker_id)
                    .where(
                        NodeCredential.secret_hash == self.digest(secret),
                        NodeCredential.kind == "ACCESS",
                    )
                )
            ).one_or_none()
            if row is None:
                raise unauthorized("NODE_AUTH_INVALID", "Invalid node credential")
            cred, worker = row
            parent = await session.get(NodeCredential, cred.parent_id)
        if cred.revoked_at or parent is None or parent.revoked_at or worker.revoked_at:
            raise unauthorized("NODE_CREDENTIAL_REVOKED", "Node credential revoked")
        if cred.expires_at <= now:
            raise unauthorized("NODE_TOKEN_EXPIRED", "Node access token expired")
        if parent.expires_at <= now:
            raise unauthorized("NODE_KEY_EXPIRED", "Node key expired; re-enroll the node")
        return NodePrincipal(worker.id, worker.resource_id, cred.id)

    async def exchange_node_key(self, authorization: str | None) -> tuple[uuid.UUID, IssuedSecret]:
        secret = self._bearer(authorization, NODE_KEY_PREFIX)
        now = utcnow()
        async with self.ctx.sessionmaker() as session, session.begin():
            row = (
                await session.execute(
                    select(NodeCredential, WorkerNode)
                    .join(WorkerNode, WorkerNode.id == NodeCredential.worker_id)
                    .where(
                        NodeCredential.secret_hash == self.digest(secret),
                        NodeCredential.kind == "NODE_KEY",
                    )
                )
            ).one_or_none()
            if row is None:
                raise unauthorized("NODE_AUTH_INVALID", "Invalid node credential")
            key, worker = row
            if key.revoked_at or worker.revoked_at:
                raise unauthorized("NODE_CREDENTIAL_REVOKED", "Node credential revoked")
            if key.expires_at <= now:
                raise unauthorized("NODE_KEY_EXPIRED", "Node key expired; re-enroll the node")
            ttl = min(
                self.ctx.settings.node_access_token_ttl_seconds,
                max(1, int((key.expires_at - now).total_seconds())),
            )
            access = await self._issue(session, worker.id, "ACCESS", ttl, parent_id=key.id)
        return worker.id, access

    async def rotate(self, node: NodePrincipal) -> Registration:
        async with self.ctx.sessionmaker() as session, session.begin():
            current = await session.get(NodeCredential, node.credential_id)
            now = utcnow()
            # Revoke every live credential of this node, then issue a fresh pair.
            await session.execute(
                update(NodeCredential)
                .where(
                    NodeCredential.worker_id == node.worker_id, NodeCredential.revoked_at.is_(None)
                )
                .values(revoked_at=now)
            )
            issued = await self._issue_pair(session, node.worker_id)
            self._audit(
                session,
                "worker.credentials.rotate",
                node.worker_id,
                None,
                previous_key=str(current.parent_id) if current else None,
            )
        return issued

    async def revoke_worker(self, principal: Principal, worker_id: uuid.UUID) -> int:
        """ADMIN only. Revokes all node credentials and forces active leases to expire."""
        if principal.role != "ADMIN":
            raise forbidden("ADMIN_REQUIRED", "Only an administrator can revoke workers")
        now = utcnow()
        async with self.ctx.sessionmaker() as session, session.begin():
            worker = await session.get(WorkerNode, worker_id, with_for_update=True)
            if worker is None:
                raise not_found("WORKER_NOT_FOUND", "Worker not found")
            worker.revoked_at = worker.revoked_at or now
            worker.status = "OFFLINE"
            await session.execute(
                update(NodeCredential)
                .where(NodeCredential.worker_id == worker_id, NodeCredential.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            result = await session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.status.in_(("RESERVED", "RUNNING")),
                )
                .values(leased_until=now)
            )
            self._audit(session, "worker.revoke", worker_id, principal.user_id)
        return result.rowcount or 0
