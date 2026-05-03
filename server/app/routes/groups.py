"""Group management routes."""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..services.groups import GroupService
from ..services.router import MessageRouter


class CreateGroupRequest(BaseModel):
    name: str
    description: str | None = None


def create_group_routes(
    group_service: GroupService,
    message_router: MessageRouter,
) -> APIRouter:
    router = APIRouter(prefix="/api/groups", tags=["groups"])

    @router.post("")
    async def create_group(request: Request, body: CreateGroupRequest):
        agent_id = await require_auth(request)

        existing = await group_service.get_group_by_name(body.name)
        if existing:
            raise HTTPException(status_code=409, detail=f"Group '{body.name}' already exists")

        group = await group_service.create_group(
            name=body.name,
            description=body.description,
            created_by=agent_id,
        )

        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "created_at": group.created_at.isoformat() if group.created_at else "",
            "message": f"Group '{group.name}' created. You are the admin.",
        }

    @router.get("")
    async def list_groups(request: Request):
        agent_id = await require_auth(request)
        groups = await group_service.list_groups(agent_id=agent_id)
        return {
            "groups": [
                {
                    "id": g.id,
                    "name": g.name,
                    "description": g.description,
                    "created_at": g.created_at.isoformat() if g.created_at else "",
                }
                for g in groups
            ]
        }

    @router.get("/{group_id}")
    async def get_group(request: Request, group_id: str):
        agent_id = await require_auth(request)

        group = await group_service.get_group(group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        members = await group_service.get_members(group_id)
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "created_by": group.created_by,
            "created_at": group.created_at.isoformat() if group.created_at else "",
            "members": members,
            "member_count": len(members),
        }

    @router.post("/{group_id}/join")
    async def join_group(request: Request, group_id: str):
        agent_id = await require_auth(request)

        group = await group_service.get_group(group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        joined = await group_service.join_group(group_id, agent_id)
        if not joined:
            return {"ok": True, "message": "Already a member"}

        return {"ok": True, "message": f"Joined group '{group.name}'"}

    @router.post("/{group_id}/leave")
    async def leave_group(request: Request, group_id: str):
        agent_id = await require_auth(request)

        is_member = await group_service.is_member(group_id, agent_id)
        if not is_member:
            raise HTTPException(status_code=400, detail="Not a member of this group")

        await group_service.leave_group(group_id, agent_id)
        return {"ok": True, "message": "Left the group"}

    @router.get("/{group_id}/messages")
    async def get_group_messages(
        request: Request,
        group_id: str,
        limit: int = 50,
    ):
        agent_id = await require_auth(request)

        is_member = await group_service.is_member(group_id, agent_id)
        if not is_member:
            raise HTTPException(status_code=403, detail="Not a member of this group")

        # Get group message history via the router
        messages = await message_router.get_history(
            agent_id=agent_id,
            with_agent=None,
            limit=limit,
        )
        # Filter to only group messages for this group
        group_messages = [m for m in messages if m["target_id"] == group_id]
        return {"messages": group_messages, "count": len(group_messages)}

    return router
