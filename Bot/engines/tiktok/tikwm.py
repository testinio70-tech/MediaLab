from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import DOWNLOAD_TIMEOUT, TIKTOK_DOWNLOADS
from engines.base import DownloadEngine
from models import DownloadResult
from utils import sanitize_filename, unique_file_path


logger = logging.getLogger(__name__)


class TikWMError(RuntimeError):
    """Error controlado producido por el servicio TikWM."""


class TikWMEngine(DownloadEngine):
    """
    Descarga el archivo original de TikTok mediante TikWM Original Downloader.

    Flujo utilizado:
        1. POST /api/video/task/submit
        2. Obtener task_id
        3. GET /api/video/task/result?task_id=...
        4. Esperar download_url
        5. Descargar el MP4 por streaming
    """

    BASE_URL = "https://www.tikwm.com"
    ORIGINAL_PAGE_URL = f"{BASE_URL}/originalDownloader.html"
    SUBMIT_URL = f"{BASE_URL}/api/video/task/submit"
    RESULT_URL = f"{BASE_URL}/api/video/task/result"

    POLL_INTERVAL_SECONDS = 2.0
    REQUEST_TIMEOUT_SECONDS = 45
    CHUNK_SIZE = 1024 * 1024

    _TIKTOK_HOSTS = {
        "tiktok.com",
        "www.tiktok.com",
        "m.tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    }

    _VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)", re.IGNORECASE)

    _FAILURE_WORDS = (
        "fail",
        "failed",
        "error",
        "invalid",
        "not found",
        "unavailable",
        "denied",
        "forbidden",
        "private",
        "expired",
    )

    _PROCESSING_WORDS = (
        "processing",
        "pending",
        "waiting",
        "queued",
        "running",
        "created",
        "submitted",
    )

    def __init__(self, session: Session | None = None) -> None:
        self._session = session or self._create_session()
        self._download_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "TikWM Original"

    @property
    def platform(self) -> str:
        return "tiktok"

    async def download_async(self, url: str) -> DownloadResult:
        """Ejecuta la descarga fuera del hilo principal de Telegram."""

        return await asyncio.to_thread(self.download, url)

    def download(self, url: str) -> DownloadResult:
        clean_url = url.strip()

        if not self._is_tiktok_url(clean_url):
            return self._failure(
                clean_url,
                "El enlace no parece pertenecer a TikTok.",
            )

        TIKTOK_DOWNLOADS.mkdir(parents=True, exist_ok=True)

        with self._download_lock:
            try:
                task_id = self._submit_task(clean_url)
                result_payload = self._wait_for_result(task_id)
                media_data = self._extract_media_data(result_payload)

                download_urls = self._extract_download_urls(media_data)
                if not download_urls:
                    raise TikWMError(
                        "TikWM terminó la tarea, pero no entregó download_url."
                    )

                video_id = self._extract_video_id(clean_url, media_data)
                title = self._extract_text(
                    media_data,
                    "title",
                    "desc",
                    "description",
                )
                author = self._extract_author(media_data)
                thumbnail = self._extract_text(
                    media_data,
                    "thumbnail",
                    "cover",
                    "cover_url",
                    "origin_cover",
                )

                file_path, direct_url = self._download_original_file(
                    urls=download_urls,
                    title=title,
                    author=author,
                    video_id=video_id,
                )
                file_size = file_path.stat().st_size

                return DownloadResult(
                    success=True,
                    message="Video original descargado correctamente con TikWM.",
                    file_path=file_path,
                    title=title,
                    author=author,
                    platform=self.platform,
                    engine=self.name,
                    file_size=file_size,
                    video_id=video_id,
                    url=clean_url,
                    thumbnail=thumbnail,
                    direct_url=direct_url,
                )

            except TikWMError as error:
                logger.warning("TikWM no pudo completar la descarga: %s", error)
                return self._failure(
                    clean_url,
                    "TikWM no pudo descargar ese video.",
                    error,
                )
            except requests.RequestException as error:
                logger.warning("Error de red al comunicarse con TikWM: %s", error)
                return self._failure(
                    clean_url,
                    "No se pudo conectar correctamente con TikWM.",
                    error,
                )
            except OSError as error:
                logger.warning("Error al guardar el archivo de TikWM: %s", error)
                return self._failure(
                    clean_url,
                    "TikWM descargó el video, pero no se pudo guardar.",
                    error,
                )
            except Exception as error:
                logger.exception("Error inesperado en el motor TikWM.")
                return self._failure(
                    clean_url,
                    "Ocurrió un error inesperado durante la descarga.",
                    error,
                )

    def _submit_task(self, url: str) -> str:
        response = self._session.post(
            self.SUBMIT_URL,
            data={
                "url": url,
                "web": "1",
            },
            timeout=self.REQUEST_TIMEOUT_SECONDS,
        )

        payload = self._read_json_response(response, "crear la tarea")
        task_id = self._find_first_scalar(
            payload,
            "task_id",
            "taskId",
            "taskid",
        )

        if task_id is None or not str(task_id).strip():
            message = self._extract_api_message(payload)
            detail = f": {message}" if message else ""
            raise TikWMError(
                f"TikWM no devolvió un task_id válido{detail}."
            )

        return str(task_id).strip()

    def _wait_for_result(self, task_id: str) -> Mapping[str, Any]:
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT
        last_message = ""

        while time.monotonic() < deadline:
            response = self._session.get(
                self.RESULT_URL,
                params={"task_id": task_id},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
            payload = self._read_json_response(
                response,
                "consultar el resultado",
            )

            media_data = self._extract_media_data(payload)
            if self._extract_download_urls(media_data):
                return payload

            last_message = self._extract_api_message(payload)
            if self._payload_is_failure(payload, last_message):
                detail = f": {last_message}" if last_message else ""
                raise TikWMError(f"La tarea de TikWM falló{detail}.")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            time.sleep(min(self.POLL_INTERVAL_SECONDS, remaining))

        detail = f" Último estado: {last_message}." if last_message else ""
        raise TikWMError(
            "TikWM no terminó la tarea dentro del tiempo permitido."
            f"{detail}"
        )

    def _download_original_file(
        self,
        urls: list[str],
        title: str,
        author: str,
        video_id: str,
    ) -> tuple[Path, str]:
        stem = self._build_filename_stem(
            title=title,
            author=author,
            video_id=video_id,
        )

        errors: list[str] = []

        for media_url in urls:
            destination: Path | None = None
            partial_path: Path | None = None

            try:
                with self._session.get(
                    media_url,
                    stream=True,
                    allow_redirects=True,
                    timeout=(30, min(DOWNLOAD_TIMEOUT, 180)),
                    headers={
                        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
                        "Referer": self.ORIGINAL_PAGE_URL,
                    },
                ) as response:
                    response.raise_for_status()
                    self._validate_media_response(response)

                    suffix = self._guess_video_suffix(response, media_url)
                    destination = unique_file_path(
                        TIKTOK_DOWNLOADS,
                        stem,
                        suffix,
                    )
                    partial_path = destination.with_suffix(
                        f"{destination.suffix}.part"
                    )

                    bytes_written = 0
                    with partial_path.open("wb") as output_file:
                        for chunk in response.iter_content(
                            chunk_size=self.CHUNK_SIZE
                        ):
                            if not chunk:
                                continue

                            output_file.write(chunk)
                            bytes_written += len(chunk)

                    if bytes_written <= 0:
                        raise TikWMError(
                            "TikWM devolvió un archivo de video vacío."
                        )

                    partial_path.replace(destination)
                    return destination, media_url

            except (requests.RequestException, OSError, TikWMError) as error:
                errors.append(str(error))

                if partial_path is not None:
                    try:
                        partial_path.unlink(missing_ok=True)
                    except OSError:
                        pass

                if destination is not None and destination.is_file():
                    try:
                        destination.unlink(missing_ok=True)
                    except OSError:
                        pass

        detail = " | ".join(error for error in errors if error)
        if detail:
            raise TikWMError(
                f"Ninguna URL de descarga de TikWM funcionó: {detail}"
            )

        raise TikWMError("TikWM no proporcionó una URL descargable.")

    @classmethod
    def _extract_media_data(
        cls,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """
        Devuelve el diccionario más cercano que contiene download_url.

        TikWM puede envolver el resultado dentro de uno o varios campos
        llamados data/result. La búsqueda recursiva permite tolerar esas
        variaciones sin depender de una única forma exacta del JSON.
        """

        queue: deque[Mapping[str, Any]] = deque([payload])
        best_candidate: Mapping[str, Any] = payload

        while queue:
            current = queue.popleft()

            if any(
                key in current
                for key in (
                    "download_url",
                    "downloadUrl",
                    "play_url",
                    "playUrl",
                )
            ):
                return current

            if any(
                key in current
                for key in (
                    "title",
                    "author",
                    "video_id",
                    "id",
                    "cover",
                )
            ):
                best_candidate = current

            for value in current.values():
                if isinstance(value, Mapping):
                    queue.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, Mapping):
                            queue.append(item)

        return best_candidate

    @classmethod
    def _extract_download_urls(
        cls,
        payload: Mapping[str, Any],
    ) -> list[str]:
        """Prioriza download_url, que corresponde al archivo original."""

        collected: list[str] = []

        for keys in (
            ("download_url", "downloadUrl", "original_url", "originalUrl"),
            ("play_url", "playUrl"),
        ):
            for key in keys:
                value = cls._find_first_scalar(payload, key)
                if not isinstance(value, str):
                    continue

                normalized = cls._normalize_media_url(value)
                if normalized and normalized not in collected:
                    collected.append(normalized)

        return collected

    @classmethod
    def _find_first_scalar(
        cls,
        payload: Any,
        *keys: str,
    ) -> Any | None:
        wanted = set(keys)
        queue: deque[Any] = deque([payload])

        while queue:
            current = queue.popleft()

            if isinstance(current, Mapping):
                for key, value in current.items():
                    if key in wanted and not isinstance(
                        value,
                        (Mapping, list, tuple),
                    ):
                        return value

                queue.extend(current.values())

            elif isinstance(current, (list, tuple)):
                queue.extend(current)

        return None

    @classmethod
    def _extract_text(
        cls,
        payload: Mapping[str, Any],
        *keys: str,
    ) -> str:
        value = cls._find_first_scalar(payload, *keys)
        return str(value).strip() if value is not None else ""

    @classmethod
    def _extract_author(cls, payload: Mapping[str, Any]) -> str:
        direct = cls._find_first_scalar(
            payload,
            "author_name",
            "authorName",
            "nickname",
            "unique_id",
            "uniqueId",
            "username",
        )
        if direct is not None:
            return str(direct).strip()

        for container_name in ("author", "author_info", "user"):
            container = cls._find_first_mapping(payload, container_name)
            if container is None:
                continue

            for key in (
                "nickname",
                "unique_id",
                "uniqueId",
                "username",
                "name",
                "id",
            ):
                value = container.get(key)
                if value is not None and not isinstance(value, (Mapping, list)):
                    return str(value).strip()

        value = payload.get("author")
        if value is not None and not isinstance(value, (Mapping, list)):
            return str(value).strip()

        return ""

    @classmethod
    def _find_first_mapping(
        cls,
        payload: Any,
        wanted_key: str,
    ) -> Mapping[str, Any] | None:
        queue: deque[Any] = deque([payload])

        while queue:
            current = queue.popleft()

            if isinstance(current, Mapping):
                value = current.get(wanted_key)
                if isinstance(value, Mapping):
                    return value

                queue.extend(current.values())
            elif isinstance(current, (list, tuple)):
                queue.extend(current)

        return None

    @classmethod
    def _extract_video_id(
        cls,
        source_url: str,
        payload: Mapping[str, Any],
    ) -> str:
        value = cls._find_first_scalar(
            payload,
            "video_id",
            "videoId",
            "aweme_id",
            "awemeId",
            "id",
        )
        if value is not None and str(value).strip():
            return str(value).strip()

        match = cls._VIDEO_ID_PATTERN.search(source_url)
        return match.group(1) if match else ""

    @classmethod
    def _payload_is_failure(
        cls,
        payload: Mapping[str, Any],
        message: str,
    ) -> bool:
        status = cls._find_first_scalar(
            payload,
            "status",
            "state",
            "task_status",
            "taskStatus",
        )

        if isinstance(status, str):
            lowered_status = status.strip().lower()
            if any(word in lowered_status for word in cls._FAILURE_WORDS):
                return True
            if any(word in lowered_status for word in cls._PROCESSING_WORDS):
                return False

        if isinstance(status, (int, float)) and status < 0:
            return True

        code = cls._find_first_scalar(payload, "code", "status_code")
        if isinstance(code, str) and code.lstrip("-").isdigit():
            code = int(code)

        if isinstance(code, (int, float)) and code not in (0, 1, 200):
            lowered_message = message.lower()
            if not any(
                word in lowered_message
                for word in cls._PROCESSING_WORDS
            ):
                return True

        lowered_message = message.lower()
        return any(word in lowered_message for word in cls._FAILURE_WORDS)

    @classmethod
    def _extract_api_message(cls, payload: Mapping[str, Any]) -> str:
        value = cls._find_first_scalar(
            payload,
            "message",
            "msg",
            "error",
            "detail",
            "status_text",
        )
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _read_json_response(
        response: Response,
        action: str,
    ) -> Mapping[str, Any]:
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            raise TikWMError(
                f"TikWM respondió HTTP {response.status_code} al {action}."
            ) from error

        try:
            payload = response.json()
        except ValueError as error:
            content_type = response.headers.get("Content-Type", "desconocido")
            raise TikWMError(
                "TikWM no devolvió JSON válido al "
                f"{action} (Content-Type: {content_type})."
            ) from error

        if not isinstance(payload, Mapping):
            raise TikWMError(
                f"TikWM devolvió una respuesta inesperada al {action}."
            )

        return payload

    @classmethod
    def _normalize_media_url(cls, value: str) -> str:
        cleaned = value.strip().replace("\\/", "/")
        if not cleaned:
            return ""

        if cleaned.startswith("//"):
            return f"https:{cleaned}"

        return urljoin(cls.BASE_URL, cleaned)

    @staticmethod
    def _validate_media_response(response: Response) -> None:
        content_type = response.headers.get("Content-Type", "").lower()

        if any(
            blocked_type in content_type
            for blocked_type in (
                "text/html",
                "application/json",
                "text/plain",
            )
        ):
            raise TikWMError(
                "La URL de TikWM devolvió texto en lugar de un video "
                f"({content_type or 'tipo desconocido'})."
            )

    @staticmethod
    def _guess_video_suffix(response: Response, media_url: str) -> str:
        content_type = response.headers.get("Content-Type", "").lower()

        if "video/webm" in content_type:
            return ".webm"
        if "video/quicktime" in content_type:
            return ".mov"
        if "video/x-m4v" in content_type:
            return ".m4v"

        url_suffix = Path(urlparse(media_url).path).suffix.lower()
        if url_suffix in {".mp4", ".webm", ".mov", ".m4v"}:
            return url_suffix

        return ".mp4"

    @staticmethod
    def _build_filename_stem(
        title: str,
        author: str,
        video_id: str,
    ) -> str:
        safe_author = sanitize_filename(
            author,
            default="TikTok",
            max_length=45,
        )
        safe_title = sanitize_filename(
            title,
            default="video original",
            max_length=105,
        )

        parts = [safe_author, safe_title]
        if video_id:
            parts.append(f"[{sanitize_filename(video_id, max_length=30)}]")

        return " - ".join(part for part in parts if part)

    @classmethod
    def _is_tiktok_url(cls, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False

        if parsed.scheme.lower() not in {"http", "https"}:
            return False

        hostname = (parsed.hostname or "").lower()
        return (
            hostname in cls._TIKTOK_HOSTS
            or hostname.endswith(".tiktok.com")
        )

    @staticmethod
    def _create_session() -> Session:
        session = requests.Session()

        retry = Retry(
            total=4,
            connect=4,
            read=4,
            status=4,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=4,
            pool_maxsize=8,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/150.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
                "Origin": TikWMEngine.BASE_URL,
                "Referer": TikWMEngine.ORIGINAL_PAGE_URL,
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        return session

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


# Alias descriptivo opcional para futuras importaciones.
TikTokTikWMEngine = TikWMEngine
