from __future__ import annotations

import asyncio
import logging
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests

from config import COOKIES_FOLDER, DOWNLOAD_TIMEOUT, INSTAGRAM_DOWNLOADS


logger = logging.getLogger(__name__)

_INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}
_SUPPORTED_PATHS = {"p", "reel", "reels", "tv"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
_MEDIA_EXTENSIONS = _IMAGE_EXTENSIONS | _VIDEO_EXTENSIONS
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


@dataclass(slots=True)
class InstagramDownloadResult:
    success: bool
    message: str
    files: list[Path] = field(default_factory=list)
    working_directory: Path | None = None
    error: str = ""
    content_type: str = "post"
    engine: str = "gallery-dl"

    @property
    def total_size(self) -> int:
        total = 0
        for path in self.files:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total


async def resolve_instagram_url(url: str) -> str:
    """Normaliza publicaciones individuales y resuelve enlaces /share/."""
    clean_url = url.strip()
    normalized = normalize_instagram_url(clean_url)
    if normalized is not None:
        return normalized

    try:
        parsed = urlparse(clean_url)
    except ValueError:
        return clean_url

    host = (parsed.hostname or "").lower()
    if host not in _INSTAGRAM_HOSTS or not parsed.path.lower().startswith("/share/"):
        return clean_url

    return await asyncio.to_thread(_resolve_instagram_share_url_sync, clean_url)


def normalize_instagram_url(url: str) -> str | None:
    """Devuelve una URL canónica para /p/, /reel(s)/ o /tv/."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host not in _INSTAGRAM_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    kind = parts[0].lower()
    shortcode = parts[1].strip()
    if kind not in _SUPPORTED_PATHS or not shortcode:
        return None

    if kind == "reels":
        kind = "reel"

    path = f"/{kind}/{shortcode}/"
    return urlunparse(("https", "www.instagram.com", path, "", "", ""))


def is_instagram_single_media_url(url: str) -> bool:
    return normalize_instagram_url(url) is not None


def is_instagram_video_url(url: str) -> bool:
    normalized = normalize_instagram_url(url)
    if normalized is None:
        return False

    path = urlparse(normalized).path.lower()
    return path.startswith("/reel/") or path.startswith("/tv/")


def instagram_content_type(url: str) -> str:
    normalized = normalize_instagram_url(url)
    if normalized is None:
        return "publicación"

    path = urlparse(normalized).path.lower()
    if path.startswith("/reel/"):
        return "reel"
    if path.startswith("/tv/"):
        return "video"
    return "publicación"


async def download_instagram_media(url: str) -> InstagramDownloadResult:
    """
    Descarga una publicación individual de Instagram en máxima calidad disponible.

    Se fuerza la API REST de gallery-dl, que expone medios de mayor resolución,
    y los videos se delegan a yt-dlp con selección bestvideo+bestaudio.
    """
    clean_url = normalize_instagram_url(url)
    if clean_url is None:
        return InstagramDownloadResult(
            success=False,
            message=(
                "El enlace no corresponde a una publicación, Reel o video "
                "individual de Instagram."
            ),
        )

    content_type = instagram_content_type(clean_url)
    request_folder = (
        INSTAGRAM_DOWNLOADS
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
        "extractor.instagram.api=rest",
        "--option",
        "extractor.instagram.videos=ytdl",
        "--option",
        "extractor.instagram.audio=false",
        "--option",
        "extractor.instagram.previews=false",
        "--option",
        "extractor.instagram.warn-images=true",
        "--option",
        "extractor.instagram.warn-videos=true",
        "--option",
        "extractor.ytdl.format=bestvideo*+bestaudio/best",
        "--option",
        "downloader.ytdl.format=bestvideo*+bestaudio/best",
        "--option",
        "downloader.ytdl.module=yt-dlp",
    ]

    cookie_file = find_instagram_cookie_file()
    if cookie_file is not None:
        command.extend(("--cookies", str(cookie_file)))

    command.append(clean_url)

    logger.info(
        "Descargando %s de Instagram con gallery-dl en máxima calidad disponible.",
        content_type,
    )

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
            return InstagramDownloadResult(
                success=False,
                message="Instagram tardó demasiado en entregar el contenido.",
                working_directory=request_folder,
                error="Tiempo de espera agotado.",
                content_type=content_type,
            )

        output = _decode_process_output(stdout)
        error_output = _decode_process_output(stderr)
        files = _collect_media_files(request_folder)

        if error_output:
            logger.warning("Salida de gallery-dl para Instagram: %s", error_output)

        if process.returncode != 0:
            detail = error_output or output or f"Código {process.returncode}"
            logger.warning("gallery-dl no pudo descargar Instagram: %s", detail)
            return InstagramDownloadResult(
                success=False,
                message="No se pudo descargar ese contenido de Instagram.",
                files=files,
                working_directory=request_folder,
                error=detail,
                content_type=content_type,
            )

        if not files:
            detail = error_output or output or "gallery-dl no generó archivos."
            logger.warning("gallery-dl terminó sin medios de Instagram: %s", detail)
            return InstagramDownloadResult(
                success=False,
                message="Instagram no entregó archivos descargables.",
                working_directory=request_folder,
                error=detail,
                content_type=content_type,
            )

        logger.info(
            "gallery-dl descargó %s archivo(s) de Instagram en %s.",
            len(files),
            request_folder,
        )
        return InstagramDownloadResult(
            success=True,
            message="Contenido de Instagram descargado correctamente.",
            files=files,
            working_directory=request_folder,
            content_type=content_type,
        )

    except FileNotFoundError as error:
        logger.exception("No se pudo iniciar gallery-dl para Instagram.")
        return InstagramDownloadResult(
            success=False,
            message="gallery-dl no está instalado en el entorno virtual.",
            working_directory=request_folder,
            error=str(error),
            content_type=content_type,
        )
    except OSError as error:
        logger.exception("Windows no pudo ejecutar gallery-dl para Instagram.")
        return InstagramDownloadResult(
            success=False,
            message="Windows no pudo ejecutar correctamente gallery-dl.",
            working_directory=request_folder,
            error=str(error),
            content_type=content_type,
        )


def find_instagram_cookie_file() -> Path | None:
    for filename in (
        "instagram.txt",
        "cookies-instagram.txt",
        "cookies.txt",
    ):
        candidate = COOKIES_FOLDER / filename
        if candidate.is_file():
            return candidate
    return None


def _resolve_instagram_share_url_sync(url: str) -> str:
    try:
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=30,
            headers=_REQUEST_HEADERS,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.warning("No se pudo resolver el enlace compartido de Instagram: %s", error)
        return url

    normalized = normalize_instagram_url(response.url)
    if normalized is not None:
        logger.info("Enlace de Instagram resuelto: %s -> %s", url, normalized)
        return normalized

    logger.warning(
        "El enlace compartido de Instagram terminó en una URL no compatible: %s",
        response.url,
    )
    return url


def _collect_media_files(folder: Path) -> list[Path]:
    files = [
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _MEDIA_EXTENSIONS
        and not path.name.lower().endswith(".part")
    ]
    return sorted(files, key=_natural_sort_key)


def _natural_sort_key(path: Path) -> list[object]:
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def _decode_process_output(value: bytes | None) -> str:
    if not value:
        return ""
    return value.decode("utf-8", errors="replace").strip()
