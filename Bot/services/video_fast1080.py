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
from typing import Any

from config import (
    FAST1080_FOLDER,
    FAST1080_MAX_DURATION_SECONDS,
    FAST1080_PROCESS_TIMEOUT_SECONDS,
    FAST1080_TARGET_SIZE_BYTES,
    FFMPEG_BINARY,
    FFPROBE_BINARY,
    MAX_TELEGRAM_FILE_SIZE,
)


logger = logging.getLogger(__name__)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class Fast1080Result:
    success: bool
    message: str
    output_path: Path | None = None
    file_size: int = 0
    width: int = 0
    height: int = 0
    duration: float = 0.0
    encoder: str = ""
    error: str = ""


@dataclass(slots=True, frozen=True)
class VideoProbe:
    width: int
    height: int
    duration: float


class Fast1080Enhancer:
    def __init__(self) -> None:
        self.ffmpeg = FFMPEG_BINARY
        self.ffprobe = FFPROBE_BINARY

    def availability_text(self) -> str:
        ffmpeg_ok = _binary_exists(self.ffmpeg)
        ffprobe_ok = _binary_exists(self.ffprobe)
        if ffmpeg_ok and ffprobe_ok:
            return "disponible"
        missing = []
        if not ffmpeg_ok:
            missing.append("ffmpeg")
        if not ffprobe_ok:
            missing.append("ffprobe")
        return "faltan " + ", ".join(missing)

    def create_paths(
        self,
        *,
        user_id: int,
        file_unique_id: str,
        original_name: str,
    ) -> tuple[Path, Path]:
        safe_id = _SAFE_TOKEN.sub("_", file_unique_id).strip("._") or "video"
        workspace = FAST1080_FOLDER / str(user_id) / safe_id
        workspace.mkdir(parents=True, exist_ok=True)

        suffix = Path(original_name).suffix.lower()
        if suffix not in {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}:
            suffix = ".mp4"

        return workspace / f"input{suffix}", workspace / "fast1080.mp4"

    async def process_async(
        self,
        input_path: Path,
        output_path: Path,
    ) -> Fast1080Result:
        return await asyncio.to_thread(self.process, input_path, output_path)

    def process(
        self,
        input_path: Path,
        output_path: Path,
    ) -> Fast1080Result:
        if not _binary_exists(self.ffmpeg):
            return Fast1080Result(
                False,
                "No se encontró FFmpeg.",
                error=f"Binario no encontrado: {self.ffmpeg}",
            )
        if not _binary_exists(self.ffprobe):
            return Fast1080Result(
                False,
                "No se encontró ffprobe.",
                error=f"Binario no encontrado: {self.ffprobe}",
            )
        if not input_path.is_file():
            return Fast1080Result(
                False,
                "No se encontró el video de entrada.",
                error=str(input_path),
            )

        try:
            probe = self._probe(input_path)
        except Exception as error:
            logger.exception("ffprobe no pudo leer el video.")
            return Fast1080Result(
                False,
                "No se pudo analizar el video recibido.",
                error=str(error),
            )

        if probe.duration <= 0:
            return Fast1080Result(
                False,
                "El video no contiene una duración válida.",
            )
        if probe.duration > FAST1080_MAX_DURATION_SECONDS:
            return Fast1080Result(
                False,
                "El video supera la duración máxima de Super rápido 1080.",
                duration=probe.duration,
                error=(
                    f"Duración {probe.duration:.2f}s; máximo "
                    f"{FAST1080_MAX_DURATION_SECONDS}s"
                ),
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)

        target_width, target_height = (
            (1920, 1080)
            if probe.width >= probe.height
            else (1080, 1920)
        )
        video_filter = (
            f"scale={target_width}:{target_height}:"
            "force_original_aspect_ratio=decrease:flags=lanczos,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        )

        base_bitrate = _calculate_video_bitrate(probe.duration)
        encoder_candidates = (
            ["h264_nvenc", "libx264"]
            if self._supports_nvenc()
            else ["libx264"]
        )
        last_error = ""

        for size_factor in (1.0, 0.74):
            bitrate = max(700_000, int(base_bitrate * size_factor))

            for encoder in encoder_candidates:
                output_path.unlink(missing_ok=True)
                command = self._build_command(
                    input_path=input_path,
                    output_path=output_path,
                    video_filter=video_filter,
                    encoder=encoder,
                    video_bitrate=bitrate,
                )
                completed = _run_process(
                    command,
                    timeout=FAST1080_PROCESS_TIMEOUT_SECONDS,
                )
                if completed.returncode != 0:
                    last_error = _tail(completed.stderr)
                    logger.warning(
                        "FFmpeg falló con %s: %s",
                        encoder,
                        last_error,
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
                    logger.warning(last_error)
                    break

                try:
                    output_probe = self._probe(output_path)
                except Exception as error:
                    last_error = str(error)
                    continue

                encoder_name = (
                    "FFmpeg · NVIDIA NVENC"
                    if encoder == "h264_nvenc"
                    else "FFmpeg · libx264"
                )
                return Fast1080Result(
                    True,
                    "Video escalado correctamente a un máximo de 1080p.",
                    output_path=output_path,
                    file_size=file_size,
                    width=output_probe.width,
                    height=output_probe.height,
                    duration=output_probe.duration,
                    encoder=encoder_name,
                )

        output_path.unlink(missing_ok=True)
        return Fast1080Result(
            False,
            "FFmpeg no pudo generar una salida compatible con Telegram.",
            duration=probe.duration,
            error=last_error,
        )

    def _build_command(
        self,
        *,
        input_path: Path,
        output_path: Path,
        video_filter: str,
        encoder: str,
        video_bitrate: int,
    ) -> list[str]:
        maxrate = int(video_bitrate * 1.12)
        bufsize = int(video_bitrate * 2)
        common = [
            self.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            video_filter,
            "-sn",
            "-map_metadata",
            "-1",
        ]

        if encoder == "h264_nvenc":
            video_options = [
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
        else:
            video_options = [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                str(video_bitrate),
                "-maxrate",
                str(maxrate),
                "-bufsize",
                str(bufsize),
            ]

        return common + video_options + [
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    def _probe(self, path: Path) -> VideoProbe:
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,duration",
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

        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        format_data = payload.get("format")
        duration_value = (
            format_data.get("duration")
            if isinstance(format_data, dict)
            else None
        )
        if duration_value is None:
            duration_value = video_stream.get("duration")

        duration = float(duration_value or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("La resolución del video no es válida.")

        return VideoProbe(width=width, height=height, duration=duration)

    @lru_cache(maxsize=1)
    def _supports_nvenc(self) -> bool:
        completed = _run_process(
            [self.ffmpeg, "-hide_banner", "-encoders"],
            timeout=60,
        )
        return (
            completed.returncode == 0
            and "h264_nvenc" in completed.stdout
        )


def _calculate_video_bitrate(duration: float) -> int:
    usable_bits = int(FAST1080_TARGET_SIZE_BYTES * 8 * 0.94)
    audio_bitrate = 128_000
    calculated = int(usable_bits / max(duration, 1.0)) - audio_bitrate
    return min(10_000_000, max(900_000, calculated))


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


FAST1080_ENHANCER = Fast1080Enhancer()
