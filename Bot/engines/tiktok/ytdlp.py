from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from config import COOKIES_FOLDER, DOWNLOAD_TIMEOUT, TIKTOK_DOWNLOADS
from engines.base import DownloadEngine
from models import DownloadResult
from utils import detect_platform


logger = logging.getLogger(__name__)


class TikTokYTDLPEngine(DownloadEngine):
    """Descarga videos de TikTok mediante el motor real de yt-dlp."""

    def __init__(self) -> None:
        self._download_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "yt-dlp"

    @property
    def platform(self) -> str:
        return "tiktok"

    async def download_async(self, url: str) -> DownloadResult:
        """Ejecuta yt-dlp fuera del hilo principal de Telegram."""

        return await asyncio.to_thread(self.download, url)

    def download(self, url: str) -> DownloadResult:
        clean_url = url.strip()

        if detect_platform(clean_url) != self.platform:
            return self._failure(
                clean_url,
                "El enlace no parece pertenecer a TikTok.",
            )

        TIKTOK_DOWNLOADS.mkdir(parents=True, exist_ok=True)

        with self._download_lock:
            try:
                options = self._build_options()

                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(clean_url, download=True)

                    if info is None:
                        raise DownloadError(
                            "yt-dlp no devolvió información del video."
                        )

                    if "entries" in info:
                        entries = [
                            entry for entry in (info.get("entries") or [])
                            if entry
                        ]
                        if not entries:
                            raise DownloadError(
                                "yt-dlp no encontró un video descargable."
                            )
                        info = entries[0]

                    file_path = self._resolve_file_path(info, ydl)

                if file_path is None or not file_path.is_file():
                    raise FileNotFoundError(
                        "yt-dlp terminó, pero no se encontró el archivo final."
                    )

                width, height = self._extract_dimensions(info)

                return DownloadResult(
                    success=True,
                    message="Video descargado correctamente con yt-dlp.",
                    file_path=file_path,
                    title=str(info.get("title") or "").strip(),
                    author=str(
                        info.get("uploader")
                        or info.get("creator")
                        or info.get("channel")
                        or ""
                    ).strip(),
                    platform=self.platform,
                    engine=self.name,
                    file_size=file_path.stat().st_size,
                    width=width,
                    height=height,
                    duration=float(info.get("duration") or 0),
                    extension=file_path.suffix.lstrip(".").lower(),
                    video_id=str(info.get("id") or "").strip(),
                    url=clean_url,
                    thumbnail=str(info.get("thumbnail") or "").strip(),
                )

            except DownloadError as error:
                logger.warning("yt-dlp no pudo completar la descarga: %s", error)
                return self._failure(
                    clean_url,
                    "yt-dlp no pudo descargar ese video.",
                    error,
                )
            except OSError as error:
                logger.warning("Error al guardar el archivo de yt-dlp: %s", error)
                return self._failure(
                    clean_url,
                    "yt-dlp procesó el video, pero no se pudo guardar.",
                    error,
                )
            except Exception as error:
                logger.exception("Error inesperado en el motor yt-dlp.")
                return self._failure(
                    clean_url,
                    "Ocurrió un error inesperado durante la descarga con yt-dlp.",
                    error,
                )

    def _build_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "format": "bestvideo*+bestaudio/best",
            "outtmpl": str(
                TIKTOK_DOWNLOADS
                / "%(uploader)s - %(title)s [%(id)s].%(ext)s"
            ),
            "merge_output_format": "mp4",
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
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "writethumbnail": False,
            "writeinfojson": False,
        }

        cookie_file = self._find_cookie_file()
        if cookie_file is not None:
            options["cookiefile"] = str(cookie_file)

        return options

    @staticmethod
    def _find_cookie_file() -> Path | None:
        """Usa cookies opcionales sin exigirlas para videos públicos."""

        for filename in (
            "tiktok.txt",
            "cookies-tiktok.txt",
            "cookies.txt",
        ):
            candidate = COOKIES_FOLDER / filename
            if candidate.is_file():
                return candidate

        return None

    @staticmethod
    def _resolve_file_path(
        info: dict[str, Any],
        ydl: YoutubeDL,
    ) -> Path | None:
        candidates: list[Path] = []

        for download in info.get("requested_downloads") or []:
            filepath = download.get("filepath")
            if filepath:
                candidates.append(Path(filepath))

        for key in ("filepath", "_filename"):
            value = info.get(key)
            if value:
                candidates.append(Path(value))

        try:
            candidates.append(Path(ydl.prepare_filename(info)))
        except Exception:
            pass

        expanded_candidates: list[Path] = []
        for candidate in candidates:
            expanded_candidates.append(candidate)

            for suffix in (".mp4", ".mkv", ".webm", ".mov"):
                expanded_candidates.append(candidate.with_suffix(suffix))

        for candidate in expanded_candidates:
            if candidate.is_file():
                return candidate

        video_id = str(info.get("id") or "").strip()
        if video_id:
            matching_files = [
                path
                for path in TIKTOK_DOWNLOADS.iterdir()
                if path.is_file()
                and video_id in path.name
                and path.suffix.lower() not in {".part", ".ytdl"}
            ]

            if matching_files:
                return max(
                    matching_files,
                    key=lambda path: path.stat().st_mtime,
                )

        return None

    @staticmethod
    def _extract_dimensions(info: dict[str, Any]) -> tuple[int, int]:
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)

        if width > 0 and height > 0:
            return width, height

        formats = info.get("requested_formats") or []
        dimensions = [
            (
                int(item.get("width") or 0),
                int(item.get("height") or 0),
            )
            for item in formats
            if isinstance(item, dict)
        ]

        dimensions = [
            (candidate_width, candidate_height)
            for candidate_width, candidate_height in dimensions
            if candidate_width > 0 and candidate_height > 0
        ]

        if not dimensions:
            return 0, 0

        return max(
            dimensions,
            key=lambda item: item[0] * item[1],
        )

    def _failure(
        self,
        url: str,
        message: str,
        error: object = "",
    ) -> DownloadResult:
        return DownloadResult(
            success=False,
            message=message,
            platform=self.platform,
            engine=self.name,
            url=url,
            error=str(error) if error else "",
        )


TikTokYT_DLP_Engine = TikTokYTDLPEngine
