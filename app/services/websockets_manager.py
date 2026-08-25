import asyncio
import json
import uuid
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.services.redis_service import redis_service

logger = logging.getLogger(__name__)


def _channel_name(room_id: uuid.UUID) -> str:
    return f"room:{room_id}"


class ConnectionManager:
    """Registry of WebSocket connections local to this process instance.

    Cross-instance fan-out is done via Redis Pub/Sub: every event is
    published to a per-room channel, and each instance that currently
    has local subscribers for that room runs a background listener task
    which re-broadcasts incoming messages to its own local sockets only.
    """

    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, dict[WebSocket, uuid.UUID | None]] = {}
        self._conn_ids: dict[WebSocket, str] = {}
        self._listener_tasks: dict[uuid.UUID, asyncio.Task] = {}

    async def init(self) -> None:
        logger.info("ConnectionManager ready")

    async def close(self) -> None:
        """Cancel every active room subscription (app shutdown)."""
        for room_id in list(self._listener_tasks):
            self._stop_subscription(room_id)

    # ── Local connection registry ───────────────────────────────────────────

    async def connect(
        self,
        room_id: uuid.UUID,
        websocket: WebSocket,
        user_id: uuid.UUID | None = None,
    ) -> None:
        await websocket.accept()
        self._conn_ids[websocket] = uuid.uuid4().hex
        self._rooms.setdefault(room_id, {})[websocket] = user_id
        await self._ensure_subscribed(room_id)
        logger.info(
            "WS connected   | room=%s user=%s | local_total=%d",
            room_id, user_id, len(self._rooms[room_id]),
        )

    def disconnect(
        self,
        room_id: uuid.UUID,
        websocket: WebSocket,
    ) -> uuid.UUID | None:
        room = self._rooms.get(room_id, {})
        left_user_id = room.pop(websocket, None)
        self._conn_ids.pop(websocket, None)
        if not room:
            self._rooms.pop(room_id, None)
            self._stop_subscription(room_id)
        logger.info(
            "WS disconnected | room=%s user=%s | local_remaining=%d",
            room_id, left_user_id, len(room),
        )
        return left_user_id

    # ── Redis Pub/Sub fan-out ───────────────────────────────────────────────

    async def publish_event(
        self,
        room_id: uuid.UUID,
        event_type: str,
        data: dict,
        exclude: WebSocket | None = None,
    ) -> None:
        """Publish an event envelope to the room's Redis channel.

        Every instance subscribed to this room (including this one)
        receives it and re-broadcasts to its own local sockets.
        """
        envelope: dict = {"type": event_type, "data": data}
        if exclude is not None:
            conn_id = self._conn_ids.get(exclude)
            if conn_id is not None:
                envelope["_exclude"] = conn_id
        await redis_service.client.publish(_channel_name(room_id), json.dumps(envelope))

    async def _ensure_subscribed(self, room_id: uuid.UUID) -> None:
        if room_id in self._listener_tasks:
            return
        pubsub = redis_service.client.pubsub()
        await pubsub.subscribe(_channel_name(room_id))
        self._listener_tasks[room_id] = asyncio.create_task(self._listen(room_id, pubsub))
        logger.info("Subscribed to Redis channel | room=%s", room_id)

    def _stop_subscription(self, room_id: uuid.UUID) -> None:
        task = self._listener_tasks.pop(room_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _listen(self, room_id: uuid.UUID, pubsub) -> None:
        channel = _channel_name(room_id)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    envelope = json.loads(message["data"])
                except (TypeError, ValueError):
                    logger.warning("Dropped malformed Pub/Sub payload | room=%s", room_id)
                    continue
                exclude_conn_id = envelope.pop("_exclude", None)
                await self._broadcast_local(room_id, envelope, exclude_conn_id)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            logger.info("Unsubscribed from Redis channel | room=%s", room_id)

    async def _broadcast_local(
        self,
        room_id: uuid.UUID,
        envelope: dict,
        exclude_conn_id: str | None = None,
    ) -> None:
        dead: list[WebSocket] = []

        for ws in list(self._rooms.get(room_id, {})):
            if exclude_conn_id is not None and self._conn_ids.get(ws) == exclude_conn_id:
                continue
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_json(envelope)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Local broadcast failed for peer in room=%s: %s", room_id, exc)
                dead.append(ws)

        for ws in dead:
            self.disconnect(room_id, ws)


# Module-level singleton shared across the entire process lifetime.
manager = ConnectionManager()
