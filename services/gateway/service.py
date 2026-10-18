"""OpenAI-style chat gateway (non-stream only in Week 1)."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from db.models import GatewayRequest, Resource
from packages.provider_sdk.types import ChatInvocation, ChatMessage
from services.context import AppContext
from services.errors import DomainError
from services.gateway.aliases import GatewayAlias, default_aliases
from services.metering.service import record_usage
from services.scheduler.policy import ResourceEligibilityPolicy
from services.tasks.service import Principal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatOutcome:
    body: dict[str, Any]
    billable: bool


class GatewayService:
    def __init__(self, ctx: AppContext, aliases: tuple[GatewayAlias, ...] | None = None) -> None:
        self.ctx = ctx
        self.aliases = {a.alias: a for a in (aliases or default_aliases(ctx.settings))}
        self.adapters = {ctx.adapter.name: ctx.adapter}

    async def _routable(self, aliases: list[GatewayAlias]) -> list[GatewayAlias]:
        if not aliases:
            return []
        async with self.ctx.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Resource).where(Resource.id.in_([a.resource_id for a in aliases]))
                )
            ).scalars()
            by_id = {r.id: r for r in rows}
        return [
            a
            for a in aliases
            if a.provider in self.adapters
            and (res := by_id.get(a.resource_id)) is not None
            and ResourceEligibilityPolicy.is_gateway_routable(res)
        ]

    async def list_models(self) -> list[dict[str, str]]:
        routable = await self._routable(sorted(self.aliases.values(), key=lambda a: a.alias))
        return [{"id": a.alias, "object": "model", "owned_by": a.owned_by} for a in routable]

    async def _enforce_rate_limit(self, principal: Principal) -> None:
        window = int(time.time() // 60)
        key = f"spareagent:rl:gateway:{principal.user_id}:{window}"
        try:
            count = await self.ctx.redis.incr(key)
            if count == 1:
                await self.ctx.redis.expire(key, 120)
        except Exception as exc:  # noqa: BLE001 - limiter is best-effort in W1
            log.warning("rate limiter unavailable", extra={"err": str(exc)})
            return
        if count > self.ctx.settings.gateway_rate_limit_per_minute:
            raise DomainError(429, "RATE_LIMITED", "Gateway rate limit exceeded; retry later")

    async def chat(self, principal: Principal, request: dict[str, Any]) -> ChatOutcome:
        alias = self.aliases.get(request["model"])
        if alias is None or not await self._routable([alias]):
            raise DomainError(
                404, "MODEL_NOT_FOUND", f"Model '{request['model']}' is not available"
            )
        await self._enforce_rate_limit(principal)

        async with self.ctx.sessionmaker() as session, session.begin():
            record = GatewayRequest(
                id=uuid.uuid4(),
                consumer_id=principal.user_id,
                model_alias=alias.alias,
                status="STARTED",
            )
            session.add(record)
        gateway_request_id = record.id

        invocation = ChatInvocation(
            physical_model=alias.physical_model,
            messages=tuple(ChatMessage(m["role"], m["content"]) for m in request["messages"]),
            max_tokens=request.get("max_tokens"),
            temperature=request.get("temperature"),
        )
        try:
            result = await self.adapters[alias.provider].chat(invocation)
        except Exception as exc:
            async with self.ctx.sessionmaker() as session, session.begin():
                await session.execute(
                    update(GatewayRequest)
                    .where(GatewayRequest.id == gateway_request_id)
                    .values(status="FAILED")
                )
            log.warning("gateway upstream failed", extra={"model": alias.alias})
            raise DomainError(502, "UPSTREAM_ERROR", "Upstream provider error") from exc

        async with self.ctx.sessionmaker() as session, session.begin():
            await record_usage(session, result.usage, gateway_request_id=gateway_request_id)
            await session.execute(
                update(GatewayRequest)
                .where(GatewayRequest.id == gateway_request_id)
                .values(status="SUCCEEDED")
            )
        usage = result.usage
        body = {
            "id": f"chatcmpl-{gateway_request_id.hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": alias.alias,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.content},
                    "finish_reason": result.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.input_tokens + usage.output_tokens,
            },
        }
        return ChatOutcome(body=body, billable=usage.billable)
