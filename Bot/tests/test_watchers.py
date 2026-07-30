from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from handlers import _normalize_watcher_destination, _normalize_watcher_sources
from services.watcher_database import WatcherDatabase


class WatcherDatabaseTests(unittest.TestCase):
    def test_create_and_deduplicate_posts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = WatcherDatabase(Path(folder) / "watchers.sqlite3")

            async def scenario() -> None:
                await database.initialize()
                watcher = await database.create(
                    123,
                    "Noticias",
                    "@canal",
                    [("tiktok", "https://www.tiktok.com/@creator", 600)],
                )
                sources = await database.sources_for_watchers([watcher.id])
                self.assertEqual(len(sources), 1)
                self.assertFalse(sources[0].initial_scan_done)
                self.assertTrue(
                    await database.add_post(
                        watcher.id,
                        sources[0].id,
                        "tiktok",
                        "https://www.tiktok.com/@creator/video/1",
                        "1",
                    )
                )
                self.assertFalse(
                    await database.add_post(
                        watcher.id,
                        sources[0].id,
                        "tiktok",
                        "https://www.tiktok.com/@creator/video/1",
                        "1",
                    )
                )

            asyncio.run(scenario())

    def test_source_normalization_and_destination(self) -> None:
        sources = _normalize_watcher_sources(
            [
                "https://www.tiktok.com/@creator",
                "https://www.instagram.com/creator",
                "-",
            ]
        )
        self.assertEqual([item[0] for item in sources], ["tiktok", "instagram"])
        self.assertEqual(_normalize_watcher_destination("https://t.me/mychannel"), "@mychannel")
        self.assertEqual(_normalize_watcher_destination("-100123456789"), "-100123456789")
        self.assertIsNone(_normalize_watcher_destination("https://example.com/webhook"))


class WatcherDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_is_separate_from_media(self) -> None:
        from services.watcher_database import Watcher, WatcherSource
        from services.watcher_service import WatcherService
        from services.watcher_sources import DownloadedPost

        with tempfile.TemporaryDirectory() as folder:
            media_path = Path(folder) / "video.mp4"
            media_path.write_bytes(b"video")
            bot = MagicMock()
            bot.send_message = AsyncMock()
            bot.send_video = AsyncMock()
            service = WatcherService(WatcherDatabase(Path(folder) / "db.sqlite3"))
            service._bot = bot
            watcher = Watcher(1, 123, "Noticias", "@canal", True, "now")
            source = WatcherSource(
                1, 1, "tiktok", "https://www.tiktok.com/@creator", 600, 0, None, None, 0, True
            )

            await service._send_post(
                watcher,
                source,
                DownloadedPost("tiktok", "Video", (media_path,)),
            )

            bot.send_message.assert_awaited_once()
            bot.send_video.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
