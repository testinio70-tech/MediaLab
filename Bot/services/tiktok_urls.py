from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import requests


logger = logging.getLogger(__name__)

_TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}

_SHORT_HOSTS = {
    "vm.tiktok.com",
    "vt.tiktok.com",
}

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


def is_tiktok_short_url(url: str) -> bool:
    """Detecta formatos abreviados de TikTok que requieren redirección."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    if host in _SHORT_HOSTS:
        return True

    return host in _TIKTOK_HOSTS and path.startswith("/t/")


async def resolve_tiktok_url(url: str) -> str:
    """
    Resuelve enlaces cortos de TikTok sin bloquear el loop de Telegram.

    Si la resolución falla, devuelve el enlace original para mantener
    el comportamiento anterior del bot.
    """
    clean_url = url.strip()

    if not is_tiktok_short_url(clean_url):
        return clean_url

    return await asyncio.to_thread(_resolve_tiktok_url_sync, clean_url)


def _resolve_tiktok_url_sync(url: str) -> str:
    try:
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=30,
            headers=_REQUEST_HEADERS,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.warning(
            "No se pudo resolver el enlace corto de TikTok %s: %s",
            url,
            error,
        )
        return url

    resolved_url = response.url.strip()
    if not _is_valid_tiktok_media_url(resolved_url):
        logger.warning(
            "El enlace corto de TikTok terminó en una URL no clasificable: "
            "%s -> %s",
            url,
            resolved_url,
        )
        return url

    logger.info(
        "Enlace corto de TikTok resuelto: %s -> %s",
        url,
        resolved_url,
    )
    return resolved_url


def _is_valid_tiktok_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    return (
        host in _TIKTOK_HOSTS
        and ("/photo/" in path or "/video/" in path)
    )
