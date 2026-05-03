"""Agent WeChat Hub — A2A messaging hub for AI agents.

FastAPI application assembly and entry point.
"""

import os
import sys
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, generate_master_key
from .database import init_db, create_tables
from .auth import require_auth
from .services.registry import AgentRegistry
from .services.push import SSEManager
from .services.router import MessageRouter
from .services.groups import GroupService
from .routes.agents import create_agent_routes
from .routes.messages import create_message_routes
from .routes.groups import create_group_routes
from .routes.admin import create_admin_routes


# Global service instances (initialized in lifespan)
settings: Settings | None = None
registry: AgentRegistry | None = None
sse_manager: SSEManager | None = None
message_router: MessageRouter | None = None
group_service: GroupService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, registry, sse_manager, message_router, group_service

    # Load config
    settings = Settings()
    if not settings.master_api_key:
        settings.master_api_key = generate_master_key()
        print(f"\n{'='*60}")
        print(f"MASTER_API_KEY: {settings.master_api_key}")
        print(f"Save this key! It grants full admin access.")
        print(f"{'='*60}\n")

    # Store settings on app state for access in routes
    app.state.config = settings

    # Init database
    init_db(settings)
    await create_tables()

    # Init services
    from .database import async_session_factory
    registry = AgentRegistry(async_session_factory)
    sse_manager = SSEManager()
    message_router = MessageRouter(async_session_factory, registry, sse_manager)
    group_service = GroupService(async_session_factory)

    # Store services on app state
    app.state.registry = registry
    app.state.sse_manager = sse_manager
    app.state.message_router = message_router
    app.state.group_service = group_service

    # Register routes (must be done here since services are created in lifespan)
    app.include_router(create_agent_routes(registry))
    app.include_router(create_message_routes(message_router, sse_manager, registry))
    app.include_router(create_group_routes(group_service, message_router))
    app.include_router(create_admin_routes(registry, sse_manager, group_service, message_router))

    print(f"Agent WeChat Hub v{settings.hub_version} running on {settings.host}:{settings.port}")
    print(f"Database: {settings.database_url}")
    print(f"Hub URL: {settings.hub_url}")

    yield

    print("Shutting down Agent WeChat Hub...")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent WeChat Hub",
        description="A2A messaging hub for AI agents — like WeChat for agents",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "agent-wechat-hub"}

    # A2A Agent Card (standard discovery endpoint)
    @app.get("/.well-known/agent.json")
    async def agent_card(request: Request):
        cfg = request.app.state.config
        return JSONResponse(content={
            "name": cfg.hub_name,
            "description": cfg.hub_description,
            "url": cfg.hub_url,
            "version": cfg.hub_version,
            "protocol_version": "1.0",
            "capabilities": {
                "streaming": True,
                "push_notifications": True,
            },
            "default_input_modes": ["text/plain"],
            "default_output_modes": ["text/plain"],
            "skills": [
                {
                    "id": "agent_wechat_messaging",
                    "name": "Agent WeChat Messaging",
                    "description": (
                        "A2A message hub for routing messages between agents. "
                        "Use @AgentName: for 1-on-1 chat, #GroupName: for group chat, "
                        "*: for broadcast to all online agents."
                    ),
                    "tags": ["messaging", "hub", "wechat", "group-chat", "broadcast"],
                    "examples": [
                        "@Bob: Hello from Alice!",
                        "#dev-team: PR ready for review",
                        "*: System maintenance in 5 minutes",
                    ],
                }
            ],
            "security": {
                "scheme": "apiKey",
                "header": "X-API-Key",
            },
        })

    # A2A protocol info
    @app.get("/.well-known/a2a.json")
    async def a2a_protocol_info(request: Request):
        cfg = request.app.state.config
        return JSONResponse(content={
            "protocol_version": "1.0",
            "agent_card_url": f"{cfg.hub_url}/.well-known/agent.json",
            "endpoints": {
                "jsonrpc": f"{cfg.hub_url}/",
            },
        })

    return app


app = create_app()


def main():
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host if settings else "0.0.0.0",
        port=settings.port if settings else 9999,
        reload=False,
    )


if __name__ == "__main__":
    main()
