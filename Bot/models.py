from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class DownloadResult:
    """
    Resultado estándar de cualquier motor de descarga.

    Todos los motores (TikWM, yt-dlp, Instagram,
    Facebook, YouTube, etc.) deberán regresar
    SIEMPRE este mismo objeto.
    """

    success: bool

    message: str

    file_path: Optional[Path] = None

    title: str = ""

    author: str = ""

    platform: str = ""

    engine: str = ""

    file_size: int = 0

    video_id: str = ""

    url: str = ""

    error: str = ""

    thumbnail: str = ""