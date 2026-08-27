import uuid
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict, HttpUrl


class WebhookCreate(BaseModel):
    room_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=100)


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    room_id: uuid.UUID
    name: str
    token: str
    secret: str
    is_active: bool
    created_at: datetime


class WebhookPayload(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    username: str | None = Field(default=None, max_length=100)


class WebhookSubscriptionCreate(BaseModel):
    target_url: HttpUrl


class WebhookSubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    room_id: uuid.UUID
    target_url: str
    secret_key: str
    is_active: bool
    created_at: datetime
