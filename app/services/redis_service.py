import uuid
import logging

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

PRESENCE_TTL_SECONDS = 60


class RedisService:

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: redis.Redis | None = None

    async def init(self) -> None:
        self._client = redis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        await self._client.ping()
        logger.info("RedisService connected | url=%s", self._redis_url)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("RedisService connection closed")

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("RedisService.init() must be called before use")
        return self._client

    @staticmethod
    def _presence_key(room_id: uuid.UUID, user_id: uuid.UUID) -> str:
        return f"presence:{room_id}:{user_id}"

    async def set_presence(self, room_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.client.set(
            self._presence_key(room_id, user_id),
            "1",
            ex=PRESENCE_TTL_SECONDS,
        )

    async def refresh_presence(self, room_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.client.expire(
            self._presence_key(room_id, user_id),
            PRESENCE_TTL_SECONDS,
        )

    async def remove_presence(self, room_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove presence immediately, e.g. on graceful disconnect."""
        await self.client.delete(self._presence_key(room_id, user_id))

    async def get_online_users(self, room_id: uuid.UUID) -> list[str]:
        pattern = f"presence:{room_id}:*"
        user_ids: set[str] = set()
        async for key in self.client.scan_iter(match=pattern, count=100):
            user_ids.add(key.split(":", 2)[2])
        return list(user_ids)

    async def check_rate_limit(
        self,
        key: str,
        limit: int = 5,
        window_seconds: int = 1,
    ) -> bool:
        current = await self.client.incr(key)
        if current == 1:
            await self.client.expire(key, window_seconds)
        return current <= limit


redis_service = RedisService(settings.REDIS_URL)
