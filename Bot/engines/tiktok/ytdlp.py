"""
Compatibilidad temporal con la importación antigua de MediaLab.

handlers.py todavía importa TikTokYTDLPEngine desde este módulo. La clase
conserva ese nombre para no modificar ningún archivo fuera del motor TikTok,
pero toda la descarga se realiza con TikWM Original Downloader y no con yt-dlp.
"""

from __future__ import annotations

from engines.tiktok.tikwm import TikWMEngine


class TikTokYTDLPEngine(TikWMEngine):
    """Alias compatible que ejecuta internamente el motor TikWM Original."""
