from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"

from services.video_restore import (  # noqa: E402
    RESTORATION_PROFILES,
    OverlayTextDetector,
    TemporalNaturalColor,
    VIDEO_RESTORER,
    _parse_rate,
    _target_dimensions,
    cv2,
    np,
)


class VideoRestorePureTests(unittest.TestCase):
    def test_parse_rate(self) -> None:
        self.assertAlmostEqual(_parse_rate("30000/1001"), 29.970, places=3)
        self.assertEqual(_parse_rate("30"), 30.0)
        self.assertEqual(_parse_rate("0/0"), 0.0)
        self.assertEqual(_parse_rate("invalid"), 0.0)

    def test_target_dimensions_preserve_without_hd_plus(self) -> None:
        self.assertEqual(
            _target_dimensions(721, 1281, upscale_to_1080=False),
            (720, 1280),
        )

    def test_target_dimensions_upscale_portrait_to_1080(self) -> None:
        self.assertEqual(
            _target_dimensions(720, 1280, upscale_to_1080=True),
            (1080, 1920),
        )

    def test_target_dimensions_do_not_expand_existing_1080(self) -> None:
        self.assertEqual(
            _target_dimensions(1080, 1920, upscale_to_1080=True),
            (1080, 1920),
        )

    def test_target_dimensions_reduce_4k_to_1080(self) -> None:
        self.assertEqual(
            _target_dimensions(3840, 2160, upscale_to_1080=False),
            (1920, 1080),
        )


@unittest.skipIf(cv2 is None or np is None, "OpenCV/NumPy no instalados")
class VideoRestoreFrameTests(unittest.TestCase):
    def test_text_detector_marks_small_subtitle(self) -> None:
        frame = np.full((360, 640, 3), 42, dtype=np.uint8)
        cv2.putText(
            frame,
            "SUBTITULO DE PRUEBA",
            (105, 315),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.82,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        detector = OverlayTextDetector(max_mask_fraction=0.10)

        mask = detector.detect(frame)

        coverage = float(np.count_nonzero(mask)) / float(mask.size)
        self.assertGreater(coverage, 0.0005)
        self.assertLess(coverage, 0.10)

    def test_color_restoration_reduces_a_warm_cast(self) -> None:
        frame = np.empty((180, 320, 3), dtype=np.uint8)
        frame[:, :] = (78, 145, 218)
        profile = RESTORATION_PROFILES["natural_hd"]
        restorer = TemporalNaturalColor(profile)

        restored = restorer.apply(frame)

        before = frame.reshape(-1, 3).mean(axis=0)
        after = restored.reshape(-1, 3).mean(axis=0)
        self.assertLess(float(np.ptp(after)), float(np.ptp(before)))


@unittest.skipIf(cv2 is None or np is None, "OpenCV/NumPy no instalados")
class VideoRestoreIntegrationTests(unittest.TestCase):
    def test_one_second_video_keeps_audio_and_dimensions(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/ffprobe no disponibles")

        with tempfile.TemporaryDirectory(prefix="medialab-restore-test-") as folder:
            root = Path(folder)
            source = root / "source.mp4"
            output = root / "restored.mp4"
            create = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0xC87855:s=320x240:r=10:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=44100:duration=1",
                    "-vf",
                    (
                        "drawtext=text=PRUEBA:"
                        "fontcolor=white:fontsize=24:x=(w-text_w)/2:y=h-45"
                    ),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)

            original_ffmpeg = VIDEO_RESTORER.ffmpeg
            original_ffprobe = VIDEO_RESTORER.ffprobe
            VIDEO_RESTORER.ffmpeg = ffmpeg
            VIDEO_RESTORER.ffprobe = ffprobe
            VIDEO_RESTORER._supports_nvenc.cache_clear()
            try:
                result = VIDEO_RESTORER.process(
                    source,
                    output,
                    preset="natural",
                )
            finally:
                VIDEO_RESTORER.ffmpeg = original_ffmpeg
                VIDEO_RESTORER.ffprobe = original_ffprobe
                VIDEO_RESTORER._supports_nvenc.cache_clear()

            self.assertTrue(result.success, result.error)
            self.assertTrue(output.is_file())
            self.assertEqual((result.width, result.height), (320, 240))
            self.assertGreater(result.frames_processed, 0)
            self.assertGreater(result.frames_with_text, 0)
            self.assertAlmostEqual(result.duration, 1.0, delta=0.20)
            self.assertTrue(result.audio_copied)

    def test_hd_plus_outputs_vertical_1080_without_audio(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/ffprobe no disponibles")

        with tempfile.TemporaryDirectory(prefix="medialab-hdplus-test-") as folder:
            root = Path(folder)
            source = root / "vertical.mp4"
            output = root / "vertical-hd.mp4"
            create = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x704020:s=180x320:r=5:d=0.6",
                    "-vf",
                    "drawtext=text=TEXTO:fontcolor=white:fontsize=18:x=50:y=h-35",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)

            original_ffmpeg = VIDEO_RESTORER.ffmpeg
            original_ffprobe = VIDEO_RESTORER.ffprobe
            VIDEO_RESTORER.ffmpeg = ffmpeg
            VIDEO_RESTORER.ffprobe = ffprobe
            VIDEO_RESTORER._supports_nvenc.cache_clear()
            try:
                result = VIDEO_RESTORER.process(
                    source,
                    output,
                    preset="natural_hd_plus",
                )
            finally:
                VIDEO_RESTORER.ffmpeg = original_ffmpeg
                VIDEO_RESTORER.ffprobe = original_ffprobe
                VIDEO_RESTORER._supports_nvenc.cache_clear()

            self.assertTrue(result.success, result.error)
            self.assertEqual((result.width, result.height), (1080, 1920))
            self.assertFalse(result.audio_copied)


if __name__ == "__main__":
    unittest.main()
