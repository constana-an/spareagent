"""Node (remote worker) authentication.

Accepts ONLY `Authorization: Bearer sa_at_...` access tokens. `X-Dev-User-ID` and any
consumer credential are ignored here, so development consumer auth can never act as a node.
"""

from __future__ import annotations

from fastapi import Request

from services.registry.node_auth import NodeAuthService, NodePrincipal


def get_node_auth(request: Request) -> NodeAuthService:
    return request.app.state.node_auth


async def get_node_principal(request: Request) -> NodePrincipal:
    service: NodeAuthService = request.app.state.node_auth
    return await service.authenticate_access(request.headers.get("authorization"))
