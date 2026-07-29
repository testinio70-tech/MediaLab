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
    FAST_MAX_JOBS_PER_USER,
    FAST_QUEUE_MAX_SIZE,
    FAST_QUEUE_WORKERS,
)


logger = logging.getLogger(__name__)
JobRunner = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class EnhancementJob:
    key: str
    user_id: int
    source: str
    label: str
    status_message: Message
    started_text: str
    runner: JobRunner
    created_at: float = field(default_factory=monotonic)


@dataclass(slots=True, frozen=True)
class QueueReceipt:
    accepted: bool
    position: int = 0
    reason: str = ""


@dataclass(slots=True, frozen=True)
class UserQueueSnapshot:
    active: bool = False
    waiting: bool = False
    position: int = 0
    label: str = ""


class EnhancementQueue:
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

        self._queue: asyncio.Queue[EnhancementJob] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._keys: set[str] = set()
        self._user_counts: defaultdict[int, int] = defaultdict(int)
        self._active: dict[int, EnhancementJob] = {}
        self._waiting_order: list[EnhancementJob] = []
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
                name=f"medialab-enhancement-worker-{worker_id}",
            )
            for worker_id in range(1, self.worker_count + 1)
        ]
        logger.info(
            "Cola de mejoras iniciada: %s trabajador(es), capacidad %s.",
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

        waiting_jobs: list[EnhancementJob] = []
        while True:
            try:
                waiting_jobs.append(self._queue.get_nowait())
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        async with self._lock:
            for job in waiting_jobs:
                self._release_job(job)
            self._waiting_order.clear()

        logger.info(
            "Cola de mejoras detenida. Trabajos descartados: %s.",
            len(waiting_jobs),
        )

    async def enqueue(self, job: EnhancementJob) -> QueueReceipt:
        async with self._lock:
            if not self._started:
                return QueueReceipt(False, reason="unavailable")
            if job.key in self._keys:
                return QueueReceipt(False, reason="duplicate")
            if self._user_counts[job.user_id] >= self.max_jobs_per_user:
                return QueueReceipt(False, reason="user_limit")

            total_jobs = len(self._waiting_order) + len(self._active)
            if total_jobs >= self.max_size:
                return QueueReceipt(False, reason="full")

            position = total_jobs
            self._keys.add(job.key)
            self._user_counts[job.user_id] += 1
            self._waiting_order.append(job)
            self._queue.put_nowait(job)

        logger.info(
            "Trabajo de mejora añadido: usuario=%s posición=%s origen=%s",
            job.user_id,
            position,
            job.source,
        )
        return QueueReceipt(True, position=position)

    async def snapshot(self) -> tuple[int, int]:
        async with self._lock:
            return len(self._active), len(self._waiting_order)

    async def user_snapshot(self, user_id: int) -> UserQueueSnapshot:
        async with self._lock:
            for job in self._active.values():
                if job.user_id == user_id:
                    return UserQueueSnapshot(
                        active=True,
                        label=job.label,
                    )

            for position, job in enumerate(self._waiting_order, start=1):
                if job.user_id == user_id:
                    return UserQueueSnapshot(
                        waiting=True,
                        position=position,
                        label=job.label,
                    )

        return UserQueueSnapshot()

    async def _worker(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()

            async with self._lock:
                self._waiting_order = [
                    waiting_job
                    for waiting_job in self._waiting_order
                    if waiting_job.key != job.key
                ]
                self._active[worker_id] = job

            try:
                await _safe_edit(job.status_message, job.started_text)
                logger.info(
                    "Mejora iniciada: worker=%s usuario=%s origen=%s",
                    worker_id,
                    job.user_id,
                    job.source,
                )
                await job.runner()
            except asyncio.CancelledError:
                logger.info(
                    "Mejora cancelada al detener el bot: worker=%s origen=%s",
                    worker_id,
                    job.source,
                )
                raise
            except Exception:
                logger.exception(
                    "Error inesperado en mejora: worker=%s origen=%s",
                    worker_id,
                    job.source,
                )
                await _safe_edit(
                    job.status_message,
                    "❌ Ocurrió un error inesperado durante la mejora.\n\n"
                    "La cola fue liberada para continuar con el siguiente trabajo.",
                )
            finally:
                async with self._lock:
                    self._active.pop(worker_id, None)
                    self._release_job(job)

                self._queue.task_done()
                logger.info(
                    "Mejora finalizada: worker=%s usuario=%s origen=%s",
                    worker_id,
                    job.user_id,
                    job.source,
                )

    def _release_job(self, job: EnhancementJob) -> None:
        self._keys.discard(job.key)
        current_count = self._user_counts.get(job.user_id, 0)
        if current_count <= 1:
            self._user_counts.pop(job.user_id, None)
        else:
            self._user_counts[job.user_id] = current_count - 1


async def _safe_edit(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except TelegramError as error:
        logger.debug("No se pudo editar un estado Fast1080: %s", error)


ENHANCEMENT_QUEUE = EnhancementQueue(
    max_size=FAST_QUEUE_MAX_SIZE,
    workers=FAST_QUEUE_WORKERS,
    max_jobs_per_user=FAST_MAX_JOBS_PER_USER,
)

# Compatibilidad temporal con el nombre utilizado por alpha.2.
FAST_ENHANCEMENT_QUEUE = ENHANCEMENT_QUEUE
