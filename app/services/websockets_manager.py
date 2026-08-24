import uuid
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections grouped by room_id.

    Internal structure:
        _rooms: dict[room_id, dict[WebSocket, user_id | None]]
    """

    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, dict[WebSocket, uuid.UUID | None]] = {}

    async def connect(
        self,
        room_id: uuid.UUID,
        websocket: WebSocket,
        user_id: uuid.UUID | None = None,
    ) -> None:
        await websocket.accept()
        self._rooms.setdefault(room_id, {})[websocket] = user_id
        logger.info(
            "WS connected   | room=%s user=%s | total=%d",
            room_id, user_id, len(self._rooms[room_id]),
        )

    def disconnect(
        self,
        room_id: uuid.UUID,
        websocket: WebSocket,
    ) -> uuid.UUID | None:
        """Remove a connection. Returns the user_id that left (or None)."""
        room = self._rooms.get(room_id, {})
        left_user_id = room.pop(websocket, None)
        if not room:
            self._rooms.pop(room_id, None)
        logger.info(
            "WS disconnected | room=%s user=%s | remaining=%d",
            room_id, left_user_id, len(room),
        )
        return left_user_id

    def get_online_users(self, room_id: uuid.UUID) -> list[str]:
        """Return deduplicated non-null user_id strings in the room."""
        seen: set[str] = set()
        for uid in self._rooms.get(room_id, {}).values():
            if uid is not None:
                seen.add(str(uid))
        return list(seen)

    async def broadcast_to_room(
        self,
        room_id: uuid.UUID,
        event_type: str,
        data: dict,
        exclude: WebSocket | None = None,
    ) -> None:
        """Broadcast ``{"type": event_type, "data": data}`` to all peers in room."""
        envelope = {"type": event_type, "data": data}
        dead: list[WebSocket] = []

        for ws in list(self._rooms.get(room_id, {})):
            if ws is exclude:
                continue
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_json(envelope)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Broadcast failed for peer in room=%s: %s", room_id, exc)
                dead.append(ws)

        for ws in dead:
            self.disconnect(room_id, ws)


# Module-level singleton shared across the entire process lifetime.
manager = ConnectionManager()
