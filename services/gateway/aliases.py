"""Stable public model aliases -> server-side provider/model/resource binding.

Callers only ever name an alias; credentials and physical models are never selectable.
Each alias is bound to a registry resource and is only listed/routable while that
resource passes `ResourceEligibilityPolicy.is_gateway_routable` (never PERSONAL_SUBSCRIPTION).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from apps.api.config import Settings


@dataclass(frozen=True)
class GatewayAlias:
    alias: str
    provider: str
    physical_model: str
    resource_id: uuid.UUID
    owned_by: str = "spareagent"


def default_aliases(settings: Settings) -> tuple[GatewayAlias, ...]:
    return (
        GatewayAlias(
            alias="mock-chat",
            provider="mock",
            physical_model="mock-chat-v1",
            resource_id=settings.mock_chat_resource_id,
        ),
    )
