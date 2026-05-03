"""Message router — parses message prefixes, resolves targets, routes and stores."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Message, Group, GroupMember
from .registry import AgentRegistry
from .push import SSEManager


class MessageRouter:
    def __init__(self, session_factory, registry: AgentRegistry, sse: SSEManager):
        self.session_factory = session_factory
        self.registry = registry
        self.sse = sse

    def parse_content(self, raw_content: str) -> tuple[str, str, str]:
        """
        Parse message content to determine target type and clean content.
        Returns (target_type, target_name_or_id, clean_content).

        Prefix formats:
          @AgentName: message  -> direct message
          #GroupName: message  -> group message
          *: message           -> broadcast
          plain message        -> None (must be sent with explicit target_type)
        """
        content = raw_content.strip()

        # Broadcast: starts with *:
        if content.startswith("*:") or content.startswith("*："):
            clean = content[2:].strip() if content[1] == ":" else content[2:].strip()
            return "broadcast", "*", clean

        # Group: starts with #GroupName:
        if content.startswith("#"):
            colon_idx = -1
            for sep in (":", "："):
                idx = content.find(sep)
                if idx != -1 and (colon_idx == -1 or idx < colon_idx):
                    colon_idx = idx

            if colon_idx != -1:
                group_name = content[1:colon_idx].strip()
                clean = content[colon_idx + 1:].strip()
                return "group", group_name, clean
            else:
                # No colon found, treat entire thing as group name with empty message
                group_name = content[1:].strip()
                return "group", group_name, ""

        # Direct: starts with @AgentName:
        if content.startswith("@"):
            colon_idx = -1
            for sep in (":", "："):
                idx = content.find(sep)
                if idx != -1 and (colon_idx == -1 or idx < colon_idx):
                    colon_idx = idx

            if colon_idx != -1:
                agent_name = content[1:colon_idx].strip()
                clean = content[colon_idx + 1:].strip()
                return "direct", agent_name, clean
            else:
                agent_name = content[1:].strip()
                return "direct", agent_name, ""

        # No prefix — treat as raw message, target_type must be explicit
        return "unknown", "", raw_content

    async def resolve_target(self, target_type: str, target_identifier: str) -> str | None:
        """Resolve a target name to an agent_id or group_id. Returns None if not found."""
        if target_type == "broadcast":
            return "*"
        elif target_type == "direct":
            agent = await self.registry.get_by_name(target_identifier)
            return agent.id if agent else None
        elif target_type == "group":
            async with self.session_factory() as session:
                result = await session.execute(
                    select(Group).where(Group.name == target_identifier)
                )
                group = result.scalar_one_or_none()
            return group.id if group else None
        return None

    async def send(
        self,
        sender_id: str,
        content: str,
        target_type: str,
        target_id: str,
    ) -> dict:
        """
        Route a message to its target(s). Returns status dict.
        target_type: 'direct', 'group', 'broadcast'
        target_id: agent_id, group_id, or '*'
        """
        msg_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        delivered_to: list[str] = []
        offline_for: list[str] = []

        async with self.session_factory() as session:
            # Create the message record
            message = Message(
                id=msg_id,
                sender_id=sender_id,
                target_type=target_type,
                target_id=target_id,
                content=content,
                delivery_status="pending",
                created_at=now,
            )
            session.add(message)
            await session.commit()

        # Determine delivery targets
        if target_type == "broadcast":
            all_agents = await self.registry.list_agents()
            targets = [
                a.id
                for a in all_agents
                if a.id != sender_id
            ]
        elif target_type == "group":
            targets = await self._get_group_member_ids(target_id, exclude=sender_id)
        else:  # direct
            targets = [target_id]

        # Deliver to each target
        for tid in targets:
            if self.sse.is_online(tid):
                delivered = await self.sse.push(
                    tid,
                    self.sse.message_event({
                        "id": msg_id,
                        "from_agent_id": sender_id,
                        "from_agent_name": await self._get_agent_name(sender_id),
                        "target_type": target_type,
                        "target_id": target_id,
                        "content": content,
                        "timestamp": now.isoformat(),
                    }),
                )
                if delivered:
                    delivered_to.append(tid)
                    continue
            offline_for.append(tid)

        # Update delivery status
        async with self.session_factory() as session:
            final_status = "delivered" if not offline_for else "pending"
            status_values = {"delivery_status": final_status}
            if delivered_to:
                status_values["delivered_at"] = now
            await session.execute(
                update(Message).where(Message.id == msg_id).values(**status_values)
            )
            await session.commit()

        return {
            "message_id": msg_id,
            "delivered_to": delivered_to,
            "offline_for": offline_for,
            "delivered_count": len(delivered_to),
            "offline_count": len(offline_for),
        }

    async def get_inbox(self, agent_id: str) -> list[dict]:
        """Fetch undelivered messages for an agent."""
        async with self.session_factory() as session:
            # Direct messages addressed to this agent
            result = await session.execute(
                select(Message)
                .where(
                    Message.target_type == "direct",
                    Message.target_id == agent_id,
                    Message.delivery_status == "pending",
                )
                .order_by(Message.created_at)
            )
            direct_msgs = list(result.scalars().all())

            # Group messages for groups this agent belongs to
            group_ids = await self._get_agent_group_ids(agent_id)
            group_msgs = []
            if group_ids:
                result = await session.execute(
                    select(Message)
                    .where(
                        Message.target_type == "group",
                        Message.target_id.in_(group_ids),
                        Message.delivery_status == "pending",
                        Message.sender_id != agent_id,
                    )
                    .order_by(Message.created_at)
                )
                group_msgs = list(result.scalars().all())

            # Broadcast messages (directly addressed or * )
            result = await session.execute(
                select(Message)
                .where(
                    Message.target_type == "broadcast",
                    Message.delivery_status == "pending",
                    Message.sender_id != agent_id,
                )
                .order_by(Message.created_at)
            )
            broadcast_msgs = list(result.scalars().all())

            all_msgs = sorted(
                direct_msgs + group_msgs + broadcast_msgs,
                key=lambda m: m.created_at or datetime(2000, 1, 1, tzinfo=timezone.utc),
            )

            return [
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "sender_name": await self._get_agent_name(m.sender_id),
                    "target_type": m.target_type,
                    "target_id": m.target_id,
                    "content": m.content,
                    "content_type": m.content_type,
                    "timestamp": (m.created_at.isoformat() if m.created_at else ""),
                }
                for m in all_msgs
            ]

    async def ack_messages(self, agent_id: str, message_ids: list[str]):
        """Mark messages as delivered (acknowledged)."""
        if not message_ids:
            return
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            await session.execute(
                update(Message)
                .where(Message.id.in_(message_ids))
                .values(delivery_status="delivered", delivered_at=now)
            )
            await session.commit()

    async def get_history(
        self, agent_id: str, with_agent: str | None = None, limit: int = 50,
        before: str | None = None,
    ) -> list[dict]:
        """Get message history for an agent."""
        async with self.session_factory() as session:
            conditions = []

            if with_agent:
                # Conversations between agent_id and with_agent (both directions)
                from sqlalchemy import or_
                conditions.append(
                    or_(
                        (Message.sender_id == agent_id)
                        & (Message.target_type == "direct")
                        & (Message.target_id == with_agent),
                        (Message.sender_id == with_agent)
                        & (Message.target_type == "direct")
                        & (Message.target_id == agent_id),
                    )
                )
            else:
                # All messages involving this agent
                from sqlalchemy import or_
                group_ids = await self._get_agent_group_ids(agent_id)
                group_conditions = []
                if group_ids:
                    group_conditions.append(
                        (Message.target_type == "group") & Message.target_id.in_(group_ids)
                    )
                conditions.append(
                    or_(
                        (Message.sender_id == agent_id),
                        (Message.target_type == "direct") & (Message.target_id == agent_id),
                        (Message.target_type == "broadcast"),
                        *group_conditions,
                    )
                )

            stmt = select(Message)
            for cond in conditions:
                stmt = stmt.where(cond)

            if before:
                from datetime import datetime as dt
                before_dt = dt.fromisoformat(before)
                stmt = stmt.where(Message.created_at < before_dt)

            stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            messages = list(result.scalars().all())

            return [
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "sender_name": await self._get_agent_name(m.sender_id),
                    "target_type": m.target_type,
                    "target_id": m.target_id,
                    "target_name": await self._resolve_target_name(m.target_type, m.target_id),
                    "content": m.content,
                    "delivery_status": m.delivery_status,
                    "timestamp": (m.created_at.isoformat() if m.created_at else ""),
                }
                for m in reversed(messages)
            ]

    async def _get_agent_name(self, agent_id: str) -> str:
        agent = await self.registry.get_by_id(agent_id)
        return agent.name if agent else agent_id

    async def _resolve_target_name(self, target_type: str, target_id: str) -> str:
        if target_type == "broadcast":
            return "所有人"
        elif target_type == "group":
            async with self.session_factory() as session:
                result = await session.execute(
                    select(Group).where(Group.id == target_id)
                )
                group = result.scalar_one_or_none()
            return group.name if group else target_id
        else:
            return await self._get_agent_name(target_id)

    async def _get_group_member_ids(self, group_id: str, exclude: str | None = None) -> list[str]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(GroupMember).where(GroupMember.group_id == group_id)
            )
            members = list(result.scalars().all())
        return [m.agent_id for m in members if m.agent_id != exclude]

    async def _get_agent_group_ids(self, agent_id: str) -> list[str]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(GroupMember).where(GroupMember.agent_id == agent_id)
            )
            members = list(result.scalars().all())
        return [m.group_id for m in members]
