"""Agent registration and management routes."""

import json

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..services.registry import AgentRegistry


class RegisterRequest(BaseModel):
    name: str
    agent_type: str
    display_name: str | None = None


class RegisterResponse(BaseModel):
    agent_id: str
    agent_name: str
    api_key: str
    message: str


def create_agent_routes(registry: AgentRegistry) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.post("/register", response_model=RegisterResponse)
    async def register(req: RegisterRequest):
        existing = await registry.get_by_name(req.name)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Agent name '{req.name}' already registered. "
                       f"If you lost your API key, contact admin to rotate.",
            )
        agent, api_key = await registry.register(
            name=req.name,
            agent_type=req.agent_type,
            display_name=req.display_name,
        )
        return RegisterResponse(
            agent_id=agent.id,
            agent_name=agent.name,
            api_key=api_key,
            message="Keep this API key safe — it won't be shown again!",
        )

    @router.post("/heartbeat")
    async def heartbeat(request: Request):
        agent_id = await require_auth(request)
        body = await request.json()
        status = body.get("status", "online")
        pending = await registry.update_heartbeat(agent_id, status)
        return {"ok": True, "status": status, "pending_count": pending}

    @router.get("")
    async def list_agents(
        request: Request,
        status: str | None = Query(None),
        online: bool = Query(False),
    ):
        await require_auth(request)
        if online:
            status = "online"
        agents = await registry.list_agents(status=status)
        return {
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "agent_type": a.agent_type,
                    "display_name": a.display_name,
                    "status": a.status,
                    "last_seen": a.last_seen.isoformat() if a.last_seen else None,
                }
                for a in agents
            ]
        }

    @router.get("/me")
    async def get_me(request: Request):
        agent_id = await require_auth(request)
        agent = await registry.get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {
            "id": agent.id,
            "name": agent.name,
            "agent_type": agent.agent_type,
            "display_name": agent.display_name,
            "status": agent.status,
            "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
        }

    @router.post("/me/rotate-key")
    async def rotate_key(request: Request):
        agent_id = await require_auth(request)
        new_key, prefix = await registry.rotate_api_key(agent_id)
        return {
            "api_key": new_key,
            "key_prefix": prefix,
            "message": "New API key generated. Update your config.json. Old key will work for 5 more minutes.",
        }

    class RenameRequest(BaseModel):
        name: str

    @router.post("/me/rename")
    async def rename_me(request: Request, body: RenameRequest):
        agent_id = await require_auth(request)
        try:
            agent = await registry.rename_agent(agent_id, body.name)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {
            "ok": True,
            "id": agent.id,
            "name": agent.name,
            "message": f"Renamed to '{agent.name}'",
        }

    @router.delete("/me")
    async def unregister(request: Request):
        agent_id = await require_auth(request)
        ok = await registry.unregister(agent_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"ok": True, "message": "Agent unregistered"}

    return router
