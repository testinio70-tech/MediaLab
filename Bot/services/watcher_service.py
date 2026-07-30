from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from telegram import Bot

from config import (
    WATCHER_MEDIA_RETENTION_SECONDS,
    WATCHER_MAX_PER_USER,
)
from services.upload_coordinator import TELEGRAM_UPLOAD_LOCK
from services.watcher_database import (
    WATCHER_DATABASE,
    Watcher,
    WatcherDatabase,
    WatcherSource,
)
from services.watcher_sources import DownloadedPost, discover, download_post


logger = logging.getLogger(__name__)

ACTIVE_WATCHER_PLATFORMS = frozenset({"tiktok", "instagram", "facebook"})
PLANNED_WATCHER_PLATFORMS = frozenset({"x", "youtube"})


class WatcherLimitError(ValueError):
    pass


class WatcherService:
    def __init__(self, database: WatcherDatabase = WATCHER_DATABASE) -> None:
        self.database = database
        self._task: asyncio.Task[None] | None = None
        self._bot: Bot | None = None
        self._stop_event = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, bot: Bot) -> None:
        if self.running:
            return
        self._bot = bot
        await self.database.initialize()
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._loop(),
            name="medialab-watcher-scheduler",
        )
        logger.info("Auto-watchers iniciados: TikTok 10 min; Instagram/Facebook 20 min.")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._stop_event.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._bot = None

    async def create_watcher(
        self,
        owner_user_id: int,
        title: str,
        destination: str,
        sources: list[tuple[str, str, int]],
    ) -> Watcher:
        current = await self.database.count_for_user(owner_user_id)
        if current >= WATCHER_MAX_PER_USER:
            raise WatcherLimitError(
                f"El límite actual es de {WATCHER_MAX_PER_USER} watchers por usuario."
            )
        if not sources:
            raise ValueError("El watcher debe tener al menos una red social.")
        platforms = {platform for platform, _, _ in sources}
        unsupported = platforms - ACTIVE_WATCHER_PLATFORMS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"Estas plataformas aún no están activadas: {names}.")
        return await self.database.create(
            owner_user_id,
            title.strip()[:100],
            destination.strip()[:200],
            sources,
        )

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.run_due_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error en el ciclo de auto-watchers.")

    async def run_due_cycle(self) -> None:
        if self._bot is None:
            return
        if self._cycle_lock.locked():
            return
        async with self._cycle_lock:
            sources = await self.database.due_sources(time.time())
            for source in sources:
                await self._process_source(source)

    async def check_watcher(self, watcher_id: int) -> None:
        if self._bot is None:
            return
        sources = await self.database.sources_for_watchers([watcher_id])
        for source in sources:
            await self._process_source(source)

    async def _process_source(self, source: WatcherSource) -> None:
        watcher = await self.database.watcher(source.watcher_id)
        if watcher is None or not watcher.enabled:
            return

        try:
            posts = await discover(source)
            if not source.initial_scan_done:
                for post in posts:
                    await self.database.add_post(
                        source.watcher_id,
                        source.id,
                        source.platform,
                        post.url,
                        post.key,
                    )
                await self.database.mark_initial_scan_done(source.id)
            else:
                for post in reversed(posts):
                    if await self.database.has_post(watcher.id, post.key):
                        continue
                    await self._download_and_deliver(watcher, source, post)
            await self.database.mark_checked(
                source.id,
                time.time() + source.interval_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception(
                "Watcher %s falló en %s: %s",
                watcher.title,
                source.platform,
                error,
            )
            await self.database.mark_checked(
                source.id,
                time.time() + source.interval_seconds,
                str(error),
            )
            if source.error_count == 0 or (source.error_count + 1) % 3 == 0:
                await self._notify_failure(watcher, source.platform)

    async def _download_and_deliver(
        self,
        watcher: Watcher,
        source: WatcherSource,
        post: Any,
    ) -> None:
        downloaded: DownloadedPost | None = None
        try:
            downloaded = await download_post(source, post)
            await self._send_post(watcher, source, downloaded)
            await self.database.add_post(
                watcher.id,
                source.id,
                source.platform,
                post.url,
                post.key,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception(
                "No se pudo descargar o enviar un post de %s: %s",
                source.platform,
                error,
            )
            await self._notify_failure(watcher, source.platform)
        finally:
            if downloaded is not None:
                self._schedule_cleanup(downloaded.files)

    async def _send_post(
        self,
        watcher: Watcher,
        source: WatcherSource,
        downloaded: DownloadedPost,
    ) -> None:
        if self._bot is None:
            raise RuntimeError("El bot no está disponible para entregar el watcher.")
        text = (
            "🆕 Nuevo post detectado\n\n"
            f"👁 Watcher: {watcher.title}\n"
            f"🌐 Red: {source.platform.title()}\n"
            f"⚙️ Motor: {'TikWM' if source.platform == 'tiktok' else source.platform.title()}"
        )
        async with TELEGRAM_UPLOAD_LOCK:
            await self._bot.send_message(chat_id=watcher.destination, text=text)
            for path in downloaded.files:
                if not path.is_file():
                    continue
                with path.open("rb") as media:
                    suffix = path.suffix.lower()
                    if suffix in {".mp4", ".m4v", ".mov", ".mkv", ".webm"}:
                        await self._bot.send_video(
                            chat_id=watcher.destination,
                            video=media,
                            supports_streaming=True,
                            filename=path.name,
                        )
                    elif suffix in {".jpg", ".jpeg", ".png", ".webp"}:
                        await self._bot.send_photo(
                            chat_id=watcher.destination,
                            photo=media,
                        )
                    else:
                        await self._bot.send_document(
                            chat_id=watcher.destination,
                            document=media,
                            filename=path.name,
                        )

    async def _notify_failure(self, watcher: Watcher, platform: str) -> None:
        if self._bot is None:
            return
        try:
            await self._bot.send_message(
                chat_id=watcher.owner_user_id,
                text=(
                    f"⚠️ Error al revisar {platform.title()} en el watcher "
                    f"“{watcher.title}”.\n\n"
                    "Por favor, reporta este error a un desarrollador."
                ),
            )
        except Exception:
            logger.exception("No se pudo notificar el fallo del watcher al propietario.")

    @staticmethod
    def _schedule_cleanup(paths: tuple[Path, ...]) -> None:
        async def cleanup() -> None:
            await asyncio.sleep(WATCHER_MEDIA_RETENTION_SECONDS)
            for path in paths:
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    logger.warning("No se pudo borrar el temporal del watcher: %s", path)

        asyncio.create_task(cleanup(), name="watcher-media-cleanup")


WATCHER_SERVICE = WatcherService()
