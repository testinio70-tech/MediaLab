from __future__ import annotations

import logging
import secrets
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    Update,
)
from telegram.ext import ContextTypes

from config import (
    ALLOWED_USERS,
    ENGINE_SELECTION_TTL,
    MAX_PENDING_SELECTIONS_PER_USER,
    MAX_TELEGRAM_FILE_SIZE,
    MAX_TELEGRAM_PHOTO_SIZE,
    SEND_TIMEOUT,
)
from engines.tiktok.tikwm import TikWMEngine
from engines.tiktok.ytdlp import TikTokYTDLPEngine
from media_info import enrich_download_result, format_result_details
from models import DownloadResult
from services.file_cleanup import delete_sent_file, delete_sent_files
from services.tiktok_photos import (
    PhotoDownloadResult,
    download_tiktok_photos,
    is_tiktok_photo_url,
)
from services.tiktok_urls import resolve_tiktok_url
from utils import (
    detect_platform,
    extract_first_url,
    format_file_size,
)


logger = logging.getLogger(__name__)

_CALLBACK_PREFIX = "tikeng"
_PENDING_KEY = "pending_tiktok_requests"
_MEDIA_GROUP_SIZE = 10

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
        "Contenido disponible:\n"
        "• Videos con TikWM Original o yt-dlp\n"
        "• Carruseles de fotos con gallery-dl"
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
        "Envía un enlace de TikTok.\n\n"
        "🎬 Videos:\n"
        "• TikWM Original\n"
        "• yt-dlp\n\n"
        "📸 Publicaciones /photo/:\n"
        "• gallery-dl descarga todas las imágenes\n"
        "• Telegram las recibe como álbumes\n\n"
        "Los archivos enviados correctamente se eliminan de la PC.\n"
        "Los envíos fallidos se conservan temporalmente.\n\n"
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


async def send_photo_album(
    message: Message,
    result: PhotoDownloadResult,
) -> bool:
    """Envía una o varias fotos y confirma solo si todos los lotes terminan."""

    files = [path for path in result.files if path.is_file()]
    if not files:
        raise FileNotFoundError("No se encontraron imágenes para enviar.")

    oversized = [
        path
        for path in files
        if path.stat().st_size > MAX_TELEGRAM_PHOTO_SIZE
    ]
    if oversized:
        names = ", ".join(path.name for path in oversized[:3])
        raise ValueError(
            "Una o más imágenes superan el límite de 10 MB para fotos: "
            f"{names}"
        )

    caption = _format_photo_details(result, heading="✅ Procesado por MediaLab")

    if len(files) == 1:
        with files[0].open("rb") as photo:
            await message.reply_photo(
                photo=photo,
                caption=caption,
                read_timeout=SEND_TIMEOUT,
                write_timeout=SEND_TIMEOUT,
                connect_timeout=60,
                pool_timeout=60,
            )
        return True

    for start_index in range(0, len(files), _MEDIA_GROUP_SIZE):
        batch = files[start_index : start_index + _MEDIA_GROUP_SIZE]

        with ExitStack() as stack:
            media: list[InputMediaPhoto] = []

            for index, path in enumerate(batch):
                photo = stack.enter_context(path.open("rb"))
                first_photo = start_index == 0 and index == 0
                media.append(
                    InputMediaPhoto(
                        media=photo,
                        caption=caption if first_photo else None,
                    )
                )

            await message.reply_media_group(
                media=media,
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
        url = await resolve_tiktok_url(url)

    if platform == "tiktok" and is_tiktok_photo_url(url):
        await _handle_tiktok_photo_post(message, url)
        return

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
            "🎬 Enlace de video de TikTok detectado.\n\n"
            "Selecciona el motor de descarga:",
            reply_markup=keyboard,
        )
        return

    await message.reply_text(
        "❌ Plataforma aún no soportada.\n\n"
        "Actualmente puedes enviar enlaces de TikTok."
    )


async def _handle_tiktok_photo_post(
    message: Message,
    url: str,
) -> None:
    status_message = await message.reply_text(
        "📸 Publicación fotográfica de TikTok detectada.\n\n"
        "⏳ Descargando imágenes con gallery-dl..."
    )

    result = await download_tiktok_photos(url)

    if not result.success:
        if result.error:
            logger.error("Error de gallery-dl: %s", result.error)

        preserved = ""
        if result.working_directory is not None:
            preserved = (
                "\n\n📁 Los archivos parciales se conservaron temporalmente en:\n"
                f"{result.working_directory}"
            )

        await status_message.edit_text(
            f"❌ {result.message}{preserved}"
        )
        return

    await status_message.edit_text(
        f"{_format_photo_details(result, heading='📥 Descarga terminada')}\n\n"
        "📤 Enviando álbum a Telegram..."
    )

    try:
        sent = await send_photo_album(message, result)
    except Exception:
        logger.exception("Telegram no pudo enviar el álbum de fotos.")
        location = result.working_directory or result.files[0].parent
        await status_message.edit_text(
            "❌ Las imágenes se descargaron, pero Telegram no pudo enviarlas.\n\n"
            f"{_format_photo_details(result)}\n\n"
            "📁 Se conservaron temporalmente en:\n"
            f"{location}"
        )
        return

    if not sent:
        return

    completion_details = _format_photo_details(
        result,
        heading="✅ Álbum enviado",
    )
    deleted, failed = delete_sent_files(result.files)
    cleanup_line = (
        f"🧹 Archivos eliminados: {deleted}"
        if failed == 0
        else f"⚠️ Eliminados: {deleted} · Pendientes: {failed}"
    )

    await status_message.edit_text(
        f"{completion_details}\n\n"
        f"{cleanup_line}"
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

    url = await resolve_tiktok_url(url)

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
            f"{format_result_details(result)}\n\n"
            f"📁 Archivo conservado:\n{result.file_path}"
        )
        return

    if sent:
        deleted = delete_sent_file(result.file_path)
        cleanup_line = (
            "🧹 Archivo local eliminado."
            if deleted
            else "⚠️ El archivo local no pudo eliminarse; revisa el log."
        )

        await query.edit_message_text(
            f"{format_result_details(result, heading='✅ Proceso completado')}\n\n"
            f"{cleanup_line}"
        )
    else:
        await query.edit_message_text(
            "⚠️ Descarga completada, pero el archivo no se envió "
            "por superar el límite configurado.\n\n"
            f"{format_result_details(result)}"
        )


def _format_photo_details(
    result: PhotoDownloadResult,
    heading: str = "📸 Publicación fotográfica",
) -> str:
    return (
        f"{heading}\n\n"
        f"🖼️ Imágenes: {len(result.files)}\n"
        f"📦 Peso total: {format_file_size(result.total_size)}\n"
        "⚙️ Motor: gallery-dl"
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
