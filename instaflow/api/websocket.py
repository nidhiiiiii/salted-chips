"""
WebSocket Live Event Feed — Module 7

Broadcasts real-time task events (reel processed, DM received,
link extracted, health change) to connected dashboard clients.

Uses a simple in-process pub/sub via asyncio.Queue.
For multi-worker deployments, upgrade to Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from instaflow.config.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# ── Connection Manager ─────────────────────────────────────────────────

class ConnectionManager:
    """Manages all active WebSocket connections."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("ws.connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info("ws.disconnected", total=len(self._connections))

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Broadcast a JSON event to all connected clients."""
        dead: list[WebSocket] = []
        payload = json.dumps(event, default=str)

        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def emit(event_type: str, data: dict[str, Any]) -> None:
    """
    Emit an event to all WebSocket clients.
    Call this from workers/tasks after key state changes.
    """
    await manager.broadcast({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    })


# ── WebSocket Endpoint ─────────────────────────────────────────────────

@router.websocket("/ws/feed")
async def websocket_feed(ws: WebSocket) -> None:
    """
    Live event feed. Connect with any WebSocket client.

    Event types emitted:
      • reel.completed
      • reel.failed
      • dm.cta_detected
      • link.extracted
      • account.health_changed
      • account.quarantined
      • challenge.detected
    """
    await manager.connect(ws)

    # Send welcome message
    await ws.send_text(json.dumps({
        "type": "connected",
        "data": {"message": "InstaFlow live feed connected."},
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }))

    try:
        while True:
            # Keep connection alive — echo pings back
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                if msg == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive heartbeat
                try:
                    await ws.send_text(json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }))
                except Exception:
                    break
    except WebSocketDisconnect:
        manager.disconnect(ws)
