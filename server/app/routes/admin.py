"""Admin routes (master API key required)."""

from fastapi import APIRouter, Request, HTTPException

from ..auth import require_master
from ..services.registry import AgentRegistry
from ..services.push import SSEManager
from ..services.groups import GroupService
from ..services.router import MessageRouter


def create_admin_routes(
    registry: AgentRegistry,
    sse_manager: SSEManager,
    group_service: GroupService,
    message_router: MessageRouter,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    @router.get("/stats")
    async def get_stats(request: Request):
        await require_master(request)
        agents = await registry.list_agents()
        groups = await group_service.list_groups()

        online_agents = [a for a in agents if a.status == "online"]

        return {
            "agents": {
                "total": len(agents),
                "online": len(online_agents),
                "offline": len(agents) - len(online_agents),
            },
            "groups": len(groups),
            "sse_connections": sse_manager.online_count(),
        }

    @router.get("/agents")
    async def list_all_agents(request: Request):
        await require_master(request)
        agents = await registry.list_agents()
        return {
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "agent_type": a.agent_type,
                    "status": a.status,
                    "last_seen": a.last_seen.isoformat() if a.last_seen else None,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in agents
            ]
        }

    @router.delete("/agents/{agent_id}")
    async def force_remove_agent(request: Request, agent_id: str):
        await require_master(request)
        ok = await registry.unregister(agent_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"ok": True, "message": "Agent forcibly removed"}

    @router.post("/agents/{agent_id}/rotate-key")
    async def admin_rotate_key(request: Request, agent_id: str):
        await require_master(request)
        new_key, prefix = await registry.rotate_api_key(agent_id)
        return {"api_key": new_key, "key_prefix": prefix}

    return router
