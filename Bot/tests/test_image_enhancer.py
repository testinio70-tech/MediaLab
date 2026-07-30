from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np

from handlers import send_video
from models import DownloadResult
from services.image_enhancer import ImageBatchEnhancer, target_dimensions


class ImageEnhancerTests(unittest.TestCase):
    def test_target_dimensions_double_regular_photo(self) -> None:
        self.assertEqual(target_dimensions(640, 480), (1280, 960))

    def test_real_model_processes_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "input.jpg"
            output_folder = root / "output"
            sample = np.full((32, 48, 3), 120, dtype=np.uint8)
            cv2.circle(sample, (24, 16), 10, (30, 180, 220), thickness=-1)
            self.assertTrue(cv2.imwrite(str(input_path), sample))

            result = ImageBatchEnhancer().process_batch(
                [input_path],
                output_folder,
            )

            self.assertTrue(result.success, result.error)
            self.assertEqual(len(result.output_paths), 1)
            output = cv2.imread(str(result.output_paths[0]))
            self.assertIsNotNone(output)
            self.assertEqual(output.shape[:2], (64, 96))

    def test_both_free_local_modes_process_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "input.jpg"
            sample = np.full((24, 32, 3), 110, dtype=np.uint8)
            cv2.line(sample, (2, 2), (28, 20), (20, 160, 210), thickness=2)
            self.assertTrue(cv2.imwrite(str(input_path), sample))

            enhancer = ImageBatchEnhancer()
            faithful = enhancer.process_batch(
                [input_path],
                root / "faithful",
                mode="faithful",
            )
            detail = enhancer.process_batch(
                [input_path],
                root / "detail",
                mode="detail",
            )

            self.assertTrue(faithful.success, faithful.error)
            self.assertTrue(detail.success, detail.error)
            self.assertIn("Restauración fiel", faithful.provider)
            self.assertIn("Detalle IA local", detail.provider)


class VideoDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_has_no_caption_and_details_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            video_path = Path(folder) / "video.mp4"
            video_path.write_bytes(b"synthetic-video")
            result = DownloadResult(
                success=True,
                message="",
                file_path=video_path,
                engine="TikWM Original",
                width=1080,
                height=1920,
            )
            details_message = MagicMock(message_id=99)
            bot = MagicMock()
            bot.send_message = AsyncMock(return_value=details_message)
            bot.send_video = AsyncMock()
            message = MagicMock(chat_id=123)
            message.get_bot.return_value = bot

            with patch(
                "handlers._delete_message_later",
                new=AsyncMock(),
            ):
                sent = await send_video(
                    message,
                    result,
                    reply_to_message=False,
                )

            self.assertTrue(sent)
            details = bot.send_message.await_args.kwargs["text"]
            self.assertIn("✅ Listo", details)
            self.assertIn("⚙️ Motor: TikWM Original", details)
            self.assertIn("📦 Tamaño:", details)
            self.assertIn("📐 Resolución: 1080 × 1920", details)
            send_kwargs = bot.send_video.await_args.kwargs
            self.assertNotIn("caption", send_kwargs)


if __name__ == "__main__":
    unittest.main()
