"""Worker control-plane endpoints (contracted for W2).

Node credentials (signed, short-lived, scoped to node + lease ids) are Week 2. Accepting
consumer/dev user tokens here would let a user act as a worker, so in Week 1 every call is
refused with 403. The local mock worker uses the same LeaseService in-process instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from services.errors import forbidden

router = APIRouter(prefix="/v1", tags=["workers"])


def _node_auth_unavailable() -> None:
    raise forbidden(
        "NODE_AUTH_UNAVAILABLE",
        "Worker node credentials are not available until Week 2; remote workers are disabled",
    )


@router.post(
    "/workers/register", operation_id="registerWorker", status_code=201, response_model=None
)
async def register_worker() -> None:
    _node_auth_unavailable()


@router.post(
    "/workers/{worker_id}/heartbeat",
    operation_id="workerHeartbeat",
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def worker_heartbeat(worker_id: str) -> None:
    _node_auth_unavailable()


@router.post(
    "/leases/{lease_id}/accept",
    operation_id="acceptLease",
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def accept_lease(lease_id: str) -> None:
    _node_auth_unavailable()


@router.post("/leases/{lease_id}/complete", operation_id="completeLease", response_model=None)
async def complete_lease(lease_id: str) -> None:
    _node_auth_unavailable()
