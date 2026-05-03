"""AgentWire Hub API Client.

Provides a clean Python interface to the A2A messaging hub.
"""

import json
import os
from typing import AsyncGenerator

import httpx

DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".agent-wechat", "config.json")


class HubClient:
    def __init__(self, hub_url: str, api_key: str = "", agent_id: str = ""):
        self.base_url = hub_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    def _headers(self) -> dict:
        h = {}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    # ── Registration ──────────────────────────────────────────

    async def register(self, name: str, agent_type: str, display_name: str | None = None) -> dict:
        body = {"name": name, "agent_type": agent_type}
        if display_name:
            body["display_name"] = display_name
        r = await self.client.post(f"{self.base_url}/api/agents/register", json=body)
        if r.status_code == 409:
            raise ValueError(f"Agent name '{name}' already registered")
        r.raise_for_status()
        return r.json()

    # ── Heartbeat ─────────────────────────────────────────────

    async def heartbeat(self, status: str = "online") -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/agents/heartbeat",
            json={"status": status},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    # ── Agents ────────────────────────────────────────────────

    async def list_agents(self, online_only: bool = False) -> list[dict]:
        params = {}
        if online_only:
            params["online"] = "true"
        r = await self.client.get(
            f"{self.base_url}/api/agents",
            params=params,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json().get("agents", [])

    async def get_me(self) -> dict:
        r = await self.client.get(
            f"{self.base_url}/api/agents/me",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def rotate_key(self) -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/agents/me/rotate-key",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    # ── Messaging ─────────────────────────────────────────────

    async def send_message(self, content: str, target: str = "", target_type: str = "direct") -> dict:
        body = {
            "content": content,
            "target": target,
            "target_type": target_type,
        }
        r = await self.client.post(
            f"{self.base_url}/api/messages/send",
            json=body,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def get_inbox(self) -> list[dict]:
        r = await self.client.get(
            f"{self.base_url}/api/messages/inbox",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json().get("messages", [])

    async def mark_read(self, message_ids: list[str]) -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/messages/read",
            json={"message_ids": message_ids},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def get_sent_status(self, message_id: str) -> dict:
        r = await self.client.get(
            f"{self.base_url}/api/messages/sent-status/{message_id}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def ack_inbox(self, message_ids: list[str]) -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/messages/inbox/ack",
            json={"message_ids": message_ids},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def get_history(self, with_agent: str | None = None, limit: int = 50) -> list[dict]:
        params = {"limit": limit}
        if with_agent:
            params["with"] = with_agent
        r = await self.client.get(
            f"{self.base_url}/api/messages/history",
            params=params,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json().get("messages", [])

    # ── SSE Stream ────────────────────────────────────────────

    async def connect_sse(self) -> AsyncGenerator[dict, None]:
        """Connect to the SSE stream for real-time messages."""
        url = f"{self.base_url}/api/messages/stream?api_key={self.api_key}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                buffer = ""
                async for chunk in response.aiter_bytes():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n\n" in buffer:
                        msg_str, buffer = buffer.split("\n\n", 1)
                        event = self._parse_sse(msg_str)
                        if event:
                            yield event

    def _parse_sse(self, raw: str) -> dict | None:
        """Parse an SSE message string into a dict."""
        event_type = "message"
        data = ""
        for line in raw.split("\n"):
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if not data:
            return None
        try:
            return {"event": event_type, **json.loads(data)}
        except json.JSONDecodeError:
            return {"event": event_type, "data": data}

    # ── Groups ────────────────────────────────────────────────

    async def create_group(self, name: str, description: str = "") -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/groups",
            json={"name": name, "description": description},
            headers=self._headers(),
        )
        if r.status_code == 409:
            raise ValueError(f"Group '{name}' already exists")
        r.raise_for_status()
        return r.json()

    async def list_groups(self) -> list[dict]:
        r = await self.client.get(
            f"{self.base_url}/api/groups",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json().get("groups", [])

    async def get_group(self, group_id: str) -> dict:
        r = await self.client.get(
            f"{self.base_url}/api/groups/{group_id}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def join_group(self, group_id: str) -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/groups/{group_id}/join",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def leave_group(self, group_id: str) -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/groups/{group_id}/leave",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def rename_agent(self, new_name: str) -> dict:
        r = await self.client.post(
            f"{self.base_url}/api/agents/me/rename",
            json={"name": new_name},
            headers=self._headers(),
        )
        if r.status_code == 409:
            raise ValueError(f"Agent name '{new_name}' is already taken")
        r.raise_for_status()
        return r.json()

    # ── Cleanup ───────────────────────────────────────────────

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_config(config: dict, path: str = DEFAULT_CONFIG_PATH):
    with open(path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
