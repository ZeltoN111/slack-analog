import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import Room, Message, User
from app.schemas import MessageCreate, MessageResponse

router = APIRouter(tags=["messages"])


@router.post(
    "/rooms/{room_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    room_id: uuid.UUID,
    payload: MessageCreate,
    x_idempotency_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    room_res = await db.execute(select(Room).where(Room.id == room_id))
    if not room_res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    if payload.user_id is not None:
        user_res = await db.execute(select(User).where(User.id == payload.user_id))
        if user_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    msg = Message(
        room_id=room_id,
        user_id=payload.user_id,
        content=payload.content,
        idempotency_key=x_idempotency_key,
    )
    db.add(msg)
    try:
        await db.commit()
        await db.refresh(msg)
        return msg
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Message could not be created because a unique value already exists",
        )


@router.get("/rooms/{room_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    room_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(message_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    await db.delete(msg)
    await db.commit()
    return None
