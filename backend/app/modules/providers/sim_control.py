"""Server-side client for the simulator's admin endpoints (owner decision, Phase 8).

The admin app never sees the simulator admin key: staff call the VieWallet API, which checks
``simulator:manage:any``, writes an audit row and forwards the call with the key kept on the
server. The client is only built outside production-like environments.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.modules.providers.port import ProviderTimeout, ProviderUnavailable

MODES = ("NORMAL", "DEGRADED", "OUTAGE")
ACTIONS = ("SUCCEED", "FAIL", "EXPIRE", "RETURN", "RESEND_CALLBACK")


class SimulatorControl:
    def __init__(self, client: httpx.AsyncClient, *, admin_key: str) -> None:
        self._client = client
        self._admin_key = admin_key

    @classmethod
    def build(
        cls, base_url: str, *, admin_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> SimulatorControl:
        client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(5, connect=2), transport=transport
        )
        return cls(client, admin_key=admin_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.request(
                method, path, json=body, headers={"X-Sim-Admin-Key": self._admin_key}
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnavailable(type(exc).__name__) from exc
        except httpx.TransportError as exc:
            raise ProviderTimeout(type(exc).__name__) from exc
        if response.status_code >= 500:  # noqa: PLR2004
            raise ProviderUnavailable(f"HTTP {response.status_code}")
        response.raise_for_status()
        return response.json()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200  # noqa: PLR2004

    async def get_mode(self) -> str:
        return str((await self._call("GET", "/v1/admin/settings"))["mode"])

    async def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(mode)
        return str((await self._call("PUT", "/v1/admin/settings", {"mode": mode}))["mode"])

    async def operations(self) -> list[dict[str, Any]]:
        return list((await self._call("GET", "/v1/admin/operations"))["items"])

    async def act(self, operation_key: str, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError(action)
        result: dict[str, Any] = await self._call(
            "POST", f"/v1/admin/operations/{operation_key}/actions", {"action": action}
        )
        return result
