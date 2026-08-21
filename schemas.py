import uuid
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., max_length=100)

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    username: str
    email: str
    created_at: datetime

class RoomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    created_at: datetime

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