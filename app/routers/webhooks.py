import hashlib
import hmac
import secrets
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Room, Webhook, WebhookSubscription, Message
from app.schemas import (
    WebhookCreate, WebhookResponse, WebhookPayload,
    WebhookSubscriptionCreate, WebhookSubscriptionOut,
)
from app.services.redis_service import redis_service
from app.services.websockets_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

WEBHOOK_RATE_LIMIT = 5
WEBHOOK_RATE_WINDOW_SECONDS = 1
SIGNATURE_HEADER = "X-Signature-256"


def _verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    provided = signature_header.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


@router.post("/hooks", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreate,
    db: AsyncSession = Depends(get_db),
) -> Webhook:
    room = await db.get(Room, payload.room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    webhook = Webhook(
        room_id=payload.room_id,
        name=payload.name,
        token=secrets.token_urlsafe(32),
        secret=secrets.token_urlsafe(32),
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.post("/hooks/{token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(
    token: str,
    request: Request,
    payload: WebhookPayload,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # 1. Rate limit, keyed per-token, checked before touching the DB.
    allowed = await redis_service.check_rate_limit(
        key=f"ratelimit:webhook:{token}",
        limit=WEBHOOK_RATE_LIMIT,
        window_seconds=WEBHOOK_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    # 2. Look up an active webhook by token.
    result = await db.execute(
        select(Webhook).where(Webhook.token == token, Webhook.is_active.is_(True))
    )
    webhook = result.scalar_one_or_none()
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # 3. Verify the HMAC signature over the exact raw request body.
    raw_body = await request.body()
    if not _verify_signature(webhook.secret, raw_body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing signature",
        )

    # 4. Persist the message as a system/bot message (no user_id).
    msg = Message(
        room_id=webhook.room_id,
        user_id=None,
        content=payload.content,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # 5. Fan the message out to connected clients via Redis Pub/Sub.
    display_name = payload.username or webhook.name
    await manager.publish_event(
        webhook.room_id,
        event_type="message",
        data={
            "id":         str(msg.id),
            "room_id":    str(msg.room_id),
            "user_id":    None,
            "username":   display_name,
            "content":    msg.content,
            "created_at": msg.created_at.isoformat(),
        },
    )

    logger.info("Webhook delivered | webhook=%s room=%s", webhook.id, webhook.room_id)
    return {"status": "accepted", "message_id": str(msg.id)}


# ── Outgoing webhook subscriptions ──────────────────────────────────────────

@router.post(
    "/rooms/{room_id}/subscriptions",
    response_model=WebhookSubscriptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    room_id: uuid.UUID,
    payload: WebhookSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
) -> WebhookSubscription:
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    subscription = WebhookSubscription(
        room_id=room_id,
        target_url=str(payload.target_url),
        secret_key=secrets.token_urlsafe(32),
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return subscription


@router.get("/rooms/{room_id}/subscriptions", response_model=list[WebhookSubscriptionOut])
async def list_subscriptions(
    room_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    result = await db.execute(
        select(WebhookSubscription).where(WebhookSubscription.room_id == room_id)
    )
    return result.scalars().all()


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    subscription_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    await db.delete(subscription)
    await db.commit()
    return None
