"""Application settings with fail-closed validation.

Settings are read from environment variables only (no implicit .env loading), so the
process environment is the single source of truth inside containers.
"""

from __future__ import annotations

import ipaddress
import uuid
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LOCAL_WORKER = "44444444-4444-4444-8444-444444444444"
DEFAULT_MOCK_CHAT_RESOURCE = "33333333-3333-4333-8333-333333333333"
# Development-only HMAC pepper for credential digests; refused outside APP_ENV=local.
DEV_NODE_TOKEN_PEPPER = "dev-only-node-token-pepper-change-me"  # noqa: S105


class ConfigError(ValueError):
    """Raised when the configuration is unsafe or unsupported."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_env: Literal["local", "test", "staging", "production"] = "local"
    # Fail closed: dev auth must be switched on explicitly.
    dev_auth_only: bool = False
    dev_auth_trusted_cidrs: str = "127.0.0.1/32,::1/128"

    friends_auth_enabled: bool = False
    friends_web_origin: str = "http://localhost:3000"
    # More dashboard origins for the same server on other networks (comma-separated), e.g.
    # the Tailscale address next to the LAN one. `friends_web_origin` stays the primary.
    friends_web_extra_origins: str = ""
    # D-102: the dashboard origin is reachable from the internet (Tailscale Funnel), so
    # members need only a browser. Devices and the API stay on the private network.
    friends_web_public: bool = False
    friends_session_hours: int = Field(default=12, ge=1, le=24)
    # Sessions started by joining with a group invite code (members on their own browser).
    friends_member_session_days: int = Field(default=90, ge=1, le=365)

    database_url: str = "postgresql+asyncpg://spareagent:spareagent@localhost:55432/spareagent"
    redis_url: str = "redis://localhost:56379/0"

    # Only the deterministic mock is implemented in Week 1. Anything else is a hard failure.
    provider_mode: str = "mock"
    openai_api_key: SecretStr | None = None

    log_level: str = "INFO"
    artifact_root: Path = Path("/var/lib/spareagent/artifacts")
    # D-103: prebuilt Mac App releases offered to registered devices as in-app updates.
    app_release_dir: Path = Path("/var/lib/spareagent/app-release")

    lease_seconds: int = Field(default=60, ge=1, le=3600)
    worker_heartbeat_ttl_seconds: int = Field(default=30, ge=1, le=3600)
    retry_backoff_base_seconds: float = Field(default=1.0, ge=0, le=300)
    budget_safety_margin_percent: int = Field(default=10, ge=0, le=100)
    gateway_rate_limit_per_minute: int = Field(default=60, ge=1, le=100000)
    sse_poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    sse_keepalive_seconds: float = Field(default=15.0, gt=0, le=300)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)

    # Week 2 remote worker protocol
    node_token_pepper: SecretStr = SecretStr(DEV_NODE_TOKEN_PEPPER)
    node_access_token_ttl_seconds: int = Field(default=900, ge=10, le=3600)
    node_key_ttl_seconds: int = Field(default=30 * 86400, ge=60, le=90 * 86400)
    enrollment_token_max_ttl_seconds: int = Field(default=86400, ge=60, le=7 * 86400)
    max_node_concurrency: int = Field(default=4, ge=1, le=100)
    lease_poll_max_wait_seconds: float = Field(default=25.0, ge=0, le=60)
    lease_poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    # 0012: room for a task's deliverables (each ≤ 5 MB, together ≤ 12 MB) plus logs.
    max_completion_artifact_bytes: int = Field(default=13 * 1024 * 1024, ge=1024)

    # Week 3 sandboxed code execution (disabled by default; D-35)
    code_execution_enabled: bool = False
    code_execution_resource_ids: str = ""
    code_task_estimate_cents: int = Field(default=25, ge=1, le=100000)
    repo_fixture_root: str | None = None
    sandbox_image: str = "spareagent-sandbox:dev"
    verification_timeout_seconds: float = Field(default=60.0, ge=1, le=600)
    verification_memory_mb: int = Field(default=512, ge=64, le=8192)
    verification_max_errors: int = Field(default=3, ge=1, le=20)
    verification_claim_seconds: int = Field(default=300, ge=10, le=3600)

    # Week 4 experimental subscription-sharing scope (invitation-only, non-commercial).
    # Process-wide operator kill switch; defaults OFF. Cross-user dispatch additionally needs
    # an operator-verified authorization record, an enabled group and owner consent, and is
    # LIVE additionally needs the separate operator switch and every authorization gate.
    experiment_enabled: bool = False
    experiment_terms_version: str = "2026-10-experimental-v1"
    experiment_max_group_members: int = Field(default=5, ge=2, le=20)
    # Milestone D operator switch for LIVE cross-user execution on owners' own Codex CLI.
    # Off by default; even when on, each dispatch needs a verified, unexpired,
    # route-covered authorization plus the owner's codex-check and credits attestations.
    experiment_live_enabled: bool = False
    # One-click sharing: anyone holding the operator's signup code (shipped inside the
    # worker bundle) can create their own provider account and invite-code group. Each new
    # group copies the operator-verified template authorization, so the operator still
    # decides which written permission covers self-serve groups and can revoke it.
    self_serve_enabled: bool = False
    self_serve_signup_code: SecretStr | None = None
    self_serve_authorization_id: uuid.UUID | None = None

    local_worker_node_ids: str = DEFAULT_LOCAL_WORKER
    mock_chat_resource_id: uuid.UUID = uuid.UUID(DEFAULT_MOCK_CHAT_RESOURCE)

    @property
    def friends_web_origins(self) -> tuple[str, ...]:
        extra = (o.strip() for o in self.friends_web_extra_origins.split(","))
        return (self.friends_web_origin, *(o for o in extra if o and o != self.friends_web_origin))

    @field_validator("self_serve_signup_code", "self_serve_authorization_id", mode="before")
    @classmethod
    def _blank_is_unset(cls, value):
        return None if value == "" else value

    @field_validator("provider_mode")
    @classmethod
    def _known_provider_mode(cls, value: str) -> str:
        if value != "mock":
            raise ConfigError(
                f"Unsupported PROVIDER_MODE={value!r}. Week 1 implements only 'mock'; "
                "live provider adapters are explicit opt-in and not part of this build."
            )
        return value

    @model_validator(mode="after")
    def _dev_auth_only_local(self) -> Settings:
        if self.dev_auth_only and self.app_env != "local":
            raise ConfigError(
                "DEV_AUTH_ONLY=true is only permitted with APP_ENV=local; refusing to start."
            )
        if (
            self.app_env != "local"
            and self.node_token_pepper.get_secret_value() == DEV_NODE_TOKEN_PEPPER
        ):
            raise ConfigError("NODE_TOKEN_PEPPER must be set to a secret value outside local")
        if len(self.node_token_pepper.get_secret_value()) < 16:
            raise ConfigError("NODE_TOKEN_PEPPER must be at least 16 characters")
        if self.repo_fixture_root and self.app_env != "local":
            raise ConfigError("REPO_FIXTURE_ROOT (local Git mirrors) is only allowed in local")
        if self.friends_auth_enabled:
            from urllib.parse import urlsplit

            for value in self.friends_web_origins:
                origin = urlsplit(value)
                if (
                    origin.scheme not in ("http", "https")
                    or not origin.hostname
                    or origin.username
                    or origin.password
                    or origin.path
                    or origin.query
                    or origin.fragment
                    or (self.app_env != "local" and origin.scheme != "https")
                ):
                    raise ConfigError("FRIENDS_WEB_ORIGIN(S) must be origins; HTTPS outside local")
        if self.self_serve_enabled:
            code = self.self_serve_signup_code
            if code is None or len(code.get_secret_value()) < 16:
                raise ConfigError("SELF_SERVE_SIGNUP_CODE must be at least 16 characters")
            if self.self_serve_authorization_id is None or not self.experiment_enabled:
                raise ConfigError(
                    "SELF_SERVE_ENABLED needs EXPERIMENT_ENABLED and SELF_SERVE_AUTHORIZATION_ID"
                )
        self.code_resource_ids  # noqa: B018 - validate UUIDs eagerly
        # Validate CIDRs eagerly so a typo cannot silently widen or break the trust boundary.
        self.trusted_networks  # noqa: B018
        return self

    @cached_property
    def trusted_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        nets = []
        for raw in self.dev_auth_trusted_cidrs.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                nets.append(ipaddress.ip_network(raw, strict=False))
            except ValueError as exc:
                raise ConfigError(f"Invalid DEV_AUTH_TRUSTED_CIDRS entry: {raw!r}") from exc
        return tuple(nets)

    @cached_property
    def code_resource_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(
            uuid.UUID(p.strip()) for p in self.code_execution_resource_ids.split(",") if p.strip()
        )

    @cached_property
    def local_worker_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(
            uuid.UUID(part.strip())
            for part in self.local_worker_node_ids.split(",")
            if part.strip()
        )


def load_settings() -> Settings:
    return Settings()
