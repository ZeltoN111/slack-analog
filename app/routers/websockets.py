import json
import uuid
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.models import User, Room, Message
from app.services.websockets_manager import manager
from app.services.redis_service import redis_service
from app.workers.webhook_dispatcher import dispatcher

logger = logging.getLogger(__name__)
MAX_WS_PAYLOAD_BYTES = 16_384

router = APIRouter(tags=["websockets"])


def _msg_to_dict(msg: Message, username: str | None = None) -> dict:
    return {
        "id":         str(msg.id),
        "room_id":    str(msg.room_id),
        "user_id":    str(msg.user_id) if msg.user_id else None,
        "username":   username,
        "content":    msg.content,
        "created_at": msg.created_at.isoformat(),
    }


@router.websocket("/ws/{room_id}")
async def websocket_endpoint(
    room_id: uuid.UUID,
    websocket: WebSocket,
    user_id: uuid.UUID | None = Query(default=None),
) -> None:
    async with async_session_factory() as session:
        room_result = await session.execute(
            select(Room).where(Room.id == room_id)
        )
        if room_result.scalar_one_or_none() is None:
            await websocket.accept()
            await websocket.close(code=4004, reason="Room not found")
            logger.warning("WS rejected (4004) | room=%s does not exist", room_id)
            return

        conn_username: str | None = None
        if user_id is not None:
            user_result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user_obj = user_result.scalar_one_or_none()
            if user_obj is None:
                await websocket.accept()
                await websocket.close(code=4003, reason="User not found")
                logger.warning("WS rejected (4003) | user=%s does not exist", user_id)
                return
            conn_username = user_obj.username

    await manager.connect(room_id, websocket, user_id)

    if user_id is not None:
        await redis_service.set_presence(room_id, user_id)

    try:
        async with async_session_factory() as session:
            history_result = await session.execute(
                select(Message)
                .where(Message.room_id == room_id)
                .order_by(desc(Message.created_at))
                .limit(50)
                .options(selectinload(Message.user))
            )
            history_msgs = list(reversed(history_result.scalars().all()))

        await websocket.send_json({
            "type": "history",
            "data": {
                "messages": [
                    _msg_to_dict(m, username=m.user.username if m.user else None)
                    for m in history_msgs
                ],
                "online_users": await redis_service.get_online_users(room_id),
            },
        })
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
        if user_id is not None:
            await redis_service.remove_presence(room_id, user_id)
        raise
    except Exception:
        manager.disconnect(room_id, websocket)
        if user_id is not None:
            await redis_service.remove_presence(room_id, user_id)
        logger.exception("WS history failed | room=%s user=%s", room_id, user_id)
        raise

    if user_id is not None:
        await manager.publish_event(
            room_id,
            event_type="user_joined",
            data={
                "user_id":      str(user_id),
                "username":     conn_username,
                "online_users": await redis_service.get_online_users(room_id),
            },
            exclude=websocket,
        )

    try:
        while True:
            try:
                raw_payload = await websocket.receive_text()
            except WebSocketDisconnect:
                raise
            except ValueError:
                await websocket.send_json({
                    "type": "error",
                    "data": {"detail": "Invalid JSON payload"},
                })
                continue

            if len(raw_payload.encode("utf-8")) > MAX_WS_PAYLOAD_BYTES:
                await websocket.send_json({
                    "type": "error",
                    "data": {"detail": "Payload is too large"},
                })
                continue

            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "data": {"detail": "Invalid JSON payload"},
                })
                continue

            if not isinstance(payload, dict):
                await websocket.send_json({
                    "type": "error",
                    "data": {"detail": "Payload must be a JSON object"},
                })
                continue

            event_type = payload.get("type")

            if event_type not in ("typing", "message"):
                await websocket.send_json({
                    "type": "error",
                    "data": {"detail": f"Unknown event type: '{event_type or ''}'"},
                })
                continue

            if user_id is not None:
                await redis_service.refresh_presence(room_id, user_id)

            if event_type == "typing":
                await manager.publish_event(
                    room_id,
                    event_type="typing",
                    data={
                        "user_id":  str(user_id) if user_id else None,
                        "username": conn_username,
                    },
                    exclude=websocket,
                )

            elif event_type == "message":
                raw_content = payload.get("content")
                if not isinstance(raw_content, str):
                    await websocket.send_json({
                        "type": "error",
                        "data": {"detail": "Field 'content' must be a string"},
                    })
                    continue

                content = raw_content.strip()
                if not content:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"detail": "Field 'content' is required and must not be empty"},
                    })
                    continue
                if len(content) > 4000:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"detail": "Field 'content' must not exceed 4000 characters"},
                    })
                    continue

                async with async_session_factory() as session:
                    msg = Message(
                        room_id=room_id,
                        user_id=user_id,
                        content=content,
                    )
                    session.add(msg)
                    await session.commit()
                    await session.refresh(msg)

                    username: str | None = None
                    if user_id is not None:
                        user_result = await session.execute(
                            select(User).where(User.id == user_id)
                        )
                        user_obj = user_result.scalar_one_or_none()
                        if user_obj:
                            username = user_obj.username

                await manager.publish_event(
                    room_id,
                    event_type="message",
                    data=_msg_to_dict(msg, username=username),
                )
                await dispatcher.notify_message_created(
                    room_id,
                    content=msg.content,
                    username=username,
                    created_at=msg.created_at,
                )

    except WebSocketDisconnect:
        left_user_id = manager.disconnect(room_id, websocket)
        logger.info("WS client left | room=%s user=%s", room_id, left_user_id)

        if left_user_id is not None:
            await redis_service.remove_presence(room_id, left_user_id)
            await manager.publish_event(
                room_id,
                event_type="user_left",
                data={
                    "user_id":      str(left_user_id),
                    "username":     conn_username,
                    "online_users": await redis_service.get_online_users(room_id),
                },
            )
    except Exception:
        left_user_id = manager.disconnect(room_id, websocket)
        if left_user_id is not None:
            await redis_service.remove_presence(room_id, left_user_id)
        logger.exception("WS client failed | room=%s user=%s", room_id, user_id)
        raise
