from __future__ import annotations

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_USERS, MAX_TELEGRAM_FILE_SIZE
from engines.tiktok.ytdlp import TikTokYTDLPEngine


logger = logging.getLogger(__name__)
tiktok_engine = TikTokYTDLPEngine()


def _is_authorized(update: Update) -> bool:
    """Permite a todos si la lista está vacía; si no, valida el ID."""

    if not ALLOWED_USERS:
        return True

    user = update.effective_user
    return user is not None and user.id in ALLOWED_USERS


async def _reject_unauthorized(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ No tienes autorización para usar este bot."
        )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    if update.message is None:
        return

    await update.message.reply_text(
        "✅ MediaLab está en línea.\n\n"
        "Envíame un enlace compatible.\n\n"
        "Plataforma disponible actualmente:\n"
        "• TikTok"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    if update.message is None:
        return

    await update.message.reply_text(
        "📥 MediaLab Downloader\n\n"
        "Envía directamente un enlace compatible.\n\n"
        "Plataformas disponibles actualmente:\n"
        "• TikTok\n\n"
        "Comandos:\n"
        "/start - Iniciar MediaLab\n"
        "/help - Mostrar esta ayuda"
    )


async def send_video(
    update: Update,
    video_path: Path,
) -> None:
    if update.message is None:
        return

    if not video_path.is_file():
        raise FileNotFoundError(f"No se encontró el video: {video_path}")

    file_size = video_path.stat().st_size

    if file_size > MAX_TELEGRAM_FILE_SIZE:
        await update.message.reply_text(
            "⚠️ El video fue descargado en la computadora, "
            "pero supera el límite configurado para enviarlo.\n\n"
            f"Archivo:\n{video_path}"
        )
        return

    with video_path.open("rb") as video:
        await update.message.reply_video(
            video=video,
            caption="✅ Procesado por MediaLab",
            supports_streaming=True,
            read_timeout=300,
            write_timeout=300,
            connect_timeout=60,
            pool_timeout=60,
        )


async def process_tiktok(
    update: Update,
    url: str,
) -> None:
    if update.message is None:
        return

    status = await update.message.reply_text(
        "⏳ Descargando con yt-dlp..."
    )

    result = await tiktok_engine.download_async(url)

    if not result.success or result.file_path is None:
        if result.error:
            logger.error("Error del motor yt-dlp: %s", result.error)

        await status.edit_text(f"❌ {result.message}")
        return

    await status.edit_text("📤 Descarga terminada. Enviando video...")

    try:
        await send_video(update, result.file_path)
    except Exception:
        logger.exception("Telegram no pudo enviar el video.")
        await status.edit_text(
            "❌ El video se descargó, pero Telegram no pudo enviarlo."
        )
        return

    try:
        await status.delete()
    except Exception:
        logger.warning("No se pudo borrar el mensaje de estado.")


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    if update.message is None or update.message.text is None:
        return

    url = update.message.text.strip()

    if "tiktok.com" in url.lower():
        await process_tiktok(update, url)
        return

    await update.message.reply_text(
        "❌ Plataforma aún no soportada.\n\n"
        "Actualmente puedes enviar enlaces de TikTok."
    )
