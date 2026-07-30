from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from config import (
    AUDIO_DOWNLOADS,
    AUDIO_MAX_DURATION_SECONDS,
    COOKIES_FOLDER,
    DOWNLOAD_TIMEOUT,
)
from utils import detect_platform


logger = logging.getLogger(__name__)
_AUDIO_PLATFORMS = frozenset({"youtube", "tiktok", "instagram"})


@dataclass(slots=True)
class AudioDownloadResult:
    success: bool
    message: str
    file_path: Path | None = None
    title: str = ""
    author: str = ""
    platform: str = ""
    engine: str = "yt-dlp · MP3 192 kbps"
    file_size: int = 0
    duration: float = 0.0
    audio_id: str = ""
    error: str = ""


def supports_audio_url(url: str) -> bool:
    return detect_platform(url) in _AUDIO_PLATFORMS


class AudioExtractor:
    """Obtiene la pista pública y la entrega como un MP3 temporal."""

    def __init__(self) -> None:
        self._download_lock = threading.Lock()

    async def download_async(self, url: str) -> AudioDownloadResult:
        return await asyncio.to_thread(self.download, url)

    def download(self, url: str) -> AudioDownloadResult:
        clean_url = url.strip()
        platform = detect_platform(clean_url)
        if platform not in _AUDIO_PLATFORMS:
            return self._failure(
                platform or "",
                "Ese enlace no es compatible con la extracción de MP3.",
            )

        AUDIO_DOWNLOADS.mkdir(parents=True, exist_ok=True)
        try:
            with self._download_lock:
                with YoutubeDL(self._build_options(platform)) as ydl:
                    info = ydl.extract_info(clean_url, download=True)
                    if info is None:
                        raise DownloadError("yt-dlp no devolvió información del audio.")
                    if "entries" in info:
                        entries = [entry for entry in info.get("entries") or [] if entry]
                        if not entries:
                            raise DownloadError("No se encontró una pista de audio descargable.")
                        info = entries[0]
                    file_path = self._resolve_file_path(info, ydl)

            if file_path is None or not file_path.is_file():
                raise FileNotFoundError("yt-dlp terminó, pero no se encontró el MP3 final.")

            return AudioDownloadResult(
                success=True,
                message="MP3 extraído correctamente.",
                file_path=file_path,
                title=str(info.get("title") or "Audio").strip(),
                author=str(
                    info.get("uploader")
                    or info.get("creator")
                    or info.get("channel")
                    or ""
                ).strip(),
                platform=platform,
                file_size=file_path.stat().st_size,
                duration=float(info.get("duration") or 0),
                audio_id=str(info.get("id") or "").strip(),
            )
        except DownloadError as error:
            logger.warning("No se pudo extraer audio de %s: %s", platform, error)
            return self._failure(platform, "No fue posible obtener el audio de ese enlace.", error)
        except OSError as error:
            logger.warning("No se pudo guardar el MP3: %s", error)
            return self._failure(
                platform,
                "El audio se procesó, pero no se pudo guardar temporalmente.",
                error,
            )
        except Exception as error:
            logger.exception("Error inesperado durante la extracción de MP3.")
            return self._failure(
                platform,
                "Ocurrió un error inesperado al extraer el MP3.",
                error,
            )

    def _build_options(self, platform: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "impersonate": ImpersonateTarget.from_str("chrome"),
            "format": "bestaudio/best",
            "outtmpl": str(AUDIO_DOWNLOADS / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "overwrites": False,
            "continuedl": True,
            "retries": 5,
            "fragment_retries": 5,
            "file_access_retries": 3,
            "socket_timeout": min(DOWNLOAD_TIMEOUT, 120),
            "windowsfilenames": True,
            "writethumbnail": False,
            "writeinfojson": False,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "match_filter": self._duration_filter,
        }
        cookie_file = self._find_cookie_file(platform)
        if cookie_file is not None:
            options["cookiefile"] = str(cookie_file)
        return options

    @staticmethod
    def _duration_filter(info: dict[str, Any], *, incomplete: bool) -> str | None:
        if incomplete:
            return None
        duration = float(info.get("duration") or 0)
        if duration > AUDIO_MAX_DURATION_SECONDS:
            return (
                "El contenido supera el límite de "
                f"{AUDIO_MAX_DURATION_SECONDS // 60} minutos para MP3."
            )
        return None

    @staticmethod
    def _find_cookie_file(platform: str) -> Path | None:
        candidates = {
            "tiktok": ("tiktok.txt", "cookies-tiktok.txt", "cookies.txt"),
            "instagram": ("instagram.txt", "cookies-instagram.txt", "cookies.txt"),
            "youtube": ("youtube.txt", "cookies-youtube.txt", "cookies.txt"),
        }
        for filename in candidates.get(platform, ()):
            candidate = COOKIES_FOLDER / filename
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _resolve_file_path(info: dict[str, Any], ydl: YoutubeDL) -> Path | None:
        candidates: list[Path] = []
        for key in ("filepath", "_filename"):
            value = info.get(key)
            if value:
                candidates.append(Path(value))
        try:
            candidates.append(Path(ydl.prepare_filename(info)))
        except Exception:
            pass

        expanded = [candidate.with_suffix(".mp3") for candidate in candidates]
        expanded.extend(candidates)
        for candidate in expanded:
            if candidate.is_file() and candidate.suffix.lower() == ".mp3":
                return candidate

        audio_id = str(info.get("id") or "").strip()
        if audio_id and AUDIO_DOWNLOADS.exists():
            matches = [
                path
                for path in AUDIO_DOWNLOADS.iterdir()
                if path.is_file()
                and path.suffix.lower() == ".mp3"
                and audio_id in path.name
            ]
            if matches:
                return max(matches, key=lambda path: path.stat().st_mtime)
        return None

    @staticmethod
    def _failure(
        platform: str,
        message: str,
        error: Exception | None = None,
    ) -> AudioDownloadResult:
        return AudioDownloadResult(
            success=False,
            message=message,
            platform=platform,
            error=str(error or ""),
        )


AUDIO_EXTRACTOR = AudioExtractor()
