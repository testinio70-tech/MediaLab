from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"

from services.video_restore import (  # noqa: E402
    ConservativeBackgroundEnhancer,
    FaceIdentityProtector,
    GenerativeTextInpainter,
    RESTORATION_PROFILES,
    OverlayTextDetector,
    PersonProtectionSegmenter,
    RestorationProgress,
    TemporalNaturalColor,
    VIDEO_RESTORER,
    _parse_rate,
    _target_dimensions,
    cv2,
    np,
)
from config import RESTORE_INPAINT_MODEL, RESTORE_SUPERRES_MODEL  # noqa: E402


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

    def test_alpha5_exposes_two_primary_modes(self) -> None:
        self.assertEqual(
            RESTORATION_PROFILES["faithful"].label,
            "Restauración fiel",
        )
        self.assertTrue(
            RESTORATION_PROFILES["ai_hd"].ai_super_resolution
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

    def test_text_detector_never_masks_protected_person_pixels(self) -> None:
        frame = np.full((360, 640, 3), 42, dtype=np.uint8)
        cv2.putText(
            frame,
            "@IDENTIDAD_PROTEGIDA",
            (90, 190),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.82,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        protected = np.full((360, 640), 255, dtype=np.uint8)
        detector = OverlayTextDetector(max_mask_fraction=0.10)

        mask = detector.detect(frame, protected_mask=protected)

        self.assertEqual(int(np.count_nonzero(mask)), 0)

    def test_text_detector_includes_nearby_social_icon(self) -> None:
        frame = np.full((420, 640, 3), 48, dtype=np.uint8)
        cv2.rectangle(frame, (90, 270), (140, 320), (245, 245, 245), 3)
        cv2.circle(frame, (115, 295), 12, (245, 245, 245), 3)
        cv2.putText(
            frame,
            "@CUENTA_DE_PRUEBA",
            (90, 365),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        detector = OverlayTextDetector(max_mask_fraction=0.12)

        mask = detector.detect(frame)

        self.assertGreater(
            int(np.count_nonzero(mask[265:325, 85:145])),
            100,
        )

    def test_person_protection_model_returns_a_safe_mask(self) -> None:
        frame = np.full((240, 320, 3), 80, dtype=np.uint8)
        segmenter = PersonProtectionSegmenter(margin_fraction=0.015)

        mask = segmenter.detect(frame)

        self.assertEqual(mask.shape, frame.shape[:2])
        self.assertEqual(mask.dtype, np.uint8)

    def test_color_restoration_reduces_a_warm_cast(self) -> None:
        frame = np.empty((180, 320, 3), dtype=np.uint8)
        frame[:, :] = (78, 145, 218)
        profile = RESTORATION_PROFILES["natural_hd"]
        restorer = TemporalNaturalColor(profile)

        restored = restorer.apply(frame)

        before = frame.reshape(-1, 3).mean(axis=0)
        after = restored.reshape(-1, 3).mean(axis=0)
        self.assertLess(float(np.ptp(after)), float(np.ptp(before)))

    def test_color_restoration_reduces_extreme_filter_saturation(self) -> None:
        frame = np.full((240, 320, 3), (45, 210, 70), dtype=np.uint8)
        restorer = TemporalNaturalColor(
            RESTORATION_PROFILES["faithful"]
        )

        restored = restorer.apply(frame)

        before = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1]
        after = cv2.cvtColor(restored, cv2.COLOR_BGR2HSV)[:, :, 1]
        self.assertLess(
            float(np.percentile(after, 75)),
            float(np.percentile(before, 75)) * 0.80,
        )

    def test_background_enhancer_preserves_protected_pixels_exactly(self) -> None:
        random = np.random.default_rng(7)
        frame = random.integers(
            0,
            255,
            size=(120, 160, 3),
            dtype=np.uint8,
        )
        protected = np.zeros((120, 160), dtype=np.uint8)
        protected[20:100, 45:120] = 255
        enhancer = ConservativeBackgroundEnhancer(
            RESTORATION_PROFILES["natural_hd"]
        )

        enhanced = enhancer.apply(frame, protected_mask=protected)

        self.assertTrue(
            np.array_equal(
                enhanced[protected > 0],
                frame[protected > 0],
            )
        )

    def test_background_enhancer_does_not_amplify_noise(self) -> None:
        random = np.random.default_rng(3)
        frame = np.clip(
            120.0 + random.normal(0.0, 10.0, size=(180, 320, 3)),
            0,
            255,
        ).astype(np.uint8)
        enhancer = ConservativeBackgroundEnhancer(
            RESTORATION_PROFILES["natural_hd"]
        )

        enhanced = enhancer.apply(
            frame,
            protected_mask=np.zeros((180, 320), dtype=np.uint8),
        )

        self.assertLessEqual(
            float(enhanced.astype(np.float32).std()),
            float(frame.astype(np.float32).std()),
        )

    def test_face_protector_returns_frame_sized_mask(self) -> None:
        protector = FaceIdentityProtector()
        frame = np.full((180, 320, 3), 80, dtype=np.uint8)

        mask = protector.detect(frame)

        self.assertEqual(mask.shape, frame.shape[:2])
        self.assertEqual(mask.dtype, np.uint8)

    def test_face_text_uses_nongenerative_interpolation(self) -> None:
        frame = np.full((120, 160, 3), 80, dtype=np.uint8)
        frame[:, :, 1] = np.arange(160, dtype=np.uint8)
        text_mask = np.zeros((120, 160), dtype=np.uint8)
        text_mask[45:65, 68:92] = 255
        frame[text_mask > 0] = 255
        face_mask = np.zeros_like(text_mask)
        face_mask[25:95, 45:115] = 255
        protected_mask = face_mask.copy()
        inpainter = GenerativeTextInpainter.__new__(
            GenerativeTextInpainter
        )
        inpainter._session = object()

        restored = inpainter.apply(
            frame,
            text_mask,
            protected_mask=protected_mask,
            face_mask=face_mask,
        )

        self.assertTrue(
            np.array_equal(
                restored[text_mask == 0],
                frame[text_mask == 0],
            )
        )
        self.assertFalse(
            np.array_equal(
                restored[text_mask > 0],
                frame[text_mask > 0],
            )
        )


@unittest.skipIf(cv2 is None or np is None, "OpenCV/NumPy no instalados")
class VideoRestoreIntegrationTests(unittest.TestCase):
    def test_one_second_video_keeps_audio_and_dimensions(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/ffprobe no disponibles")
        if not RESTORE_INPAINT_MODEL.is_file():
            self.skipTest("Modelo LaMa local no instalado")

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
            progress: list[RestorationProgress] = []
            try:
                result = VIDEO_RESTORER.process(
                    source,
                    output,
                    preset="faithful",
                    progress_callback=progress.append,
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
            self.assertTrue(progress)
            self.assertEqual(progress[-1].percent, 100)

    def test_ai_hd_outputs_vertical_1080_without_audio(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/ffprobe no disponibles")
        if (
            not RESTORE_INPAINT_MODEL.is_file()
            or not RESTORE_SUPERRES_MODEL.is_file()
        ):
            self.skipTest("Modelos IA locales no instalados")

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
                    preset="ai_hd",
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
