"""Process-wide dependencies shared by the API and the local worker."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.api.config import Settings
from db.connection import make_engine, make_sessionmaker
from packages.provider_sdk.base import ProviderAdapter
from packages.provider_sdk.mock import MockProviderAdapter
from services.artifacts.store import LocalArtifactStore
from services.queue.publisher import WakePublisher


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    artifacts: LocalArtifactStore
    adapter: ProviderAdapter
    publisher: WakePublisher

    async def aclose(self) -> None:
        await self.engine.dispose()
        await self.redis.aclose()


def build_adapter(settings: Settings) -> ProviderAdapter:
    if settings.provider_mode == "mock":
        return MockProviderAdapter()
    raise RuntimeError(f"Unsupported provider mode {settings.provider_mode}")  # pragma: no cover


def build_context(settings: Settings, *, adapter: ProviderAdapter | None = None) -> AppContext:
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=5, decode_responses=True
    )
    return AppContext(
        settings=settings,
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        redis=redis,
        artifacts=LocalArtifactStore(settings.artifact_root),
        adapter=adapter or build_adapter(settings),
        publisher=WakePublisher(redis),
    )
