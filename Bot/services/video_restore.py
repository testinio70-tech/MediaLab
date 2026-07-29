from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from config import (
    FFMPEG_BINARY,
    FFPROBE_BINARY,
    MAX_TELEGRAM_FILE_SIZE,
    RESTORE_FOLDER,
    RESTORE_MAX_DURATION_SECONDS,
    RESTORE_MAX_TEXT_MASK_PERCENT,
    RESTORE_PERSON_MODEL,
    RESTORE_PERSON_PROTECTION_PERCENT,
    RESTORE_PROCESS_TIMEOUT_SECONDS,
    RESTORE_TARGET_SIZE_BYTES,
    RESTORE_TEXT_CONFIDENCE,
    RESTORE_TEXT_MODEL,
)

try:
    import cv2
    import numpy as np
except ImportError:  # La interfaz del bot debe iniciar aunque falte el extra.
    cv2 = None
    np = None


logger = logging.getLogger(__name__)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")
_SUPPORTED_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
_MP4_COPY_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "alac"}


@dataclass(slots=True, frozen=True)
class RestorationProfile:
    key: str
    label: str
    color_strength: float
    detail_strength: float
    upscale_to_1080: bool


RESTORATION_PROFILES = {
    "natural": RestorationProfile(
        key="natural",
        label="Natural",
        color_strength=0.62,
        detail_strength=0.0,
        upscale_to_1080=False,
    ),
    "natural_hd": RestorationProfile(
        key="natural_hd",
        label="Natural HD",
        color_strength=0.78,
        detail_strength=0.22,
        upscale_to_1080=False,
    ),
    "natural_hd_plus": RestorationProfile(
        key="natural_hd_plus",
        label="Natural HD+",
        color_strength=0.78,
        detail_strength=0.28,
        upscale_to_1080=True,
    ),
}


@dataclass(slots=True, frozen=True)
class VideoProbe:
    width: int
    height: int
    duration: float
    fps: float
    audio_codec: str = ""

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)


@dataclass(slots=True)
class RestorationResult:
    success: bool
    message: str
    output_path: Path | None = None
    file_size: int = 0
    width: int = 0
    height: int = 0
    duration: float = 0.0
    encoder: str = ""
    preset: str = ""
    frames_processed: int = 0
    frames_with_text: int = 0
    audio_copied: bool = False
    error: str = ""


@dataclass(slots=True, frozen=True)
class RestorationProgress:
    percent: int
    stage: str
    frames_processed: int = 0
    total_frames: int = 0


class PersonProtectionSegmenter:
    """Protege persona, cabello y extremidades antes de reconstruir píxeles."""

    def __init__(
        self,
        *,
        model_path: Path = RESTORE_PERSON_MODEL,
        margin_fraction: float,
    ) -> None:
        if cv2 is None or np is None:
            raise RuntimeError("OpenCV y NumPy no están disponibles.")
        if not model_path.is_file():
            raise RuntimeError(
                f"No se encontró el modelo de protección humana: {model_path.name}"
            )
        self._network = cv2.dnn.readNet(str(model_path))
        self._network.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self._network.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.margin_fraction = min(0.05, max(0.005, margin_fraction))
        self._previous_raw_mask: Any | None = None
        self._previous_histogram: Any | None = None

    def detect(self, frame: Any) -> Any:
        height, width = frame.shape[:2]
        scene_changed = self._scene_changed(frame)

        resized = cv2.resize(frame, (192, 192), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
        normalized = (rgb / 255.0 - 0.5) / 0.5
        blob = cv2.dnn.blobFromImage(normalized)
        self._network.setInput(blob)
        output = self._network.forward()
        if output.ndim != 4 or output.shape[1] < 2:
            raise RuntimeError("El modelo humano devolvió una salida inválida.")

        raw_mask = (
            np.argmax(output[0], axis=0).astype(np.uint8) * 255
        )
        raw_mask = cv2.resize(
            raw_mask,
            (width, height),
            interpolation=cv2.INTER_NEAREST,
        )
        raw_mask = cv2.morphologyEx(
            raw_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            iterations=1,
        )
        contours, _ = cv2.findContours(
            raw_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        minimum_component_area = float(width * height) * 0.003
        for contour in contours:
            if cv2.contourArea(contour) < minimum_component_area:
                continue
            cv2.fillConvexPoly(
                raw_mask,
                cv2.convexHull(contour),
                255,
            )

        protected = raw_mask
        if (
            not scene_changed
            and self._previous_raw_mask is not None
            and self._previous_raw_mask.shape == raw_mask.shape
        ):
            protected = cv2.bitwise_or(protected, self._previous_raw_mask)
        self._previous_raw_mask = raw_mask

        radius = max(
            3,
            int(round(min(width, height) * self.margin_fraction)),
        )
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (radius * 2 + 1, radius * 2 + 1),
        )
        return cv2.dilate(protected, kernel, iterations=1)

    def _scene_changed(self, frame: Any) -> bool:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scale = min(1.0, 240.0 / max(gray.shape))
        if scale < 1.0:
            gray = cv2.resize(
                gray,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
        histogram = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(histogram, histogram)
        changed = False
        if self._previous_histogram is not None:
            changed = (
                cv2.compareHist(
                    self._previous_histogram,
                    histogram,
                    cv2.HISTCMP_BHATTACHARYYA,
                )
                > 0.58
            )
        self._previous_histogram = histogram
        return changed


class OverlayTextDetector:
    """Detector PP-OCR limitado a regiones confirmadas y fuera de personas."""

    def __init__(
        self,
        *,
        max_mask_fraction: float,
        model_path: Path = RESTORE_TEXT_MODEL,
        confidence_threshold: float = RESTORE_TEXT_CONFIDENCE,
    ) -> None:
        if cv2 is None or np is None:
            raise RuntimeError("OpenCV y NumPy no están disponibles.")
        if not model_path.is_file():
            raise RuntimeError(
                f"No se encontró el modelo de texto: {model_path.name}"
            )
        self.max_mask_fraction = min(0.25, max(0.01, max_mask_fraction))
        self.confidence_threshold = min(
            0.98,
            max(0.30, confidence_threshold),
        )
        network = cv2.dnn.readNet(str(model_path))
        network.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        network.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._model = cv2.dnn_TextDetectionModel_DB(network)
        self._model.setInputParams(
            scale=1.0 / 255.0,
            size=(736, 736),
            mean=(123.675, 116.28, 103.53),
            swapRB=True,
        )
        self._model.setBinaryThreshold(0.30)
        self._model.setPolygonThreshold(0.50)
        self._model.setUnclipRatio(1.45)
        self._model.setMaxCandidates(100)
        self._previous_regions: Any | None = None

    def detect(
        self,
        frame: Any,
        *,
        protected_mask: Any | None = None,
    ) -> Any:
        height, width = frame.shape[:2]
        strong_regions = np.zeros((height, width), dtype=np.uint8)
        weak_regions = np.zeros_like(strong_regions)
        frame_area = float(width * height)

        boxes, scores = self._model.detect(frame)
        strong_threshold = min(
            0.96,
            max(0.82, self.confidence_threshold + 0.14),
        )
        for box, raw_score in zip(boxes, scores):
            score = float(np.asarray(raw_score).reshape(-1)[0])
            if score < self.confidence_threshold:
                continue
            polygon = np.asarray(box, dtype=np.int32).reshape(-1, 2)
            polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
            polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
            area = abs(float(cv2.contourArea(polygon)))
            if area < 12.0 or area > frame_area * self.max_mask_fraction:
                continue
            target = strong_regions if score >= strong_threshold else weak_regions
            cv2.fillPoly(target, [polygon], 255)

        accepted_regions = strong_regions
        if self._previous_regions is not None and np.count_nonzero(weak_regions):
            support_radius = max(3, int(round(min(width, height) * 0.006)))
            support = cv2.dilate(
                self._previous_regions,
                cv2.getStructuringElement(
                    cv2.MORPH_ELLIPSE,
                    (support_radius * 2 + 1, support_radius * 2 + 1),
                ),
                iterations=1,
            )
            accepted_regions = cv2.bitwise_or(
                accepted_regions,
                cv2.bitwise_and(weak_regions, support),
            )
        current_regions = cv2.bitwise_or(strong_regions, weak_regions)
        self._previous_regions = current_regions

        mask = self._refine_glyph_mask(frame, accepted_regions)
        if protected_mask is not None:
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(protected_mask))

        masked_fraction = float(np.count_nonzero(mask)) / frame_area
        safe_limit = self.max_mask_fraction
        if protected_mask is not None:
            protected_fraction = (
                float(np.count_nonzero(protected_mask)) / frame_area
            )
            safe_limit = min(
                0.20,
                max(
                    safe_limit,
                    (1.0 - protected_fraction) * 0.20,
                ),
            )
        if masked_fraction > safe_limit:
            logger.warning(
                (
                    "Máscara DNN descartada por seguridad: %.2f%% "
                    "(límite %.2f%%)."
                ),
                masked_fraction * 100,
                safe_limit * 100,
            )
            return np.zeros((height, width), dtype=np.uint8)
        return mask

    def _refine_glyph_mask(self, frame: Any, regions: Any) -> Any:
        if not np.count_nonzero(regions):
            return regions

        height, width = frame.shape[:2]
        kernel_size = max(5, int(round(min(width, height) / 120)))
        if kernel_size % 2 == 0:
            kernel_size += 1
        kernel_size = min(kernel_size, 15)
        background = cv2.medianBlur(frame, kernel_size)
        difference = cv2.absdiff(frame, background)
        difference = np.max(difference, axis=2).astype(np.uint8)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gradient_x = cv2.convertScaleAbs(
            cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
        )
        gradient_y = cv2.convertScaleAbs(
            cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
        )
        contrast = cv2.max(
            difference,
            cv2.addWeighted(gradient_x, 0.5, gradient_y, 0.5, 0),
        )
        candidate = np.where(contrast >= 9, 255, 0).astype(np.uint8)
        candidate = cv2.bitwise_and(candidate, regions)
        candidate = cv2.morphologyEx(
            candidate,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        dilation = max(1, int(round(min(width, height) / 540)))
        candidate = cv2.dilate(
            candidate,
            cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (dilation * 2 + 1, dilation * 2 + 1),
            ),
            iterations=1,
        )
        return cv2.bitwise_and(candidate, regions)


class TemporalNaturalColor:
    """Balance de blancos y saturación suavizados para evitar parpadeos."""

    def __init__(self, profile: RestorationProfile) -> None:
        self.profile = profile
        self._gains: Any | None = None
        self._previous_histogram: Any | None = None

    def apply(self, frame: Any) -> Any:
        if cv2 is None or np is None:
            raise RuntimeError("OpenCV y NumPy no están disponibles.")

        scene_changed = self._scene_changed(frame)
        estimated = self._estimate_gains(frame)
        desired = 1.0 + (estimated - 1.0) * self.profile.color_strength

        if self._gains is None or scene_changed:
            self._gains = desired
        else:
            self._gains = self._gains * 0.92 + desired * 0.08

        corrected = frame.astype(np.float32)
        corrected *= self._gains.reshape(1, 1, 3)
        corrected = np.clip(corrected, 0, 255).astype(np.uint8)
        corrected = self._reduce_excess_saturation(corrected)

        if self.profile.detail_strength > 0:
            corrected = self._local_contrast(corrected)
        return corrected

    def _estimate_gains(self, frame: Any) -> Any:
        height, width = frame.shape[:2]
        scale = min(1.0, 320.0 / max(width, height, 1))
        sample = (
            cv2.resize(
                frame,
                (max(2, int(width * scale)), max(2, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            if scale < 1.0
            else frame
        )

        hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        value = hsv[:, :, 2]
        neutral = (saturation < 92) & (value > 36) & (value < 246)
        pixels = sample[neutral]

        minimum_pixels = max(64, int(sample.shape[0] * sample.shape[1] * 0.01))
        if len(pixels) < minimum_pixels:
            middle = (value > 36) & (value < 246)
            pixels = sample[middle]
        if len(pixels) < minimum_pixels:
            return np.ones(3, dtype=np.float32)

        channel_means = np.mean(pixels.astype(np.float32), axis=0)
        channel_means = np.maximum(channel_means, 1.0)
        target = float(np.mean(channel_means))
        gains = target / channel_means
        gains /= max(float(np.mean(gains)), 0.001)
        return np.clip(gains, 0.78, 1.24).astype(np.float32)

    def _reduce_excess_saturation(self, frame: Any) -> Any:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1].astype(np.float32)
        percentile = float(np.percentile(saturation, 75))
        excess = max(0.0, percentile - 105.0)
        reduction = min(0.18, excess / 420.0) * self.profile.color_strength
        if reduction <= 0:
            return frame
        saturation *= 1.0 - reduction
        hsv[:, :, 1] = np.clip(saturation, 0, 255).astype(np.uint8)
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    def _local_contrast(self, frame: Any) -> Any:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        luminance, channel_a, channel_b = cv2.split(lab)
        values = np.linspace(0.0, 1.0, 256, dtype=np.float32)
        amount = self.profile.detail_strength * 0.16
        curve = values + amount * (2.0 * values - 1.0) * (
            4.0 * values * (1.0 - values)
        )
        lookup = np.clip(curve * 255.0, 0, 255).astype(np.uint8)
        luminance = cv2.LUT(luminance, lookup)
        return cv2.cvtColor(
            cv2.merge((luminance, channel_a, channel_b)),
            cv2.COLOR_LAB2BGR,
        )

    def _scene_changed(self, frame: Any) -> bool:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        histogram = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(histogram, histogram)
        changed = False
        if self._previous_histogram is not None:
            distance = cv2.compareHist(
                self._previous_histogram,
                histogram,
                cv2.HISTCMP_BHATTACHARYYA,
            )
            changed = distance > 0.58
        self._previous_histogram = histogram
        return changed


class ConservativeBackgroundEnhancer:
    """Reduce ruido y recupera microdetalle solo fuera de la silueta humana."""

    def __init__(self, profile: RestorationProfile) -> None:
        self.profile = profile

    def apply(
        self,
        frame: Any,
        *,
        protected_mask: Any | None,
    ) -> Any:
        if self.profile.detail_strength <= 0:
            return frame

        denoised = cv2.bilateralFilter(
            frame,
            d=5,
            sigmaColor=14.0,
            sigmaSpace=4.0,
        )
        smooth = cv2.GaussianBlur(denoised, (0, 0), sigmaX=0.75)
        detail_amount = 0.08 + self.profile.detail_strength * 0.20
        detailed = cv2.addWeighted(
            denoised,
            1.0 + detail_amount,
            smooth,
            -detail_amount,
            0,
        )

        blend_strength = min(
            0.34,
            0.18 + self.profile.detail_strength * 0.45,
        )
        if protected_mask is None:
            return cv2.addWeighted(
                frame,
                1.0 - blend_strength,
                detailed,
                blend_strength,
                0,
            )

        background_alpha = (
            cv2.bitwise_not(protected_mask).astype(np.float32)
            / 255.0
            * blend_strength
        )
        background_alpha = cv2.GaussianBlur(
            background_alpha,
            (0, 0),
            sigmaX=1.2,
        )
        background_alpha[protected_mask > 0] = 0.0
        background_alpha = background_alpha[:, :, None]
        output = (
            frame.astype(np.float32) * (1.0 - background_alpha)
            + detailed.astype(np.float32) * background_alpha
        )
        return np.clip(output, 0, 255).astype(np.uint8)


class VideoRestorer:
    def __init__(self) -> None:
        self.ffmpeg = FFMPEG_BINARY
        self.ffprobe = FFPROBE_BINARY

    def availability_text(self) -> str:
        missing: list[str] = []
        if not _binary_exists(self.ffmpeg):
            missing.append("ffmpeg")
        if not _binary_exists(self.ffprobe):
            missing.append("ffprobe")
        if cv2 is None:
            missing.append("opencv-python-headless")
        if np is None:
            missing.append("numpy")
        if cv2 is not None and not hasattr(cv2, "dnn_TextDetectionModel_DB"):
            missing.append("OpenCV DNN TextDetectionModel_DB")
        if not RESTORE_TEXT_MODEL.is_file():
            missing.append(RESTORE_TEXT_MODEL.name)
        if not RESTORE_PERSON_MODEL.is_file():
            missing.append(RESTORE_PERSON_MODEL.name)
        return "disponible" if not missing else "faltan " + ", ".join(missing)

    def create_paths(
        self,
        *,
        user_id: int,
        file_unique_id: str,
        original_name: str,
    ) -> tuple[Path, Path]:
        safe_id = _SAFE_TOKEN.sub("_", file_unique_id).strip("._") or "video"
        workspace = RESTORE_FOLDER / str(user_id) / safe_id
        workspace.mkdir(parents=True, exist_ok=True)

        suffix = Path(original_name).suffix.lower()
        if suffix not in _SUPPORTED_SUFFIXES:
            suffix = ".mp4"
        return workspace / f"input{suffix}", workspace / "restored.mp4"

    async def process_async(
        self,
        input_path: Path,
        output_path: Path,
        *,
        preset: str,
        progress_callback: (
            Callable[[RestorationProgress], None] | None
        ) = None,
    ) -> RestorationResult:
        return await asyncio.to_thread(
            self.process,
            input_path,
            output_path,
            preset=preset,
            progress_callback=progress_callback,
        )

    def process(
        self,
        input_path: Path,
        output_path: Path,
        *,
        preset: str,
        progress_callback: (
            Callable[[RestorationProgress], None] | None
        ) = None,
    ) -> RestorationResult:
        _emit_progress(
            progress_callback,
            0,
            "Preparando el análisis protegido",
        )
        profile = RESTORATION_PROFILES.get(preset)
        if profile is None:
            return RestorationResult(
                False,
                "El modo de restauración no es válido.",
                error=f"Preset desconocido: {preset}",
            )

        availability = self.availability_text()
        if availability != "disponible":
            return RestorationResult(
                False,
                "Faltan componentes para la restauración integral.",
                preset=profile.label,
                error=availability,
            )
        if not input_path.is_file():
            return RestorationResult(
                False,
                "No se encontró el video de entrada.",
                preset=profile.label,
                error=str(input_path),
            )

        try:
            probe = self._probe(input_path)
        except Exception as error:
            logger.exception("ffprobe no pudo analizar el video para restauración.")
            return RestorationResult(
                False,
                "No se pudo analizar el video recibido.",
                preset=profile.label,
                error=str(error),
            )

        if probe.duration <= 0:
            return RestorationResult(
                False,
                "El video no contiene una duración válida.",
                preset=profile.label,
            )
        if probe.duration > RESTORE_MAX_DURATION_SECONDS:
            return RestorationResult(
                False,
                "El video supera la duración máxima de esta versión de prueba.",
                duration=probe.duration,
                preset=profile.label,
                error=(
                    f"Duración {probe.duration:.2f}s; máximo "
                    f"{RESTORE_MAX_DURATION_SECONDS}s"
                ),
            )

        target_width, target_height = _target_dimensions(
            probe.width,
            probe.height,
            upscale_to_1080=profile.upscale_to_1080,
        )
        bitrate = _calculate_video_bitrate(probe.duration)
        encoders = ["libx264"]
        last_error = ""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        for encoder in encoders:
            output_path.unlink(missing_ok=True)
            try:
                frames_processed, frames_with_text, audio_copied = (
                    self._process_attempt(
                        input_path=input_path,
                        output_path=output_path,
                        probe=probe,
                        profile=profile,
                        target_width=target_width,
                        target_height=target_height,
                        encoder=encoder,
                        video_bitrate=bitrate,
                        progress_callback=progress_callback,
                    )
                )
            except Exception as error:
                last_error = str(error)
                logger.exception(
                    "Restauración falló con el codificador %s.",
                    encoder,
                )
                continue

            if not output_path.is_file() or output_path.stat().st_size <= 0:
                last_error = "FFmpeg terminó sin crear una salida válida."
                continue

            file_size = output_path.stat().st_size
            if file_size > MAX_TELEGRAM_FILE_SIZE:
                last_error = (
                    "La salida superó el límite de Telegram: "
                    f"{file_size} bytes."
                )
                continue

            try:
                _emit_progress(
                    progress_callback,
                    98,
                    "Verificando video y audio",
                    frames_processed=frames_processed,
                    total_frames=frames_processed,
                )
                output_probe = self._probe(output_path)
            except Exception as error:
                last_error = str(error)
                continue

            encoder_name = (
                "OpenCV + FFmpeg · NVIDIA NVENC"
                if encoder == "h264_nvenc"
                else "OpenCV + FFmpeg · libx264"
            )
            result = RestorationResult(
                True,
                "Video restaurado correctamente.",
                output_path=output_path,
                file_size=file_size,
                width=output_probe.width,
                height=output_probe.height,
                duration=output_probe.duration,
                encoder=encoder_name,
                preset=profile.label,
                frames_processed=frames_processed,
                frames_with_text=frames_with_text,
                audio_copied=audio_copied,
            )
            _emit_progress(
                progress_callback,
                100,
                "Restauración terminada",
                frames_processed=frames_processed,
                total_frames=frames_processed,
            )
            return result

        output_path.unlink(missing_ok=True)
        return RestorationResult(
            False,
            "No se pudo generar una restauración compatible con Telegram.",
            duration=probe.duration,
            preset=profile.label,
            error=last_error,
        )

    def _process_attempt(
        self,
        *,
        input_path: Path,
        output_path: Path,
        probe: VideoProbe,
        profile: RestorationProfile,
        target_width: int,
        target_height: int,
        encoder: str,
        video_bitrate: int,
        progress_callback: (
            Callable[[RestorationProgress], None] | None
        ),
    ) -> tuple[int, int, bool]:
        capture = cv2.VideoCapture(str(input_path))
        if not capture.isOpened():
            raise RuntimeError("OpenCV no pudo abrir el video.")

        capture_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        capture_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if capture_width > 0 and capture_height > 0:
            target_width, target_height = _target_dimensions(
                capture_width,
                capture_height,
                upscale_to_1080=profile.upscale_to_1080,
            )

        text_detector = OverlayTextDetector(
            max_mask_fraction=RESTORE_MAX_TEXT_MASK_PERCENT / 100.0,
        )
        person_protector = PersonProtectionSegmenter(
            margin_fraction=RESTORE_PERSON_PROTECTION_PERCENT / 100.0,
        )
        color_restorer = TemporalNaturalColor(profile)
        quality_enhancer = ConservativeBackgroundEnhancer(profile)
        audio_copied = (
            probe.has_audio and probe.audio_codec in _MP4_COPY_AUDIO_CODECS
        )
        command = self._build_encode_command(
            input_path=input_path,
            output_path=output_path,
            probe=probe,
            profile=profile,
            target_width=target_width,
            target_height=target_height,
            encoder=encoder,
            video_bitrate=video_bitrate,
            audio_copied=audio_copied,
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        frames_processed = 0
        frames_with_text = 0
        total_frames = max(1, int(round(probe.duration * probe.fps)))
        last_reported_percent = -1
        started_at = monotonic()

        try:
            if process.stdin is None:
                raise RuntimeError("FFmpeg no abrió la entrada de fotogramas.")

            while True:
                if monotonic() - started_at > RESTORE_PROCESS_TIMEOUT_SECONDS:
                    raise TimeoutError("Tiempo de restauración agotado.")

                ok, frame = capture.read()
                if not ok:
                    break

                protected_mask = person_protector.detect(frame)
                mask = text_detector.detect(
                    frame,
                    protected_mask=protected_mask,
                )
                if np.count_nonzero(mask):
                    frame = cv2.inpaint(
                        frame,
                        mask,
                        inpaintRadius=2.0,
                        flags=cv2.INPAINT_NS,
                    )
                    frames_with_text += 1

                frame = color_restorer.apply(frame)
                frame = quality_enhancer.apply(
                    frame,
                    protected_mask=protected_mask,
                )
                if frame.shape[1] != target_width or frame.shape[0] != target_height:
                    interpolation = (
                        cv2.INTER_LANCZOS4
                        if target_width > frame.shape[1]
                        or target_height > frame.shape[0]
                        else cv2.INTER_AREA
                    )
                    frame = cv2.resize(
                        frame,
                        (target_width, target_height),
                        interpolation=interpolation,
                    )

                try:
                    process.stdin.write(frame.tobytes())
                except BrokenPipeError as error:
                    raise RuntimeError(
                        "FFmpeg cerró el codificador antes de tiempo."
                    ) from error
                frames_processed += 1
                percent = min(
                    94,
                    4 + int((frames_processed / total_frames) * 90),
                )
                report_percent = percent - (percent % 5)
                if report_percent > last_reported_percent:
                    _emit_progress(
                        progress_callback,
                        report_percent,
                        (
                            "Protegiendo personas, detectando texto y "
                            "recuperando detalle"
                        ),
                        frames_processed=frames_processed,
                        total_frames=total_frames,
                    )
                    last_reported_percent = report_percent

            _emit_progress(
                progress_callback,
                95,
                "Codificando con calidad alta",
                frames_processed=frames_processed,
                total_frames=total_frames,
            )
            process.stdin.close()
            stderr_bytes = process.stderr.read() if process.stderr else b""
            try:
                returncode = process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                raise TimeoutError("FFmpeg no terminó la codificación.")

            stderr = stderr_bytes.decode("utf-8", errors="replace")
            if returncode != 0:
                raise RuntimeError(_tail(stderr) or "FFmpeg falló.")
            if frames_processed <= 0:
                raise RuntimeError("El video no produjo fotogramas válidos.")
            return frames_processed, frames_with_text, audio_copied
        finally:
            capture.release()
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if process.stderr is not None:
                process.stderr.close()

    def _build_encode_command(
        self,
        *,
        input_path: Path,
        output_path: Path,
        probe: VideoProbe,
        profile: RestorationProfile,
        target_width: int,
        target_height: int,
        encoder: str,
        video_bitrate: int,
        audio_copied: bool,
    ) -> list[str]:
        maxrate = int(video_bitrate * 1.12)
        bufsize = int(video_bitrate * 2)
        command = [
            self.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{target_width}x{target_height}",
            "-framerate",
            f"{probe.fps:.6f}",
            "-i",
            "pipe:0",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
        ]

        if probe.has_audio:
            command.extend(["-map", "1:a:0?"])
        command.extend(["-sn", "-dn", "-map_metadata", "-1"])

        if encoder == "h264_nvenc":
            command.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p4",
                    "-tune",
                    "hq",
                    "-rc",
                    "vbr",
                    "-b:v",
                    str(video_bitrate),
                    "-maxrate",
                    str(maxrate),
                    "-bufsize",
                    str(bufsize),
                ]
            )
        else:
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "slow",
                    "-profile:v",
                    "high",
                    "-b:v",
                    str(video_bitrate),
                    "-maxrate",
                    str(maxrate),
                    "-bufsize",
                    str(bufsize),
                ]
            )

        command.extend(["-pix_fmt", "yuv420p"])
        if probe.has_audio:
            if audio_copied:
                command.extend(["-c:a", "copy"])
            else:
                command.extend(["-c:a", "aac", "-b:a", "128k"])
        else:
            command.append("-an")

        command.extend(
            [
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    def _probe(self, path: Path) -> VideoProbe:
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=codec_type,codec_name,width,height,duration,"
                "avg_frame_rate,r_frame_rate"
            ),
            "-of",
            "json",
            str(path),
        ]
        completed = _run_process(command, timeout=60)
        if completed.returncode != 0:
            raise RuntimeError(_tail(completed.stderr) or "ffprobe falló.")

        payload: dict[str, Any] = json.loads(completed.stdout or "{}")
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise RuntimeError("ffprobe no devolvió streams.")

        video_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict)
                and stream.get("codec_type") == "video"
            ),
            None,
        )
        if not isinstance(video_stream, dict):
            raise RuntimeError("No se encontró una pista de video.")

        audio_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict)
                and stream.get("codec_type") == "audio"
            ),
            None,
        )
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("La resolución del video no es válida.")

        format_data = payload.get("format")
        duration_value = (
            format_data.get("duration")
            if isinstance(format_data, dict)
            else None
        )
        if duration_value is None:
            duration_value = video_stream.get("duration")
        duration = float(duration_value or 0)

        fps = _parse_rate(str(video_stream.get("avg_frame_rate") or ""))
        if fps <= 0:
            fps = _parse_rate(str(video_stream.get("r_frame_rate") or ""))
        if fps <= 0:
            fps = 30.0

        audio_codec = (
            str(audio_stream.get("codec_name") or "").lower()
            if isinstance(audio_stream, dict)
            else ""
        )
        return VideoProbe(
            width=width,
            height=height,
            duration=duration,
            fps=min(120.0, max(1.0, fps)),
            audio_codec=audio_codec,
        )

    @lru_cache(maxsize=1)
    def _supports_nvenc(self) -> bool:
        completed = _run_process(
            [self.ffmpeg, "-hide_banner", "-encoders"],
            timeout=60,
        )
        if completed.returncode != 0 or "h264_nvenc" not in completed.stdout:
            return False

        smoke_test = _run_process(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x64:r=1:d=1",
                "-frames:v",
                "1",
                "-an",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            timeout=30,
        )
        if smoke_test.returncode != 0:
            logger.info(
                "NVENC anunciado pero no operativo; se usará libx264: %s",
                _tail(smoke_test.stderr, limit=500),
            )
            return False
        return True


def _emit_progress(
    callback: Callable[[RestorationProgress], None] | None,
    percent: int,
    stage: str,
    *,
    frames_processed: int = 0,
    total_frames: int = 0,
) -> None:
    if callback is None:
        return
    try:
        callback(
            RestorationProgress(
                percent=min(100, max(0, int(percent))),
                stage=stage,
                frames_processed=max(0, int(frames_processed)),
                total_frames=max(0, int(total_frames)),
            )
        )
    except Exception:
        logger.debug(
            "El receptor de progreso de restauración falló.",
            exc_info=True,
        )


def _target_dimensions(
    width: int,
    height: int,
    *,
    upscale_to_1080: bool,
) -> tuple[int, int]:
    width = max(2, width)
    height = max(2, height)

    long_edge_limit = 1920
    short_edge_limit = 1080
    long_edge = max(width, height)
    short_edge = min(width, height)
    cap_scale = min(
        1.0,
        long_edge_limit / long_edge,
        short_edge_limit / short_edge,
    )
    if cap_scale < 1.0:
        return (
            _even(int(round(width * cap_scale))),
            _even(int(round(height * cap_scale))),
        )
    if not upscale_to_1080:
        return _even(width), _even(height)

    scale = min(
        long_edge_limit / long_edge,
        short_edge_limit / short_edge,
    )
    return _even(int(round(width * scale))), _even(int(round(height * scale)))


def _even(value: int) -> int:
    value = max(2, value)
    return value if value % 2 == 0 else value - 1


def _calculate_video_bitrate(duration: float) -> int:
    usable_bits = int(RESTORE_TARGET_SIZE_BYTES * 8 * 0.93)
    reserved_audio_bitrate = 192_000
    calculated = int(usable_bits / max(duration, 1.0)) - reserved_audio_bitrate
    return min(10_000_000, max(900_000, calculated))


def _parse_rate(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return 0.0

    numerator, denominator = value.split("/", 1)
    try:
        divisor = float(denominator)
        if divisor == 0:
            return 0.0
        return float(numerator) / divisor
    except ValueError:
        return 0.0


def _binary_exists(binary: str) -> bool:
    candidate = Path(binary)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.is_file()
    return shutil.which(binary) is not None


def _run_process(
    command: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(
            command,
            returncode=124,
            stdout=stdout,
            stderr=f"{stderr}\nTiempo de procesamiento agotado.",
        )


def _tail(value: str, limit: int = 1800) -> str:
    normalized = (value or "").strip()
    return normalized[-limit:]


VIDEO_RESTORER = VideoRestorer()
