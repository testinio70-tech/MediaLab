from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget

from config import (
    COOKIES_FOLDER,
    DOWNLOAD_TIMEOUT,
    FACEBOOK_DOWNLOADS,
    INSTAGRAM_DOWNLOADS,
    WATCHER_DISCOVERY_TIMEOUT_SECONDS,
    WATCHER_MAX_DISCOVERED_POSTS,
)
from engines.tiktok.tikwm import TikWMEngine
from services.instagram import download_instagram_media, normalize_instagram_url
from services.watcher_database import WatcherSource


logger = logging.getLogger(__name__)
_DISCOVERY_LOCK = threading.Lock()
_TIKWM = TikWMEngine()


@dataclass(slots=True, frozen=True)
class DiscoveredPost:
    key: str
    url: str
    title: str = ""
    is_story: bool = False


@dataclass(slots=True, frozen=True)
class DownloadedPost:
    platform: str
    title: str
    files: tuple[Path, ...]


def _cookie_file(platform: str) -> Path | None:
    names = {
        "tiktok": ("tiktok.txt", "cookies-tiktok.txt", "cookies.txt"),
        "instagram": ("instagram.txt", "cookies-instagram.txt", "cookies.txt"),
        "facebook": ("facebook.txt", "cookies-facebook.txt", "cookies.txt"),
    }
    for name in names.get(platform, ()):
        candidate = COOKIES_FOLDER / name
        if candidate.is_file():
            return candidate
    return None


def _profile_candidates(source: WatcherSource) -> list[tuple[str, bool]]:
    profile = source.profile_url.rstrip("/")
    candidates = [(profile, False)]
    if source.platform == "instagram" and "/stories" not in profile.lower():
        candidates.append((f"{profile}/stories", True))
    if source.platform == "facebook" and "/stories" not in profile.lower():
        candidates.append((f"{profile}/stories", True))
    return candidates


def _build_discovery_options(platform: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "impersonate": ImpersonateTarget.from_str("chrome"),
        "extract_flat": True,
        "skip_download": True,
        "playlistend": WATCHER_MAX_DISCOVERED_POSTS,
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": min(DOWNLOAD_TIMEOUT, WATCHER_DISCOVERY_TIMEOUT_SECONDS),
        "windowsfilenames": True,
    }
    cookie_file = _cookie_file(platform)
    if cookie_file is not None:
        options["cookiefile"] = str(cookie_file)
    return options


def _is_post_url(platform: str, url: str) -> bool:
    lowered = url.lower()
    if platform == "tiktok":
        return "/video/" in lowered or "/photo/" in lowered
    if platform == "instagram":
        return any(token in lowered for token in ("/p/", "/reel/", "/tv/", "/stories/"))
    if platform == "facebook":
        return any(
            token in lowered
            for token in (
                "/videos/",
                "/reel/",
                "/posts/",
                "/stories/",
                "/story.php",
                "/permalink.php",
            )
        )
    return False


def _discover_sync(source: WatcherSource) -> list[DiscoveredPost]:
    found: list[DiscoveredPost] = []
    seen: set[str] = set()
    with _DISCOVERY_LOCK:
        for candidate_url, is_story in _profile_candidates(source):
            try:
                with YoutubeDL(_build_discovery_options(source.platform)) as ydl:
                    info = ydl.extract_info(candidate_url, download=False)
            except Exception as error:
                logger.warning(
                    "No se pudo revisar %s (%s): %s",
                    source.profile_url,
                    source.platform,
                    error,
                )
                continue

            entries = info.get("entries") if isinstance(info, dict) else None
            candidates = entries if entries else [info]
            for entry in candidates or []:
                if not isinstance(entry, dict):
                    continue
                url = str(
                    entry.get("webpage_url")
                    or entry.get("original_url")
                    or entry.get("url")
                    or ""
                ).strip()
                if not url or not _is_post_url(source.platform, url):
                    continue
                key = str(entry.get("id") or url).strip()
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    DiscoveredPost(
                        key=key,
                        url=url,
                        title=str(entry.get("title") or "").strip(),
                        is_story=is_story or "/stories/" in url.lower(),
                    )
                )
                if len(found) >= WATCHER_MAX_DISCOVERED_POSTS:
                    return found
    return found


async def discover(source: WatcherSource) -> list[DiscoveredPost]:
    return await asyncio.to_thread(_discover_sync, source)


async def download_post(source: WatcherSource, post: DiscoveredPost) -> DownloadedPost:
    if source.platform == "tiktok":
        result = await _TIKWM.download_async(post.url)
        if not result.success or result.file_path is None:
            raise RuntimeError(result.message)
        return DownloadedPost("tiktok", result.title or post.title, (result.file_path,))

    if source.platform == "instagram":
        if "/stories/" in post.url.lower():
            files, title = await asyncio.to_thread(
                _download_generic_sync,
                post.url,
                "instagram",
                INSTAGRAM_DOWNLOADS,
            )
            return DownloadedPost("instagram", title or post.title or "Instagram", tuple(files))
        url = normalize_instagram_url(post.url) or post.url
        result = await download_instagram_media(url)
        if not result.success or not result.files:
            raise RuntimeError(result.message)
        return DownloadedPost("instagram", post.title or "Instagram", tuple(result.files))

    if source.platform == "facebook":
        files, title = await asyncio.to_thread(_download_facebook_sync, post.url)
        return DownloadedPost("facebook", title or post.title or "Facebook", tuple(files))

    raise RuntimeError(f"Plataforma no activada: {source.platform}")


def _download_facebook_sync(url: str) -> tuple[list[Path], str]:
    return _download_generic_sync(url, "facebook", FACEBOOK_DOWNLOADS)


def _download_generic_sync(
    url: str,
    platform: str,
    destination: Path,
) -> tuple[list[Path], str]:
    destination.mkdir(parents=True, exist_ok=True)
    options = {
        **_build_discovery_options(platform),
        "extract_flat": False,
        "format": "bestvideo*+bestaudio/best",
        "outtmpl": str(destination / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
    }
    options["cookiefile"] = str(_cookie_file(platform)) if _cookie_file(platform) else options.get("cookiefile")
    if not options.get("cookiefile"):
        options.pop("cookiefile", None)
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        if not isinstance(info, dict):
            raise RuntimeError(f"{platform.title()} no devolvió información descargable.")
        path_value = info.get("filepath") or info.get("_filename")
        if path_value:
            path = Path(path_value)
            if path.is_file():
                return [path], str(info.get("title") or "")
        prepared = Path(ydl.prepare_filename(info))
        for candidate in (prepared, prepared.with_suffix(".mp4"), prepared.with_suffix(".mkv")):
            if candidate.is_file():
                return [candidate], str(info.get("title") or "")
    raise FileNotFoundError(f"{platform.title()} terminó sin producir un archivo.")
