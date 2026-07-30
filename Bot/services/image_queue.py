from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic

from telegram import Message
from telegram.error import TelegramError

from config import (
    PHOTO_MAX_JOBS_PER_USER,
    PHOTO_QUEUE_MAX_SIZE,
    PHOTO_QUEUE_WORKERS,
)


logger = logging.getLogger(__name__)
JobRunner = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class ImageBatchJob:
    key: str
    user_id: int
    source: str
    image_count: int
    mode_label: str
    status_message: Message
    runner: JobRunner
    created_at: float = field(default_factory=monotonic)


@dataclass(slots=True, frozen=True)
class QueueReceipt:
    accepted: bool
    position: int = 0
    reason: str = ""


class ImageBatchQueue:
    """Cola exclusiva para lotes fotográficos; no comparte workers de video."""

    def __init__(
        self,
        *,
        max_size: int,
        workers: int,
        max_jobs_per_user: int,
    ) -> None:
        self.max_size = max(1, max_size)
        self.worker_count = max(1, workers)
        self.max_jobs_per_user = max(1, max_jobs_per_user)
        self._queue: asyncio.Queue[ImageBatchJob] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._keys: set[str] = set()
        self._user_counts: defaultdict[int, int] = defaultdict(int)
        self._active: dict[int, ImageBatchJob] = {}
        self._waiting: list[ImageBatchJob] = []
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(
                self._worker(worker_id),
                name=f"medialab-image-worker-{worker_id}",
            )
            for worker_id in range(1, self.worker_count + 1)
        ]
        logger.info(
            "Cola Foto IA iniciada: %s trabajador, capacidad %s lotes.",
            self.worker_count,
            self.max_size,
        )

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        discarded: list[ImageBatchJob] = []
        while True:
            try:
                discarded.append(self._queue.get_nowait())
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        async with self._lock:
            for job in discarded:
                self._release(job)
            self._waiting.clear()

    async def enqueue(self, job: ImageBatchJob) -> QueueReceipt:
        async with self._lock:
            if not self._started:
                return QueueReceipt(False, reason="unavailable")
            if job.key in self._keys:
                return QueueReceipt(False, reason="duplicate")
            if self._user_counts[job.user_id] >= self.max_jobs_per_user:
                return QueueReceipt(False, reason="user_limit")
            total = len(self._active) + len(self._waiting)
            if total >= self.max_size:
                return QueueReceipt(False, reason="full")

            self._keys.add(job.key)
            self._user_counts[job.user_id] += 1
            self._waiting.append(job)
            self._queue.put_nowait(job)

        return QueueReceipt(True, position=total)

    async def snapshot(self) -> tuple[int, int]:
        async with self._lock:
            return len(self._active), len(self._waiting)

    async def _worker(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()
            async with self._lock:
                self._waiting = [item for item in self._waiting if item.key != job.key]
                self._active[worker_id] = job

            try:
                await _safe_edit(
                    job.status_message,
                    "🖼️ Mejorando lote con Foto IA x2…\n\n"
                    f"📚 Imágenes: {job.image_count}\n"
                    f"🎨 Acabado: {job.mode_label}\n"
                    "⚙️ Cola exclusiva de imágenes",
                )
                await job.runner()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Falló un lote Foto IA: %s", job.source)
                await _safe_edit(
                    job.status_message,
                    "❌ Ocurrió un error durante Foto IA x2.\n\n"
                    "La cola de imágenes fue liberada.",
                )
            finally:
                async with self._lock:
                    self._active.pop(worker_id, None)
                    self._release(job)
                self._queue.task_done()

    def _release(self, job: ImageBatchJob) -> None:
        self._keys.discard(job.key)
        count = self._user_counts.get(job.user_id, 0)
        if count <= 1:
            self._user_counts.pop(job.user_id, None)
        else:
            self._user_counts[job.user_id] = count - 1


async def _safe_edit(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except TelegramError as error:
        logger.debug("No se pudo editar el estado Foto IA: %s", error)


IMAGE_QUEUE = ImageBatchQueue(
    max_size=PHOTO_QUEUE_MAX_SIZE,
    workers=PHOTO_QUEUE_WORKERS,
    max_jobs_per_user=PHOTO_MAX_JOBS_PER_USER,
)
