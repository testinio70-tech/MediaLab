from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from config import COOKIES_FOLDER, DOWNLOAD_TIMEOUT, TIKTOK_PHOTOS


logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}


@dataclass(slots=True)
class PhotoDownloadResult:
    success: bool
    message: str
    files: list[Path] = field(default_factory=list)
    working_directory: Path | None = None
    error: str = ""

    @property
    def total_size(self) -> int:
        total = 0
        for path in self.files:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total


def is_tiktok_photo_url(url: str) -> bool:
    """Detecta publicaciones fotográficas de TikTok por la ruta /photo/."""

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return host in _TIKTOK_HOSTS and "/photo/" in path


async def download_tiktok_photos(url: str) -> PhotoDownloadResult:
    """Descarga un carrusel fotográfico con gallery-dl en un subproceso."""

    clean_url = url.strip()
    if not is_tiktok_photo_url(clean_url):
        return PhotoDownloadResult(
            success=False,
            message="El enlace no parece ser una publicación fotográfica de TikTok.",
        )

    request_folder = (
        TIKTOK_PHOTOS
        / f"{int(time.time())}-{secrets.token_hex(4)}"
    )
    request_folder.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--config-ignore",
        "--no-input",
        "--quiet",
        "--windows-filenames",
        "--directory",
        str(request_folder),
        "--option",
        "extractor.tiktok.photos=true",
        "--option",
        "extractor.tiktok.videos=false",
        "--option",
        "extractor.tiktok.audio=false",
        "--option",
        "extractor.tiktok.covers=false",
        "--option",
        "extractor.tiktok.subtitles=false",
    ]

    cookie_file = _find_cookie_file()
    if cookie_file is not None:
        command.extend(("--cookies", str(cookie_file)))

    command.append(clean_url)

    logger.info("Descargando fotos de TikTok con gallery-dl.")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=DOWNLOAD_TIMEOUT,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return PhotoDownloadResult(
                success=False,
                message="gallery-dl tardó demasiado en descargar las imágenes.",
                working_directory=request_folder,
                error="Tiempo de espera agotado.",
            )

        output = _decode_process_output(stdout)
        error_output = _decode_process_output(stderr)

        files = _collect_image_files(request_folder)
        if process.returncode != 0:
            detail = error_output or output or f"Código {process.returncode}"
            logger.warning("gallery-dl no pudo descargar el carrusel: %s", detail)
            return PhotoDownloadResult(
                success=False,
                message="No se pudieron descargar las imágenes de esa publicación.",
                files=files,
                working_directory=request_folder,
                error=detail,
            )

        if not files:
            detail = error_output or output or "gallery-dl no generó imágenes."
            logger.warning("gallery-dl terminó sin imágenes: %s", detail)
            return PhotoDownloadResult(
                success=False,
                message="La publicación no entregó imágenes descargables.",
                working_directory=request_folder,
                error=detail,
            )

        logger.info(
            "gallery-dl descargó %s imágenes en %s.",
            len(files),
            request_folder,
        )
        return PhotoDownloadResult(
            success=True,
            message="Imágenes descargadas correctamente.",
            files=files,
            working_directory=request_folder,
        )

    except FileNotFoundError as error:
        logger.exception("No se pudo iniciar gallery-dl.")
        return PhotoDownloadResult(
            success=False,
            message="gallery-dl no está instalado en el entorno virtual.",
            working_directory=request_folder,
            error=str(error),
        )
    except OSError as error:
        logger.exception("Error del sistema al ejecutar gallery-dl.")
        return PhotoDownloadResult(
            success=False,
            message="Windows no pudo ejecutar correctamente gallery-dl.",
            working_directory=request_folder,
            error=str(error),
        )


def _collect_image_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )


def _find_cookie_file() -> Path | None:
    for filename in (
        "tiktok.txt",
        "cookies-tiktok.txt",
        "cookies.txt",
    ):
        candidate = COOKIES_FOLDER / filename
        if candidate.is_file():
            return candidate

    return None


def _decode_process_output(value: bytes | None) -> str:
    if not value:
        return ""
    return value.decode("utf-8", errors="replace").strip()
