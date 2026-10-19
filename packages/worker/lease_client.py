"""HTTPS client for the SpareAgent node API (outbound only; no inbound ports).

Plain HTTP is refused unless explicitly allowed AND the host is loopback (local development
and in-process tests). Access tokens are refreshed from the node key automatically.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

INSECURE_OK_HOSTS = frozenset({"localhost", "testserver"})
REFRESH_CODES = frozenset({"NODE_TOKEN_EXPIRED", "NODE_AUTH_INVALID", "NODE_AUTH_REQUIRED"})


class WorkerApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class TransportError(Exception):
    """Network-level failure (connection refused, TLS error, timeout). Retry with backoff."""


def validate_api_url(url: str, allow_insecure_http: bool) -> str:
    parts = urlsplit(url)
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError("API URL must be an origin without credentials, path, query or fragment")
    if parts.scheme == "https":
        return url.rstrip("/")
    if parts.scheme == "http" and allow_insecure_http:
        host = (parts.hostname or "").lower()
        loopback = host in INSECURE_OK_HOSTS
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if loopback:
            return url.rstrip("/")
    raise ValueError("API URL must use https:// (plain http is only allowed for loopback dev)")


def _error(resp: httpx.Response) -> WorkerApiError:
    try:
        err = resp.json()["error"]
        return WorkerApiError(resp.status_code, err.get("code", "ERROR"), err.get("message", ""))
    except Exception:  # noqa: BLE001
        return WorkerApiError(resp.status_code, "HTTP_ERROR", resp.reason_phrase)


class NodeClient:
    def __init__(
        self,
        api_url: str,
        *,
        worker_id: str | None = None,
        node_key: str | None = None,
        ca_file: str | None = None,
        allow_insecure_http: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_url = validate_api_url(api_url, allow_insecure_http)
        self.worker_id = worker_id
        self.node_key = node_key
        self._access: str | None = None
        self._access_expires: datetime | None = None
        self._http = httpx.AsyncClient(
            base_url=self.api_url,
            verify=ca_file if ca_file else True,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,  # no inherited proxy / netrc credentials on node requests
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(
        self, method: str, path: str, token: str | None, **kwargs: Any
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            return await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from exc

    # ---------------------------------------------------------------- credentials
    async def register(
        self,
        enrollment_token: str,
        *,
        resource_id: str,
        name: str,
        capabilities: dict[str, Any],
        max_concurrency: int,
    ) -> dict[str, Any]:
        resp = await self._send(
            "POST",
            "/v1/workers/register",
            enrollment_token,
            json={
                "resource_id": resource_id,
                "name": name,
                "capabilities": capabilities,
                "max_concurrency": max_concurrency,
            },
        )
        if resp.status_code != 201:
            raise _error(resp)
        data = resp.json()
        self.worker_id = data["worker_id"]
        self.node_key = data["node_key"]
        self._set_access(data["access_token"], data["access_token_expires_at"])
        return data

    def _set_access(self, token: str, expires_at: str) -> None:
        self._access = token
        self._access_expires = datetime.fromisoformat(expires_at)

    async def refresh(self) -> None:
        if not self.node_key:
            raise WorkerApiError(401, "NODE_AUTH_REQUIRED", "node is not registered")
        resp = await self._send("POST", "/v1/workers/token", self.node_key)
        if resp.status_code != 200:
            raise _error(resp)
        data = resp.json()
        self._set_access(data["access_token"], data["expires_at"])

    async def _ensure_access(self) -> str:
        soon = datetime.now(UTC) + timedelta(seconds=30)
        if self._access is None or self._access_expires is None or self._access_expires <= soon:
            await self.refresh()
        return self._access  # type: ignore[return-value]

    async def call(
        self, method: str, path: str, *, expect: tuple[int, ...] = (200,), **kwargs: Any
    ) -> httpx.Response:
        token = await self._ensure_access()
        resp = await self._send(method, path, token, **kwargs)
        if resp.status_code == 401 and _error(resp).code in REFRESH_CODES:
            await self.refresh()
            resp = await self._send(method, path, self._access, **kwargs)
        if resp.status_code not in expect:
            raise _error(resp)
        return resp

    # ---------------------------------------------------------------- node API
    async def me(self) -> dict[str, Any]:
        return (await self.call("GET", "/v1/workers/me")).json()

    async def heartbeat(self, state: str = "ONLINE", capabilities: dict | None = None) -> None:
        body: dict[str, Any] = {"state": state}
        if capabilities is not None:
            body["capabilities"] = capabilities
        await self.call("POST", f"/v1/workers/{self.worker_id}/heartbeat", json=body, expect=(204,))

    async def rotate(self) -> dict[str, Any]:
        data = (await self.call("POST", f"/v1/workers/{self.worker_id}/credentials/rotate")).json()
        self.node_key = data["node_key"]
        self._set_access(data["access_token"], data["access_token_expires_at"])
        return data

    async def poll(self, wait_seconds: float) -> dict[str, Any] | None:
        resp = await self.call(
            "GET",
            f"/v1/workers/{self.worker_id}/leases/next",
            params={"wait_seconds": wait_seconds},
            expect=(200, 204),
            timeout=wait_seconds + 15,
        )
        return resp.json() if resp.status_code == 200 else None

    async def accept(self, lease_id: str) -> None:
        await self.call("POST", f"/v1/leases/{lease_id}/accept", expect=(204,))

    async def progress(self, lease_id: str, message: str, percent: int) -> dict[str, Any]:
        return (
            await self.call(
                "POST",
                f"/v1/leases/{lease_id}/progress",
                json={"message": message, "percent": percent},
            )
        ).json()

    async def renew(self, lease_id: str) -> dict[str, Any]:
        return (await self.call("POST", f"/v1/leases/{lease_id}/renew")).json()

    async def complete(self, lease_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return (await self.call("POST", f"/v1/leases/{lease_id}/complete", json=payload)).json()
