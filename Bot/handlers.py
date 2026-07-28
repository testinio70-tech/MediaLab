from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.ext import ContextTypes

from config import (
    ALLOWED_USERS,
    ENGINE_SELECTION_TTL,
    MAX_PENDING_SELECTIONS_PER_USER,
    MAX_TELEGRAM_FILE_SIZE,
    SEND_TIMEOUT,
)
from engines.tiktok.tikwm import TikWMEngine
from engines.tiktok.ytdlp import TikTokYTDLPEngine
from media_info import enrich_download_result, format_result_details
from models import DownloadResult
from utils import detect_platform, extract_first_url


logger = logging.getLogger(__name__)

_CALLBACK_PREFIX = "tikeng"
_PENDING_KEY = "pending_tiktok_requests"

TIKTOK_ENGINES = {
    "tikwm": TikWMEngine(),
    "ytdlp": TikTokYTDLPEngine(),
}


def _is_authorized(update: Update) -> bool:
    """Permite a todos si la lista está vacía; si no, valida el ID."""

    if not ALLOWED_USERS:
        return True

    user = update.effective_user
    return user is not None and user.id in ALLOWED_USERS


async def _reject_unauthorized(update: Update) -> None:
    if update.callback_query is not None:
        await update.callback_query.answer(
            "No tienes autorización para usar este bot.",
            show_alert=True,
        )
        return

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

    if update.effective_message is None:
        return

    await update.effective_message.reply_text(
        "✅ MediaLab está en línea.\n\n"
        "Envíame un enlace compatible.\n\n"
        "Plataforma disponible actualmente:\n"
        "• TikTok\n\n"
        "Motores disponibles:\n"
        "• TikWM Original\n"
        "• yt-dlp"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    if update.effective_message is None:
        return

    await update.effective_message.reply_text(
        "📥 MediaLab Downloader\n\n"
        "Envía directamente un enlace de TikTok y selecciona "
        "el motor de descarga.\n\n"
        "Motores disponibles:\n"
        "• TikWM Original\n"
        "• yt-dlp\n\n"
        "Al terminar verás el peso total y la resolución del archivo.\n\n"
        "Comandos:\n"
        "/start - Iniciar MediaLab\n"
        "/help - Mostrar esta ayuda"
    )


async def send_video(
    message: Message,
    result: DownloadResult,
) -> bool:
    """Envía el video y devuelve True cuando Telegram lo acepta."""

    video_path = result.file_path
    if video_path is None or not video_path.is_file():
        raise FileNotFoundError(f"No se encontró el video: {video_path}")

    result.file_size = video_path.stat().st_size
    details = format_result_details(
        result,
        heading="✅ Procesado por MediaLab",
    )

    if result.file_size > MAX_TELEGRAM_FILE_SIZE:
        await message.reply_text(
            "⚠️ El video fue descargado en la computadora, "
            "pero supera el límite configurado para enviarlo.\n\n"
            f"{details}\n\n"
            f"📁 Archivo:\n{video_path}"
        )
        return False

    with video_path.open("rb") as video:
        await message.reply_video(
            video=video,
            caption=details,
            supports_streaming=True,
            read_timeout=SEND_TIMEOUT,
            write_timeout=SEND_TIMEOUT,
            connect_timeout=60,
            pool_timeout=60,
        )

    return True


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    if message is None or message.text is None:
        return

    url = extract_first_url(message.text)
    if url is None:
        await message.reply_text(
            "❌ No encontré un enlace HTTP o HTTPS válido en el mensaje."
        )
        return

    platform = detect_platform(url)

    if platform == "tiktok":
        request_id = _store_pending_tiktok_url(context, url)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🌐 TikWM Original",
                        callback_data=(
                            f"{_CALLBACK_PREFIX}:{request_id}:tikwm"
                        ),
                    ),
                    InlineKeyboardButton(
                        "🛠️ yt-dlp",
                        callback_data=(
                            f"{_CALLBACK_PREFIX}:{request_id}:ytdlp"
                        ),
                    ),
                ]
            ]
        )

        await message.reply_text(
            "🎬 Enlace de TikTok detectado.\n\n"
            "Selecciona el motor de descarga:",
            reply_markup=keyboard,
        )
        return

    await message.reply_text(
        "❌ Plataforma aún no soportada.\n\n"
        "Actualmente puedes enviar enlaces de TikTok."
    )


async def handle_tiktok_engine_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Procesa el botón elegido y ejecuta solo ese motor."""

    query = update.callback_query
    if query is None:
        return

    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    await query.answer()

    parsed = _parse_callback_data(query.data)
    if parsed is None:
        await query.edit_message_text(
            "❌ La selección del motor no es válida."
        )
        return

    request_id, engine_key = parsed
    pending = _pending_requests(context)
    request_data = pending.pop(request_id, None)

    if request_data is None:
        await query.edit_message_text(
            "⌛ Este selector ya fue utilizado o expiró.\n\n"
            "Envía nuevamente el enlace de TikTok."
        )
        return

    created_at = float(request_data.get("created_at") or 0)
    if time.time() - created_at > ENGINE_SELECTION_TTL:
        await query.edit_message_text(
            "⌛ Este selector expiró.\n\n"
            "Envía nuevamente el enlace de TikTok."
        )
        return

    engine = TIKTOK_ENGINES.get(engine_key)
    if engine is None:
        await query.edit_message_text(
            "❌ Ese motor no está disponible."
        )
        return

    url = str(request_data.get("url") or "").strip()
    if not url:
        await query.edit_message_text(
            "❌ No se pudo recuperar el enlace de TikTok."
        )
        return

    await query.edit_message_text(
        f"⏳ Descargando con {engine.name}..."
    )

    result = await engine.download_async(url)

    if not result.success or result.file_path is None:
        if result.error:
            logger.error(
                "Error del motor %s: %s",
                engine.name,
                result.error,
            )

        await query.edit_message_text(
            f"❌ {result.message}\n\n"
            f"⚙️ Motor: {engine.name}"
        )
        return

    enrich_download_result(result)
    download_details = format_result_details(
        result,
        heading="📥 Descarga terminada",
    )

    await query.edit_message_text(
        f"{download_details}\n\n"
        "📤 Enviando video a Telegram..."
    )

    message = query.message
    if message is None:
        logger.error("La consulta de Telegram no contiene un mensaje.")
        return

    try:
        sent = await send_video(message, result)
    except Exception:
        logger.exception("Telegram no pudo enviar el video.")
        await query.edit_message_text(
            "❌ El video se descargó, pero Telegram no pudo enviarlo.\n\n"
            f"{format_result_details(result)}"
        )
        return

    if sent:
        await query.edit_message_text(
            format_result_details(
                result,
                heading="✅ Proceso completado",
            )
        )
    else:
        await query.edit_message_text(
            "⚠️ Descarga completada, pero el archivo no se envió "
            "por superar el límite configurado.\n\n"
            f"{format_result_details(result)}"
        )


def _store_pending_tiktok_url(
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
) -> str:
    pending = _pending_requests(context)
    _remove_expired_requests(pending)

    while len(pending) >= MAX_PENDING_SELECTIONS_PER_USER:
        oldest_request_id = next(iter(pending))
        pending.pop(oldest_request_id, None)

    request_id = secrets.token_hex(6)
    pending[request_id] = {
        "url": url,
        "created_at": time.time(),
    }

    return request_id


def _pending_requests(
    context: ContextTypes.DEFAULT_TYPE,
) -> dict[str, dict[str, Any]]:
    current = context.user_data.get(_PENDING_KEY)

    if isinstance(current, dict):
        return current

    pending: dict[str, dict[str, Any]] = {}
    context.user_data[_PENDING_KEY] = pending
    return pending


def _remove_expired_requests(
    pending: dict[str, dict[str, Any]],
) -> None:
    now = time.time()
    expired_ids = [
        request_id
        for request_id, request_data in pending.items()
        if now - float(request_data.get("created_at") or 0)
        > ENGINE_SELECTION_TTL
    ]

    for request_id in expired_ids:
        pending.pop(request_id, None)


def _parse_callback_data(data: str | None) -> tuple[str, str] | None:
    if not data:
        return None

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
        return None

    request_id, engine_key = parts[1], parts[2]

    if not request_id or engine_key not in TIKTOK_ENGINES:
        return None

    return request_id, engine_key
