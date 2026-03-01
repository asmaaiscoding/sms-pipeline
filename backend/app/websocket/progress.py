"""
app/websocket/progress.py
─────────────────────────
WebSocket endpoint that relays real-time job progress from Redis pub/sub.

Clients connect to:  ws://host/ws/projects/{project_id}/progress

They receive JSON messages like:
    {"job_id": "...", "project_id": "...", "status": "running", "progress": 42.5, "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


@router.websocket("/ws/projects/{project_id}/progress")
async def job_progress_ws(websocket: WebSocket, project_id: UUID):
    """
    Stream real-time job progress for a project.
    No auth on WS for simplicity — project_id acts as the channel key.
    """
    await websocket.accept()

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()

    try:
        channel = f"job:{project_id}"
        await pubsub.subscribe(channel)

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message and message["type"] == "message":
                await websocket.send_text(message["data"])

            # Check if client is still connected
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
                # Client can send "ping" to keep alive
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                pass

    except WebSocketDisconnect:
        logger.debug("WS client disconnected for project %s", project_id)
    except Exception as exc:
        logger.warning("WS error: %s", exc)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await r.close()
