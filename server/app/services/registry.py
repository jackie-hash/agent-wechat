"""Agent registry — registration, lookup, heartbeat."""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import generate_api_key
from ..models import Agent, APIKey


class AgentRegistry:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def register(
        self, name: str, agent_type: str, display_name: str | None = None
    ) -> tuple[Agent, str]:
        """Register a new agent. Returns (agent, raw_api_key)."""
        api_key = generate_api_key()
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        key_prefix = api_key[:8]

        async with self.session_factory() as session:
            agent = Agent(
                name=name,
                agent_type=agent_type,
                display_name=display_name or name,
                status="offline",
                last_seen=datetime.now(timezone.utc),
            )
            session.add(agent)
            await session.flush()

            api_key_record = APIKey(
                agent_id=agent.id,
                key_hash=key_hash,
                key_prefix=key_prefix,
                label="default",
                is_active=True,
            )
            session.add(api_key_record)
            await session.commit()
            await session.refresh(agent)

        return agent, api_key

    async def get_by_id(self, agent_id: str) -> Agent | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Agent | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Agent).where(Agent.name == name)
            )
            return result.scalar_one_or_none()

    async def list_agents(self, status: str | None = None) -> list[Agent]:
        async with self.session_factory() as session:
            stmt = select(Agent)
            if status:
                stmt = stmt.where(Agent.status == status)
            result = await session.execute(stmt.order_by(Agent.name))
            return list(result.scalars().all())

    async def update_heartbeat(self, agent_id: str, status: str = "online") -> int:
        """Update agent heartbeat. Returns count of pending messages."""
        async with self.session_factory() as session:
            await session.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(status=status, last_seen=datetime.now(timezone.utc))
            )

            # Count pending messages
            from ..models import Message
            result = await session.execute(
                select(Message).where(
                    Message.target_id == agent_id,
                    Message.delivery_status == "pending",
                )
            )
            pending = len(list(result.scalars().all()))
            await session.commit()

        return pending

    async def rotate_api_key(self, agent_id: str) -> tuple[str, str]:
        """Generate a new API key, revoke old ones. Returns (new_raw_key, key_prefix)."""
        new_key = generate_api_key()
        key_hash = hashlib.sha256(new_key.encode()).hexdigest()
        key_prefix = new_key[:8]

        async with self.session_factory() as session:
            # Revoke old active keys
            await session.execute(
                update(APIKey)
                .where(APIKey.agent_id == agent_id, APIKey.is_active == True)
                .values(is_active=False, revoked_at=datetime.now(timezone.utc))
            )
            # Create new key
            new_api_key = APIKey(
                agent_id=agent_id,
                key_hash=key_hash,
                key_prefix=key_prefix,
                label="rotated",
                is_active=True,
            )
            session.add(new_api_key)
            await session.commit()

        return new_key, key_prefix

    async def unregister(self, agent_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if agent is None:
                return False
            await session.delete(agent)
            await session.commit()
        return True

    async def mark_offline_stale(self, timeout_seconds: int):
        """Mark agents offline if they haven't sent heartbeat within timeout."""
        cutoff = datetime.now(timezone.utc).timestamp() - timeout_seconds
        async with self.session_factory() as session:
            from datetime import datetime as dt
            await session.execute(
                update(Agent)
                .where(
                    Agent.status == "online",
                    Agent.last_seen < dt.fromtimestamp(cutoff, tz=timezone.utc),
                )
                .values(status="offline")
            )
            await session.commit()
