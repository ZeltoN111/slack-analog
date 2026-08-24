"""
app.schemas
~~~~~~~~~~~
Re-exports all Pydantic schemas so callers can write:

    from app.schemas import UserCreate, RoomResponse, MessageCreate
"""
from app.schemas.user import UserCreate, UserResponse
from app.schemas.room import RoomCreate, RoomResponse
from app.schemas.message import MessageCreate, MessageResponse

__all__ = [
    "UserCreate", "UserResponse",
    "RoomCreate", "RoomResponse",
    "MessageCreate", "MessageResponse",
]
