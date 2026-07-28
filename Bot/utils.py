from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final
from urllib.parse import urlparse


# ==========================================================
# Plataformas y dominios compatibles
# ==========================================================

PLATFORM_DOMAINS: Final[dict[str, tuple[str, ...]]] = {
    "tiktok": (
        "tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    ),
    "instagram": (
        "instagram.com",
        "instagr.am",
    ),
    "youtube": (
        "youtube.com",
        "youtu.be",
    ),
    "facebook": (
        "facebook.com",
        "fb.watch",
    ),
    "x": (
        "x.com",
        "twitter.com",
    ),
}

URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s<>\"']+",
    flags=re.IGNORECASE,
)

WINDOWS_RESERVED_NAMES: Final[set[str]] = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

INVALID_FILENAME_CHARS: Final[re.Pattern[str]] = re.compile(
    r'[<>:"/\\|?*\x00-\x1F]'
)


# ==========================================================
# URLs y plataformas
# ==========================================================


def extract_first_url(text: str) -> str | None:
    """Extrae el primer enlace HTTP o HTTPS contenido en un texto."""

    if not text:
        return None

    match = URL_PATTERN.search(text.strip())

    if match is None:
        return None

    return match.group(0).rstrip(".,;:!?)]}")


def normalize_url(url: str) -> str:
    """Limpia y valida superficialmente una URL HTTP o HTTPS."""

    cleaned_url = url.strip().strip("<>\"'")
    parsed = urlparse(cleaned_url)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("La URL debe comenzar con http:// o https://.")

    if not parsed.netloc:
        raise ValueError("La URL no contiene un dominio válido.")

    return cleaned_url


def detect_platform(url: str) -> str | None:
    """Devuelve la plataforma correspondiente al dominio de la URL."""

    try:
        normalized_url = normalize_url(url)
    except ValueError:
        return None

    hostname = (urlparse(normalized_url).hostname or "").lower()
    hostname = hostname.removeprefix("www.")

    for platform, domains in PLATFORM_DOMAINS.items():
        if any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in domains
        ):
            return platform

    return None


def is_supported_url(url: str) -> bool:
    """Indica si MediaLab reconoce la plataforma de la URL."""

    return detect_platform(url) is not None


# ==========================================================
# Archivos y carpetas
# ==========================================================


def ensure_directory(directory: Path) -> Path:
    """Crea una carpeta y sus padres si todavía no existen."""

    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_filename(
    value: str,
    default: str = "media",
    max_length: int = 120,
) -> str:
    """Convierte un texto en un nombre de archivo seguro para Windows."""

    cleaned = INVALID_FILENAME_CHARS.sub("_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")

    if not cleaned:
        cleaned = default

    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    cleaned = cleaned[:max_length].rstrip(" ._")

    return cleaned or default


def unique_file_path(
    directory: Path,
    stem: str,
    suffix: str,
) -> Path:
    """Genera una ruta que no sobrescriba un archivo existente."""

    ensure_directory(directory)

    safe_stem = sanitize_filename(stem)
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"

    candidate = directory / f"{safe_stem}{safe_suffix}"

    if not candidate.exists():
        return candidate

    counter = 2

    while True:
        candidate = directory / f"{safe_stem} ({counter}){safe_suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def delete_file_safely(file_path: Path | None) -> bool:
    """Elimina un archivo sin lanzar error cuando ya no existe."""

    if file_path is None:
        return False

    try:
        file_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def format_file_size(size_bytes: int) -> str:
    """Convierte bytes a una representación legible."""

    if size_bytes < 0:
        raise ValueError("El tamaño no puede ser negativo.")

    size = float(size_bytes)

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            decimals = 0 if unit == "B" else 2
            return f"{size:.{decimals}f} {unit}"

        size /= 1024

    return f"{size_bytes} B"


# ==========================================================
# Registro de actividad
# ==========================================================


def configure_logging(
    log_directory: Path,
    log_filename: str = "medialab.log",
    level: int = logging.INFO,
) -> Path:
    """Configura el registro en archivo y consola para MediaLab."""

    ensure_directory(log_directory)
    log_path = log_directory / sanitize_filename(
        log_filename,
        default="medialab.log",
    )

    logging.basicConfig(
        level=level,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=[
            logging.FileHandler(
                log_path,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
        force=True,
    )

    return log_path
