import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.database import async_session_factory
from app.models import WebhookSubscription

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DELIVERIES = 10
RETRY_DELAYS_SECONDS = (2, 4, 8)
DELIVERY_TIMEOUT_SECONDS = 5.0
DLQ_MAXLEN = 1000
SIGNATURE_HEADER = "X-Signature-256"


@dataclass
class WebhookDeliveryJob:
    subscription_id: uuid.UUID
    target_url: str
    secret_key: str
    payload: dict


class WebhookDispatcher:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[WebhookDeliveryJob] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_DELIVERIES)
        self._client: httpx.AsyncClient | None = None
        self._worker_task: asyncio.Task | None = None
        self._delivery_tasks: set[asyncio.Task] = set()
        self.dead_letter_queue: deque[dict] = deque(maxlen=DLQ_MAXLEN)

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS)
        self._worker_task = asyncio.create_task(self._run())
        logger.info("WebhookDispatcher started")

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        for task in list(self._delivery_tasks):
            task.cancel()
        if self._delivery_tasks:
            await asyncio.gather(*self._delivery_tasks, return_exceptions=True)

        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("WebhookDispatcher stopped")

    async def notify_message_created(
        self,
        room_id: uuid.UUID,
        content: str,
        username: str | None,
        created_at: datetime,
    ) -> None:
        async with async_session_factory() as session:
            result = await session.execute(
                select(WebhookSubscription).where(
                    WebhookSubscription.room_id == room_id,
                    WebhookSubscription.is_active.is_(True),
                )
            )
            subscriptions = result.scalars().all()

        if not subscriptions:
            return

        event_payload = {
            "event": "message_created",
            "room_id": str(room_id),
            "content": content,
            "username": username,
            "created_at": created_at.isoformat(),
        }

        for sub in subscriptions:
            await self._queue.put(
                WebhookDeliveryJob(
                    subscription_id=sub.id,
                    target_url=sub.target_url,
                    secret_key=sub.secret_key,
                    payload=event_payload,
                )
            )

    async def _run(self) -> None:
        try:
            while True:
                job = await self._queue.get()
                task = asyncio.create_task(self._deliver_with_retry(job))
                self._delivery_tasks.add(task)
                task.add_done_callback(self._delivery_tasks.discard)
        except asyncio.CancelledError:
            pass

    async def _deliver_with_retry(self, job: WebhookDeliveryJob) -> None:
        body = json.dumps(job.payload).encode("utf-8")
        signature = "sha256=" + hmac.new(job.secret_key.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: signature}

        total_attempts = len(RETRY_DELAYS_SECONDS) + 1
        last_error: str | None = None

        for attempt in range(1, total_attempts + 1):
            async with self._semaphore:
                try:
                    assert self._client is not None
                    response = await self._client.post(job.target_url, content=body, headers=headers)
                    if 200 <= response.status_code < 300:
                        logger.info(
                            "Webhook delivered | subscription=%s url=%s status=%d attempt=%d",
                            job.subscription_id, job.target_url, response.status_code, attempt,
                        )
                        return
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                except httpx.RequestError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"

            if attempt <= len(RETRY_DELAYS_SECONDS):
                delay = RETRY_DELAYS_SECONDS[attempt - 1]
                logger.warning(
                    "Webhook delivery failed | subscription=%s attempt=%d/%d error=%s | retrying in %ds",
                    job.subscription_id, attempt, total_attempts, last_error, delay,
                )
                await asyncio.sleep(delay)

        self.dead_letter_queue.append({
            "subscription_id": str(job.subscription_id),
            "target_url": job.target_url,
            "payload": job.payload,
            "error": last_error,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error(
            "Webhook delivery exhausted retries, moved to DLQ | subscription=%s url=%s error=%s",
            job.subscription_id, job.target_url, last_error,
        )


dispatcher = WebhookDispatcher()
