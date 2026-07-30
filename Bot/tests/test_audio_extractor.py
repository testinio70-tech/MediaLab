from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import BadRequest

from handlers import _safe_edit_query_message, send_audio
from services.audio_extractor import (
    AUDIO_MAX_DURATION_SECONDS,
    AudioDownloadResult,
    AudioExtractor,
    supports_audio_url,
)


class AudioExtractorTests(unittest.TestCase):
    def test_supported_platforms_are_limited_to_requested_audio_sources(self) -> None:
        self.assertTrue(supports_audio_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(supports_audio_url("https://youtu.be/abc"))
        self.assertTrue(supports_audio_url("https://www.tiktok.com/@creator/video/1"))
        self.assertTrue(supports_audio_url("https://www.instagram.com/reel/abc/"))
        self.assertFalse(supports_audio_url("https://example.com/video"))

    def test_duration_filter_rejects_long_audio_without_downloading(self) -> None:
        extractor = AudioExtractor()
        rejected = extractor._duration_filter(
            {"duration": AUDIO_MAX_DURATION_SECONDS + 1},
            incomplete=False,
        )
        self.assertIsNotNone(rejected)
        self.assertIsNone(extractor._duration_filter({}, incomplete=False))

    def test_options_convert_to_mp3_without_playlist(self) -> None:
        options = AudioExtractor()._build_options("youtube")
        self.assertEqual(options["format"], "bestaudio/best")
        self.assertTrue(options["noplaylist"])
        self.assertEqual(options["postprocessors"][0]["preferredcodec"], "mp3")


class AudioDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_menu_button_is_safely_ignored(self) -> None:
        query = MagicMock()
        query.edit_message_text = AsyncMock(
            side_effect=BadRequest("Message is not modified")
        )

        edited = await _safe_edit_query_message(query, "Mismo menú", MagicMock())

        self.assertFalse(edited)

    async def test_audio_is_sent_as_telegram_audio(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            audio_path = Path(folder) / "sample.mp3"
            audio_path.write_bytes(b"synthetic-mp3")
            result = AudioDownloadResult(
                success=True,
                message="",
                file_path=audio_path,
                title="Ejemplo",
                author="MediaLab",
                platform="youtube",
                duration=12,
            )
            details_message = MagicMock(message_id=7)
            bot = MagicMock()
            bot.send_message = AsyncMock(return_value=details_message)
            bot.send_audio = AsyncMock()
            message = MagicMock(chat_id=123)
            message.get_bot.return_value = bot

            with patch("handlers._delete_message_later", new=AsyncMock()):
                sent = await send_audio(message, result)

            self.assertTrue(sent)
            self.assertIn("✅ MP3 listo", bot.send_message.await_args.kwargs["text"])
            self.assertEqual(bot.send_audio.await_args.kwargs["title"], "Ejemplo")
            self.assertEqual(bot.send_audio.await_args.kwargs["performer"], "MediaLab")


if __name__ == "__main__":
    unittest.main()
