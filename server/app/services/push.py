"""SSE (Server-Sent Events) connection manager for real-time message push."""

import asyncio
import json
from datetime import datetime, timezone


class SSEManager:
    def __init__(self):
        self._connections: dict[str, asyncio.Queue] = {}

    async def connect(self, agent_id: str) -> asyncio.Queue:
        """Register a new SSE connection. Returns a queue for pushing events."""
        # Close existing connection if any
        await self.disconnect(agent_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._connections[agent_id] = queue
        return queue

    async def disconnect(self, agent_id: str):
        """Remove an SSE connection."""
        queue = self._connections.pop(agent_id, None)
        if queue:
            # Insert sentinel to signal end of stream
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def push(self, agent_id: str, event: dict) -> bool:
        """Push an event to an agent's SSE queue. Returns True if delivered, False if offline."""
        queue = self._connections.get(agent_id)
        if queue is None:
            return False
        try:
            queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False

    def is_online(self, agent_id: str) -> bool:
        return agent_id in self._connections

    def online_count(self) -> int:
        return len(self._connections)

    def online_agents(self) -> list[str]:
        return list(self._connections.keys())

    def format_sse(self, data: dict) -> str:
        """Format a dict as an SSE message string."""
        event_type = data.get("event", "message")
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event_type}\ndata: {payload}\n\n"

    def heartbeat_event(self) -> dict:
        return {"event": "heartbeat", "data": {}}

    def message_event(self, msg: dict) -> dict:
        return {
            "event": "new_message",
            "data": msg,
        }

    def offline_delivery_event(self, count: int) -> dict:
        return {
            "event": "offline_delivery",
            "data": {"pending_count": count},
        }

    def agent_status_event(self, agent_name: str, status: str) -> dict:
        return {
            "event": "agent_status",
            "data": {"agent_name": agent_name, "status": status},
        }
