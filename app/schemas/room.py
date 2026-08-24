import uuid
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class RoomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
