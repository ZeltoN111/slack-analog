from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.database import init_db
from app.routers import users, rooms, messages, websockets
from app.services.redis_service import redis_service
from app.services.websockets_manager import manager

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await redis_service.init()
    await manager.init()
    yield
    await manager.close()
    await redis_service.close()


from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

app = FastAPI(title="Slack Analog API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(users.router)
app.include_router(rooms.router)
app.include_router(messages.router)
app.include_router(websockets.router)


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
