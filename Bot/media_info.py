from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from config import FFPROBE_BINARY
from models import DownloadResult
from utils import format_file_size


logger = logging.getLogger(__name__)


def probe_video_resolution(file_path: Path) -> tuple[int, int]:
    """Obtiene ancho y alto mediante ffprobe; devuelve ceros si no está disponible."""

    if not file_path.is_file():
        return 0, 0

    command = [
        FFPROBE_BINARY,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(file_path),
    ]

    run_options: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 20,
        "check": False,
    }

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        run_options["creationflags"] = create_no_window

    try:
        completed = subprocess.run(command, **run_options)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        logger.warning("No se pudo ejecutar ffprobe para analizar %s.", file_path)
        return 0, 0

    if completed.returncode != 0:
        logger.warning(
            "ffprobe no pudo analizar %s: %s",
            file_path,
            completed.stderr.strip(),
        )
        return 0, 0

    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
        return 0, 0

    return max(width, 0), max(height, 0)


def enrich_download_result(result: DownloadResult) -> DownloadResult:
    """Completa peso, extensión y resolución usando el archivo descargado."""

    file_path = result.file_path
    if file_path is None or not file_path.is_file():
        return result

    result.file_size = file_path.stat().st_size
    result.extension = file_path.suffix.lstrip(".").lower()

    if result.width <= 0 or result.height <= 0:
        width, height = probe_video_resolution(file_path)
        if width > 0 and height > 0:
            result.width = width
            result.height = height

    return result


def format_resolution(width: int, height: int) -> str:
    """Convierte ancho y alto en una resolución legible."""

    if width <= 0 or height <= 0:
        return "No disponible"

    return f"{width} × {height}"


def format_result_details(
    result: DownloadResult,
    heading: str | None = None,
) -> str:
    """Genera el bloque de información mostrado en Telegram."""

    lines: list[str] = []

    if heading:
        lines.append(heading)

    if result.title:
        lines.append(f"🎬 Título: {_shorten(result.title, 140)}")

    if result.author:
        lines.append(f"👤 Autor: {_shorten(result.author, 80)}")

    lines.extend(
        [
            f"⚙️ Motor: {result.engine or 'No disponible'}",
            f"📦 Peso total: {format_file_size(max(result.file_size, 0))}",
            f"📐 Resolución: {format_resolution(result.width, result.height)}",
        ]
    )

    return "\n".join(lines)


def _shorten(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned

    return f"{cleaned[: limit - 1].rstrip()}…"
