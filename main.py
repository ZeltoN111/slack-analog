import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from databases import init_db, get_db
import models
import schemas

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Slack Analog API", lifespan=lifespan)

# --- USERS ---
@app.post("/users", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    user = models.User(username=payload.username, email=payload.email)
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
        return user
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

# --- ROOMS ---
@app.post("/rooms", response_model=schemas.RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(payload: schemas.RoomCreate, db: AsyncSession = Depends(get_db)):
    room = models.Room(name=payload.name)
    db.add(room)
    try:
        await db.commit()
        await db.refresh(room)
        return room
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room name already taken")

@app.get("/rooms", response_model=list[schemas.RoomResponse], status_code=status.HTTP_200_OK)
async def list_rooms(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Room).order_by(models.Room.created_at.desc()))
    return result.scalars().all()

# --- MESSAGES ---
@app.post("/rooms/{room_id}/messages", response_model=schemas.MessageResponse, status_code=status.HTTP_201_CREATED)
async def create_message(
    room_id: uuid.UUID,
    payload: schemas.MessageCreate,
    x_idempotency_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db)
):
    # 1. Перевіряємо існування кімнати
    room_res = await db.execute(select(models.Room).where(models.Room.id == room_id))
    if not room_res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    # 2. Створюємо повідомлення
    msg = models.Message(
        room_id=room_id,
        user_id=payload.user_id,
        content=payload.content,
        idempotency_key=x_idempotency_key
    )
    db.add(msg)

    # 3. Обробляємо конфлікт ключа ідемпотентності
    try:
        await db.commit()
        await db.refresh(msg)
        return msg
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Duplicate request: message with this idempotency key already processed"
        )

@app.get("/rooms/{room_id}/messages", response_model=list[schemas.MessageResponse], status_code=status.HTTP_200_OK)
async def list_messages(
    room_id: uuid.UUID, 
    limit: int = 50, 
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(models.Message)
        .where(models.Message.room_id == room_id)
        .order_by(models.Message.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()

@app.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(message_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Message).where(models.Message.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    
    await db.delete(msg)
    await db.commit()
    return None