"""Message sending, inbox, history, and SSE streaming routes."""

import asyncio
import json

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth import require_auth
from ..services.router import MessageRouter
from ..services.push import SSEManager
from ..services.registry import AgentRegistry


class SendRequest(BaseModel):
    target: str | None = None  # Agent name (for direct), group name (for group), or None for broadcast
    content: str
    target_type: str = "direct"  # direct / group / broadcast


def create_message_routes(
    router_service: MessageRouter,
    sse_manager: SSEManager,
    registry: AgentRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/api/messages", tags=["messages"])

    @router.post("/send")
    async def send_message(request: Request, body: SendRequest):
        agent_id = await require_auth(request)
        content = body.content.strip()

        # If content has a prefix (@name:, #group:, *:), parse it
        target_type, parsed_target, clean_content = router_service.parse_content(content)
        final_content = clean_content if clean_content else content

        # Use explicit target_type/target from body if provided, otherwise from parsing
        final_target_type = body.target_type
        target_identifier = body.target

        if target_type != "unknown" and not target_identifier:
            final_target_type = target_type
            target_identifier = parsed_target

        if not target_identifier:
            raise HTTPException(
                status_code=400,
                detail="Could not determine message target. Use @AgentName:, #GroupName:, *:, or specify target/target_type in body.",
            )

        # Resolve target name to ID
        target_id = await router_service.resolve_target(final_target_type, target_identifier)
        if target_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"Target '{target_identifier}' not found (type: {final_target_type})",
            )

        result = await router_service.send(
            sender_id=agent_id,
            content=final_content,
            target_type=final_target_type,
            target_id=target_id,
        )

        return {
            "ok": True,
            "message_id": result["message_id"],
            "delivered_count": result["delivered_count"],
            "offline_count": result["offline_count"],
        }

    @router.get("/inbox")
    async def get_inbox(request: Request):
        agent_id = await require_auth(request)
        messages = await router_service.get_inbox(agent_id)
        return {"messages": messages, "count": len(messages)}

    @router.post("/inbox/ack")
    async def ack_inbox(request: Request):
        agent_id = await require_auth(request)
        body = await request.json()
        message_ids = body.get("message_ids", [])
        await router_service.ack_messages(agent_id, message_ids)
        return {"ok": True}

    @router.get("/history")
    async def get_history(
        request: Request,
        with_agent: str | None = Query(None, alias="with"),
        limit: int = Query(50, ge=1, le=200),
        before: str | None = Query(None),
    ):
        agent_id = await require_auth(request)

        # Resolve agent name to ID if needed
        resolved_with: str | None = None
        if with_agent:
            # Check if it's already a UUID-like ID
            agent = await registry.get_by_name(with_agent)
            if agent:
                resolved_with = agent.id
            else:
                resolved_with = with_agent  # Assume it's already an ID

        messages = await router_service.get_history(
            agent_id=agent_id,
            with_agent=resolved_with,
            limit=limit,
            before=before,
        )
        return {"messages": messages, "count": len(messages)}

    @router.get("/stream")
    async def sse_stream(request: Request):
        agent_id = await require_auth(request)

        # Update agent status to online
        await registry.update_heartbeat(agent_id, "online")

        queue = await sse_manager.connect(agent_id)

        async def event_generator():
            try:
                # First, check for pending offline messages
                pending = await router_service.get_inbox(agent_id)
                if pending:
                    yield sse_manager.format_sse(
                        sse_manager.offline_delivery_event(len(pending))
                    )

                yield sse_manager.format_sse({"event": "connected", "data": {"agent_id": agent_id}})

                # Send heartbeat every 30 seconds
                heartbeat_task = asyncio.create_task(_heartbeat_loop(sse_manager, queue))

                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=1.0)
                        if event is None:  # Sentinel for disconnect
                            break
                        yield sse_manager.format_sse(event)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            finally:
                await sse_manager.disconnect(agent_id)
                await registry.update_heartbeat(agent_id, "offline")

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def _heartbeat_loop(sse_manager: SSEManager, queue: asyncio.Queue):
    """Send heartbeat events every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        try:
            queue.put_nowait(sse_manager.heartbeat_event())
        except asyncio.QueueFull:
            pass
