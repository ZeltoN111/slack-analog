import uuid
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    user_id: uuid.UUID | None = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    room_id: uuid.UUID
    user_id: uuid.UUID | None
    content: str
    created_at: datetime
