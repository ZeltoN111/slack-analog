"""
app.schemas
~~~~~~~~~~~
Re-exports all Pydantic schemas so callers can write:

    from app.schemas import UserCreate, RoomResponse, MessageCreate
"""
from app.schemas.user import UserCreate, UserResponse
from app.schemas.room import RoomCreate, RoomResponse
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.webhook import (
    WebhookCreate, WebhookResponse, WebhookPayload,
    WebhookSubscriptionCreate, WebhookSubscriptionOut,
)

__all__ = [
    "UserCreate", "UserResponse",
    "RoomCreate", "RoomResponse",
    "MessageCreate", "MessageResponse",
    "WebhookCreate", "WebhookResponse", "WebhookPayload",
    "WebhookSubscriptionCreate", "WebhookSubscriptionOut",
]
