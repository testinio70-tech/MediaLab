from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from config import (
    APP_VERSION,
    HEARTBEAT_FILE,
    HEARTBEAT_WRITE_INTERVAL_SECONDS,
)
from services.download_queue import DOWNLOAD_QUEUE
from services.enhancement_queue import FAST_ENHANCEMENT_QUEUE


logger = logging.getLogger(__name__)


class HeartbeatService:
    def __init__(self, path: Path, interval_seconds: int) -> None:
        self.path = path
        self.interval_seconds = max(30, interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._started_at = monotonic()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def uptime_seconds(self) -> int:
        return max(0, int(monotonic() - self._started_at))

    async def start(self) -> None:
        if self.running:
            return

        self._started_at = monotonic()
        await self._write("running")
        self._task = asyncio.create_task(
            self._loop(),
            name="medialab-heartbeat",
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None

        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        await self._write("stopped")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            await self._write("running")

    async def _write(self, status: str) -> None:
        try:
            active, waiting = await DOWNLOAD_QUEUE.snapshot()
            fast_active, fast_waiting = await FAST_ENHANCEMENT_QUEUE.snapshot()
            payload: dict[str, Any] = {
                "status": status,
                "version": APP_VERSION,
                "pid": os.getpid(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": self.uptime_seconds,
                "download_queue": {
                    "active": active,
                    "waiting": waiting,
                },
                "fast_enhancement_queue": {
                    "active": fast_active,
                    "waiting": fast_waiting,
                },
            }
            await asyncio.to_thread(_write_json_atomic, self.path, payload)
        except Exception:
            logger.exception("No se pudo actualizar el heartbeat de MediaLab.")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


HEARTBEAT_SERVICE = HeartbeatService(
    HEARTBEAT_FILE,
    HEARTBEAT_WRITE_INTERVAL_SECONDS,
)
