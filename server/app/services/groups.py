"""Group management service."""

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Group, GroupMember


class GroupService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def create_group(self, name: str, description: str | None, created_by: str) -> Group:
        async with self.session_factory() as session:
            group = Group(
                name=name,
                description=description,
                created_by=created_by,
            )
            session.add(group)
            await session.flush()

            # Creator becomes admin
            member = GroupMember(
                group_id=group.id,
                agent_id=created_by,
                role="admin",
            )
            session.add(member)
            await session.commit()
            await session.refresh(group)

        return group

    async def get_group(self, group_id: str) -> Group | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Group).where(Group.id == group_id)
            )
            return result.scalar_one_or_none()

    async def get_group_by_name(self, name: str) -> Group | None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Group).where(Group.name == name)
            )
            return result.scalar_one_or_none()

    async def list_groups(self, agent_id: str | None = None) -> list[Group]:
        async with self.session_factory() as session:
            if agent_id:
                result = await session.execute(
                    select(GroupMember).where(GroupMember.agent_id == agent_id)
                )
                member_records = list(result.scalars().all())
                group_ids = [m.group_id for m in member_records]
                if not group_ids:
                    return []
                result = await session.execute(
                    select(Group).where(Group.id.in_(group_ids)).order_by(Group.name)
                )
            else:
                result = await session.execute(select(Group).order_by(Group.name))
            return list(result.scalars().all())

    async def join_group(self, group_id: str, agent_id: str) -> bool:
        async with self.session_factory() as session:
            # Check if already a member
            result = await session.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.agent_id == agent_id,
                )
            )
            if result.scalar_one_or_none():
                return False  # Already a member

            member = GroupMember(group_id=group_id, agent_id=agent_id, role="member")
            session.add(member)
            await session.commit()
        return True

    async def leave_group(self, group_id: str, agent_id: str):
        async with self.session_factory() as session:
            await session.execute(
                delete(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.agent_id == agent_id,
                )
            )
            await session.commit()

    async def get_members(self, group_id: str) -> list[dict]:
        from .registry import AgentRegistry
        async with self.session_factory() as session:
            result = await session.execute(
                select(GroupMember).where(GroupMember.group_id == group_id)
            )
            members = list(result.scalars().all())

        registry = AgentRegistry(self.session_factory)
        member_list = []
        for m in members:
            agent = await registry.get_by_id(m.agent_id)
            member_list.append({
                "agent_id": m.agent_id,
                "agent_name": agent.name if agent else m.agent_id,
                "agent_type": agent.agent_type if agent else "unknown",
                "role": m.role,
                "online": agent.status == "online" if agent else False,
                "joined_at": m.joined_at.isoformat() if m.joined_at else "",
            })
        return member_list

    async def is_member(self, group_id: str, agent_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.agent_id == agent_id,
                )
            )
            return result.scalar_one_or_none() is not None
