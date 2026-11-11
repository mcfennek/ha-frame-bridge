"""Home Assistant Core API via the Supervisor proxy (homeassistant_api: true).

Used for what the samsungtv integration already does: finding the TV's entities, reading its
state and switching it on or off. Art functions are not available there (see frame.py).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CORE_URL = os.environ.get("FRAME_BRIDGE_CORE_URL", "http://supervisor/core/api")

_DISCOVER_TEMPLATE = """
{%- set ns = namespace(out=[]) -%}
{%- for e in integration_entities('samsungtv') -%}
  {%- set ns.out = ns.out + [{
      'entity_id': e,
      'name': state_attr(e, 'friendly_name') or e,
      'state': states(e),
      'device_id': device_id(e),
      'device_name': device_attr(device_id(e), 'name_by_user') or device_attr(device_id(e), 'name'),
      'model': device_attr(device_id(e), 'model')
  }] -%}
{%- endfor -%}
{{ ns.out | to_json }}
"""


class HomeAssistant:
    def __init__(self, token: str | None = None, base_url: str = CORE_URL):
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        return bool(self.token)

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def discover(self) -> list[dict[str, Any]]:
        """All entities of the samsungtv integration, grouped by device."""
        if not self.available:
            return []
        s = await self._sess()
        async with s.post(f"{self.base_url}/template", json={"template": _DISCOVER_TEMPLATE}) as r:
            r.raise_for_status()
            text = await r.text()
        entities = json.loads(text or "[]")
        devices: dict[str, dict[str, Any]] = {}
        for e in entities:
            key = e.get("device_id") or e["entity_id"]
            dev = devices.setdefault(
                key,
                {"device_id": e.get("device_id"), "name": e.get("device_name"), "model": e.get("model"), "entities": []},
            )
            dev["entities"].append({"entity_id": e["entity_id"], "name": e["name"], "state": e["state"]})
        return list(devices.values())

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        if not self.available or not entity_id:
            return None
        s = await self._sess()
        async with s.get(f"{self.base_url}/states/{entity_id}") as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            return await r.json()

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        s = await self._sess()
        async with s.post(f"{self.base_url}/services/{domain}/{service}", json=data) as r:
            r.raise_for_status()
