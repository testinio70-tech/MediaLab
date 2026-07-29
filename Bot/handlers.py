from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import (
    ALLOWED_USERS,
    APP_VERSION,
    ENGINE_SELECTION_TTL,
    FAST1080_MAX_DURATION_SECONDS,
    FAST1080_MAX_INPUT_BYTES,
    FAST1080_MAX_INPUT_MB,
    INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS,
    MAX_PENDING_SELECTIONS_PER_USER,
    MAX_TELEGRAM_FILE_SIZE,
    MAX_TELEGRAM_PHOTO_SIZE,
    PRIVILEGED_USERS,
    RESTORE_MAX_DURATION_SECONDS,
    RESTORE_MAX_INPUT_BYTES,
    RESTORE_MAX_INPUT_MB,
    SEND_TIMEOUT,
    STATUS_MESSAGE_DELETE_DELAY,
)
from engines.instagram.ytdlp import InstagramYTDLPEngine
from engines.tiktok.tikwm import TikWMEngine
from engines.tiktok.ytdlp import TikTokYTDLPEngine
from media_info import enrich_download_result, format_result_details
from models import DownloadResult
from services.download_queue import DOWNLOAD_QUEUE, DownloadJob, QueueReceipt
from services.enhancement_queue import (
    ENHANCEMENT_QUEUE,
    EnhancementJob,
    QueueReceipt as EnhancementQueueReceipt,
)
from services.file_cleanup import delete_sent_file, delete_sent_files
from services.heartbeat import HEARTBEAT_SERVICE
from services.instagram import (
    InstagramDownloadResult,
    download_instagram_media,
    instagram_content_type,
    is_instagram_single_media_url,
    is_instagram_video_url,
    normalize_instagram_url,
    resolve_instagram_url,
)
from services.tiktok_photos import (
    PhotoDownloadResult,
    download_tiktok_photos,
    is_tiktok_photo_url,
)
from services.tiktok_urls import resolve_tiktok_url
from services.video_fast1080 import FAST1080_ENHANCER
from services.video_restore import (
    RESTORATION_PROFILES,
    VIDEO_RESTORER,
    RestorationProgress,
)
from ui.menus import (
    download_menu,
    enhancement_menu,
    fast1080_prompt,
    feature_menu,
    health_keyboard,
    help_menu,
    help_section,
    main_menu,
    restoration_menu,
    restoration_prompt,
    status_keyboard,
)
from utils import detect_platform, extract_first_url, format_file_size


logger = logging.getLogger(__name__)

_CALLBACK_PREFIX = "tikeng"
_PENDING_KEY = "pending_tiktok_requests"
_MEDIA_GROUP_SIZE = 10

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}

TIKTOK_ENGINES = {
    "tikwm": TikWMEngine(),
    "ytdlp": TikTokYTDLPEngine(),
}
INSTAGRAM_YTDLP = InstagramYTDLPEngine()


# ==========================================================
# Autorización y comandos
# ==========================================================


def _is_authorized(update: Update) -> bool:
    """Permite a todos si la lista está vacía; si no, valida el ID."""
    if not ALLOWED_USERS:
        return True

    user = update.effective_user
    return user is not None and user.id in ALLOWED_USERS


def _is_privileged(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in PRIVILEGED_USERS


async def _reject_unauthorized(update: Update) -> None:
    if update.callback_query is not None:
        await _safe_answer_query(
            update.callback_query,
            text="No tienes autorización para usar este bot.",
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
    await menu_command(update, context)


async def menu_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    if message is None:
        return

    text, keyboard = main_menu(
        APP_VERSION,
        privileged=_is_privileged(update),
    )
    await message.reply_text(text, reply_markup=keyboard)


async def restorevideo_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    if message is None:
        return

    context.user_data.pop("menu_mode", None)
    context.user_data.pop("restore_preset", None)
    text, keyboard = restoration_menu()
    await message.reply_text(text, reply_markup=keyboard)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    if message is None:
        return

    text, keyboard = help_menu()
    await message.reply_text(text, reply_markup=keyboard)


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    await message.reply_text(
        await _build_status_text(user.id),
        reply_markup=status_keyboard(),
    )


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    if message is None:
        return

    pending = _pending_requests(context)
    pending_count = len(pending)
    pending.clear()
    context.user_data.pop("menu_mode", None)
    context.user_data.pop("restore_preset", None)

    if pending_count:
        await message.reply_text(
            "✅ Selección pendiente cancelada.\n\n"
            "Las descargas que ya entraron a la cola continúan normalmente."
        )
        return

    await message.reply_text(
        "ℹ️ No tenías una selección pendiente.\n\n"
        "Por seguridad, /cancel todavía no interrumpe una descarga activa."
    )


async def health_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    if message is None:
        return

    if not _is_privileged(update):
        await message.reply_text(
            "⛔ /health está disponible únicamente para superusuarios."
        )
        return

    await message.reply_text(
        await _build_health_text(),
        reply_markup=health_keyboard(),
    )


async def handle_navigation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    data = str(query.data or "")

    if data != "menu:feature:fast1080" and not data.startswith(
        "restore:preset:"
    ):
        context.user_data.pop("menu_mode", None)
        context.user_data.pop("restore_preset", None)

    if data == "menu:main":
        text, keyboard = main_menu(
            APP_VERSION,
            privileged=_is_privileged(update),
        )
    elif data == "menu:download":
        text, keyboard = download_menu()
    elif data == "menu:enhance":
        text, keyboard = enhancement_menu()
    elif data == "menu:restore":
        text, keyboard = restoration_menu()
    elif data == "menu:status":
        text = await _build_status_text(user.id)
        keyboard = status_keyboard()
    elif data == "menu:help" or data == "help:main":
        text, keyboard = help_menu()
    elif data == "menu:health":
        if not _is_privileged(update):
            await _safe_answer_query(
                query,
                text="Solo disponible para superusuarios.",
                show_alert=True,
            )
            return
        text = await _build_health_text()
        keyboard = health_keyboard()
    elif data == "menu:feature:fast1080":
        context.user_data["menu_mode"] = "fast1080"
        text, keyboard = fast1080_prompt(
            FAST1080_MAX_INPUT_MB,
            FAST1080_MAX_DURATION_SECONDS,
        )
    elif data.startswith("restore:preset:"):
        preset = data.rsplit(":", 1)[-1]
        profile = RESTORATION_PROFILES.get(preset)
        if profile is None:
            await _safe_answer_query(
                query,
                text="Modo de restauración desconocido.",
                show_alert=True,
            )
            return
        context.user_data["menu_mode"] = "restorevideo"
        context.user_data["restore_preset"] = preset
        text, keyboard = restoration_prompt(
            profile.label,
            RESTORE_MAX_INPUT_MB,
            RESTORE_MAX_DURATION_SECONDS,
        )
    elif data.startswith("menu:feature:"):
        text, keyboard = feature_menu(data.rsplit(":", 1)[-1])
    elif data.startswith("help:"):
        text, keyboard = help_section(data.split(":", 1)[1])
    else:
        text, keyboard = main_menu(
            APP_VERSION,
            privileged=_is_privileged(update),
        )

    if not await _safe_answer_query(query):
        return

    await query.edit_message_text(text, reply_markup=keyboard)


async def _build_status_text(user_id: int) -> str:
    active, waiting = await DOWNLOAD_QUEUE.snapshot()
    fast_active, fast_waiting = await ENHANCEMENT_QUEUE.snapshot()
    download_status = await DOWNLOAD_QUEUE.user_snapshot(user_id)
    fast_status = await ENHANCEMENT_QUEUE.user_snapshot(user_id)

    personal_lines: list[str] = []

    if download_status.active:
        personal_lines.append(
            "🟢 Descarga activa\n"
            f"🌐 Plataforma: {download_status.platform}\n"
            f"⚙️ Motor: {download_status.label}"
        )
    elif download_status.waiting:
        personal_lines.append(
            "🕒 Descarga esperando\n"
            f"📍 Posición: {download_status.position}\n"
            f"🌐 Plataforma: {download_status.platform}\n"
            f"⚙️ Motor: {download_status.label}"
        )

    if fast_status.active:
        personal_lines.append(
            "✨ Mejora activa\n"
            f"⚙️ Motor: {fast_status.label}"
        )
    elif fast_status.waiting:
        personal_lines.append(
            "🕒 Mejora esperando\n"
            f"📍 Posición: {fast_status.position}\n"
            f"⚙️ Motor: {fast_status.label}"
        )

    personal = (
        "\n\n".join(personal_lines)
        if personal_lines
        else "⚪ No tienes solicitudes activas ni pendientes."
    )

    return (
        "📊 Estado de MediaLab\n\n"
        "Cola de descargas:\n"
        f"• Activas: {active}\n"
        f"• Esperando: {waiting}\n\n"
        "Cola de mejoras:\n"
        f"• Activas: {fast_active}\n"
        f"• Esperando: {fast_waiting}\n\n"
        f"{personal}"
    )


async def _build_health_text() -> str:
    active, waiting = await DOWNLOAD_QUEUE.snapshot()
    fast_active, fast_waiting = await ENHANCEMENT_QUEUE.snapshot()
    heartbeat = "activo" if HEARTBEAT_SERVICE.running else "detenido"

    return (
        "🩺 Estado técnico de MediaLab\n\n"
        f"Versión: {APP_VERSION}\n"
        f"Tiempo activo: {_format_duration(HEARTBEAT_SERVICE.uptime_seconds)}\n"
        f"Heartbeat: {heartbeat}\n"
        f"Descargas activas: {active}\n"
        f"Descargas esperando: {waiting}\n"
        f"Mejoras activas: {fast_active}\n"
        f"Mejoras esperando: {fast_waiting}\n"
        f"FFmpeg Fast1080: {FAST1080_ENHANCER.availability_text()}\n\n"
        f"Restauración integral: {VIDEO_RESTORER.availability_text()}\n\n"
        "El supervisor externo revisará el heartbeat cada 5 minutos."
    )


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"


# ==========================================================
# Envío de archivos
# ==========================================================


async def send_video(
    message: Message,
    result: DownloadResult,
    *,
    reply_to_message: bool = True,
) -> bool:
    """Envía un video y devuelve True cuando Telegram lo acepta."""
    video_path = result.file_path
    if video_path is None or not video_path.is_file():
        raise FileNotFoundError(f"No se encontró el video: {video_path}")

    result.file_size = video_path.stat().st_size
    if result.file_size > MAX_TELEGRAM_FILE_SIZE:
        return False

    details = format_result_details(
        result,
        heading="✅ Procesado por MediaLab",
    )

    with video_path.open("rb") as video:
        send_options = {
            "video": video,
            "caption": details,
            "supports_streaming": True,
            "read_timeout": SEND_TIMEOUT,
            "write_timeout": SEND_TIMEOUT,
            "connect_timeout": 60,
            "pool_timeout": 60,
        }
        if reply_to_message:
            await message.reply_video(**send_options)
        else:
            await message.get_bot().send_video(
                chat_id=message.chat_id,
                **send_options,
            )

    return True


async def send_photo_album(
    message: Message,
    result: PhotoDownloadResult,
) -> bool:
    """Envía una o varias fotos y confirma si todos los lotes terminan."""
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
            "Una o más imágenes superan el límite para fotos: "
            f"{names}"
        )

    caption = _format_photo_details(
        result,
        heading="✅ Procesado por MediaLab",
    )

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

        if len(batch) == 1:
            with batch[0].open("rb") as photo:
                await message.reply_photo(
                    photo=photo,
                    caption=caption if start_index == 0 else None,
                    read_timeout=SEND_TIMEOUT,
                    write_timeout=SEND_TIMEOUT,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            continue

        with ExitStack() as stack:
            media: list[InputMediaPhoto] = []
            for index, path in enumerate(batch):
                photo = stack.enter_context(path.open("rb"))
                first_item = start_index == 0 and index == 0
                media.append(
                    InputMediaPhoto(
                        media=photo,
                        caption=caption if first_item else None,
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


async def send_instagram_media(
    message: Message,
    result: InstagramDownloadResult,
) -> bool:
    """Envía Instagram como originales o como álbum visual configurable."""
    files = [path for path in result.files if path.is_file()]
    if not files:
        raise FileNotFoundError("No se encontraron archivos de Instagram.")

    if any(path.stat().st_size > MAX_TELEGRAM_FILE_SIZE for path in files):
        return False

    caption = _format_instagram_details(result)

    if INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS:
        return await _send_files_as_documents(message, files, caption)

    return await _send_files_as_visual_media(message, files, caption)


async def _send_files_as_documents(
    message: Message,
    files: list[Path],
    caption: str,
) -> bool:
    if len(files) == 1:
        with files[0].open("rb") as document:
            await message.reply_document(
                document=document,
                caption=caption,
                read_timeout=SEND_TIMEOUT,
                write_timeout=SEND_TIMEOUT,
                connect_timeout=60,
                pool_timeout=60,
            )
        return True

    for start_index in range(0, len(files), _MEDIA_GROUP_SIZE):
        batch = files[start_index : start_index + _MEDIA_GROUP_SIZE]

        if len(batch) == 1:
            with batch[0].open("rb") as document:
                await message.reply_document(
                    document=document,
                    caption=caption if start_index == 0 else None,
                    read_timeout=SEND_TIMEOUT,
                    write_timeout=SEND_TIMEOUT,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            continue

        with ExitStack() as stack:
            media: list[InputMediaDocument] = []
            for index, path in enumerate(batch):
                document = stack.enter_context(path.open("rb"))
                first_item = start_index == 0 and index == 0
                media.append(
                    InputMediaDocument(
                        media=document,
                        caption=caption if first_item else None,
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


async def _send_files_as_visual_media(
    message: Message,
    files: list[Path],
    caption: str,
) -> bool:
    if any(
        path.suffix.lower() in _IMAGE_EXTENSIONS
        and path.stat().st_size > MAX_TELEGRAM_PHOTO_SIZE
        for path in files
    ):
        return await _send_files_as_documents(message, files, caption)

    if len(files) == 1:
        path = files[0]
        suffix = path.suffix.lower()

        if suffix in _IMAGE_EXTENSIONS:
            with path.open("rb") as photo:
                await message.reply_photo(
                    photo=photo,
                    caption=caption,
                    read_timeout=SEND_TIMEOUT,
                    write_timeout=SEND_TIMEOUT,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            return True

        if suffix in _VIDEO_EXTENSIONS:
            with path.open("rb") as video:
                await message.reply_video(
                    video=video,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=SEND_TIMEOUT,
                    write_timeout=SEND_TIMEOUT,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            return True

        return await _send_files_as_documents(message, files, caption)

    for start_index in range(0, len(files), _MEDIA_GROUP_SIZE):
        batch = files[start_index : start_index + _MEDIA_GROUP_SIZE]

        if len(batch) == 1:
            path = batch[0]
            suffix = path.suffix.lower()
            if suffix in _IMAGE_EXTENSIONS:
                with path.open("rb") as photo:
                    await message.reply_photo(
                        photo=photo,
                        caption=caption if start_index == 0 else None,
                        read_timeout=SEND_TIMEOUT,
                        write_timeout=SEND_TIMEOUT,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
                continue
            if suffix in _VIDEO_EXTENSIONS:
                with path.open("rb") as video:
                    await message.reply_video(
                        video=video,
                        caption=caption if start_index == 0 else None,
                        supports_streaming=True,
                        read_timeout=SEND_TIMEOUT,
                        write_timeout=SEND_TIMEOUT,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
                continue
            return await _send_files_as_documents(message, files, caption)

        with ExitStack() as stack:
            media: list[InputMediaPhoto | InputMediaVideo] = []
            for index, path in enumerate(batch):
                file_object = stack.enter_context(path.open("rb"))
                first_item = start_index == 0 and index == 0
                item_caption = caption if first_item else None

                if path.suffix.lower() in _IMAGE_EXTENSIONS:
                    media.append(
                        InputMediaPhoto(
                            media=file_object,
                            caption=item_caption,
                        )
                    )
                elif path.suffix.lower() in _VIDEO_EXTENSIONS:
                    media.append(
                        InputMediaVideo(
                            media=file_object,
                            caption=item_caption,
                            supports_streaming=True,
                        )
                    )
                else:
                    return await _send_files_as_documents(
                        message,
                        files,
                        caption,
                    )

            await message.reply_media_group(
                media=media,
                read_timeout=SEND_TIMEOUT,
                write_timeout=SEND_TIMEOUT,
                connect_timeout=60,
                pool_timeout=60,
            )

    return True


# ==========================================================
# Recepción y cola
# ==========================================================



async def handle_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    media_mode = context.user_data.get("menu_mode")
    if media_mode == "restorevideo":
        accepted = await _handle_restore_media(
            context=context,
            message=message,
            user_id=user.id,
        )
        if accepted:
            context.user_data.pop("menu_mode", None)
            context.user_data.pop("restore_preset", None)
        return

    if media_mode != "fast1080":
        await message.reply_text(
            "ℹ️ Recibí un video, pero no hay una mejora seleccionada.\n\n"
            "Abre /menu → ✨ Mejorar contenido → ⚡ Super rápido 1080."
        )
        return

    media = _extract_received_video(message)
    if media is None:
        await message.reply_text(
            "❌ El archivo recibido no parece ser un video compatible."
        )
        return

    file_size = int(getattr(media, "file_size", 0) or 0)
    if file_size > FAST1080_MAX_INPUT_BYTES:
        await message.reply_text(
            "⚠️ Ese video supera el límite de descarga del Bot API estándar.\n\n"
            f"📦 Máximo permitido: {FAST1080_MAX_INPUT_MB} MB\n"
            f"📄 Archivo recibido: {format_file_size(file_size)}"
        )
        return

    known_duration = _telegram_duration_seconds(
        getattr(media, "duration", 0)
    )
    if (
        known_duration > 0
        and known_duration > FAST1080_MAX_DURATION_SECONDS
    ):
        await message.reply_text(
            "⚠️ El video supera la duración permitida para esta primera versión.\n\n"
            f"⏱️ Máximo: {FAST1080_MAX_DURATION_SECONDS} segundos\n"
            f"🎞️ Video recibido: {known_duration:.1f} segundos"
        )
        return

    file_id = str(getattr(media, "file_id", "") or "")
    file_unique_id = str(getattr(media, "file_unique_id", "") or "")
    if not file_id or not file_unique_id:
        await message.reply_text(
            "❌ Telegram no entregó un identificador válido para ese video."
        )
        return

    file_name = str(getattr(media, "file_name", "") or "video.mp4")
    status_message = await message.reply_text(
        "⏳ Registrando video en la cola ⚡ Super rápido 1080…"
    )

    receipt = await _enqueue_fast1080(
        user_id=user.id,
        source_message=message,
        status_message=status_message,
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        announced_size=file_size,
    )
    if receipt.accepted:
        context.user_data.pop("menu_mode", None)


def _extract_received_video(message: Message) -> Any | None:
    if message.video is not None:
        return message.video

    document = message.document
    if document is None:
        return None

    mime_type = str(document.mime_type or "").lower()
    suffix = Path(document.file_name or "").suffix.lower()
    if mime_type.startswith("video/") or suffix in _VIDEO_EXTENSIONS:
        return document

    return None


def _telegram_duration_seconds(value: Any) -> float:
    if hasattr(value, "total_seconds"):
        try:
            return max(0.0, float(value.total_seconds()))
        except (TypeError, ValueError):
            return 0.0

    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


async def _enqueue_fast1080(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    file_id: str,
    file_unique_id: str,
    file_name: str,
    announced_size: int,
) -> EnhancementQueueReceipt:
    async def runner() -> None:
        await _process_fast1080_job(
            user_id=user_id,
            source_message=source_message,
            status_message=status_message,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_name=file_name,
            announced_size=announced_size,
        )

    job = EnhancementJob(
        key=f"fast1080:{user_id}:{file_unique_id}",
        user_id=user_id,
        source=file_unique_id,
        label="FFmpeg Fast 1080",
        status_message=status_message,
        started_text=(
            "⚡ Iniciando Super rápido 1080…\n\n"
            "📍 Estado: descargando el video desde Telegram\n"
            "⚙️ Cola: procesamiento rápido"
        ),
        runner=runner,
    )
    receipt = await ENHANCEMENT_QUEUE.enqueue(job)
    await _announce_fast_queue_receipt(status_message, receipt)
    return receipt


async def _announce_fast_queue_receipt(
    status_message: Message,
    receipt: EnhancementQueueReceipt,
) -> None:
    if receipt.accepted:
        if receipt.position > 0:
            await _safe_edit(
                status_message,
                "🕒 Video añadido a la cola ⚡ Super rápido 1080\n\n"
                f"📍 Posición en espera: {receipt.position}\n"
                "⚙️ Motor: FFmpeg\n\n"
                "Te avisaré cuando comience.",
            )
        return

    messages = {
        "duplicate": (
            "ℹ️ Ese mismo video ya está en la cola rápida."
        ),
        "user_limit": (
            "🕒 Ya tienes una mejora rápida activa o en espera.\n\n"
            "Cuando termine podrás enviar otro video."
        ),
        "full": (
            "⚠️ La cola rápida está llena en este momento.\n\n"
            "Intenta nuevamente cuando termine alguna solicitud."
        ),
        "unavailable": (
            "❌ La cola rápida todavía no está disponible.\n\n"
            "Reinicia MediaLab y vuelve a intentarlo."
        ),
    }
    await _safe_edit(
        status_message,
        messages.get(
            receipt.reason,
            "❌ No se pudo registrar la mejora rápida.",
        ),
    )


async def _process_fast1080_job(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    file_id: str,
    file_unique_id: str,
    file_name: str,
    announced_size: int,
) -> None:
    input_path, output_path = FAST1080_ENHANCER.create_paths(
        user_id=user_id,
        file_unique_id=file_unique_id,
        original_name=file_name,
    )

    try:
        telegram_file = await source_message.get_bot().get_file(
            file_id,
            read_timeout=SEND_TIMEOUT,
            write_timeout=SEND_TIMEOUT,
            connect_timeout=60,
            pool_timeout=60,
        )
        await telegram_file.download_to_drive(
            custom_path=input_path,
            read_timeout=SEND_TIMEOUT,
            write_timeout=SEND_TIMEOUT,
            connect_timeout=60,
            pool_timeout=60,
        )
    except Exception:
        logger.exception("No se pudo descargar el video recibido desde Telegram.")
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "❌ Telegram no permitió descargar ese video.\n\n"
            f"El límite de entrada de esta versión es {FAST1080_MAX_INPUT_MB} MB.",
        )
        return

    real_size = input_path.stat().st_size if input_path.is_file() else 0
    if real_size <= 0 or real_size > FAST1080_MAX_INPUT_BYTES:
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "⚠️ El archivo descargado no es válido o supera el límite.\n\n"
            f"📦 Máximo: {FAST1080_MAX_INPUT_MB} MB\n"
            f"📄 Recibido: {format_file_size(real_size or announced_size)}",
        )
        return

    await _safe_edit(
        status_message,
        "⚡ Procesando video con FFmpeg…\n\n"
        f"📦 Entrada: {format_file_size(real_size)}\n"
        "🎞️ Objetivo: máximo 1080p\n"
        "🔧 Filtro: Lanczos\n\n"
        "La cola de descargas continúa funcionando por separado.",
    )

    result = await FAST1080_ENHANCER.process_async(input_path, output_path)
    if not result.success or result.output_path is None:
        if result.error:
            logger.error("Fast1080 falló: %s", result.error)
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            f"❌ {result.message}\n\n"
            "Revisa la consola de MediaLab para más información.",
        )
        return

    download_result = DownloadResult(
        success=True,
        message="Video mejorado correctamente.",
        file_path=result.output_path,
        title="Super rápido 1080",
        platform="Telegram",
        engine=result.encoder,
        file_size=result.file_size,
        width=result.width,
        height=result.height,
        duration=result.duration,
        extension=".mp4",
        video_id=file_unique_id,
    )

    await _safe_edit(
        status_message,
        "📤 Enviando video mejorado a Telegram…\n\n"
        f"📐 Resolución: {result.width}×{result.height}\n"
        f"📦 Tamaño: {format_file_size(result.file_size)}\n"
        f"⚙️ Codificador: {result.encoder}",
    )

    try:
        sent = await send_video(
            source_message,
            download_result,
            reply_to_message=False,
        )
    except Exception:
        logger.exception("Telegram no pudo enviar el video Fast1080.")
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "❌ El video fue procesado, pero Telegram no pudo enviarlo.\n\n"
            "Los archivos temporales fueron eliminados para proteger el disco.",
        )
        return

    if not sent:
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "⚠️ La salida superó el límite configurado para Telegram.\n\n"
            "MediaLab intentó controlar el tamaño, pero este video necesita "
            "una compresión más fuerte.",
        )
        return

    delete_sent_files([input_path, output_path])
    await _complete_and_remove_status(
        status_message,
        "✅ Video mejorado y enviado correctamente.",
    )


async def _handle_restore_media(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    user_id: int,
) -> bool:
    preset = str(context.user_data.get("restore_preset") or "")
    profile = RESTORATION_PROFILES.get(preset)
    if profile is None:
        await message.reply_text(
            "❌ No encuentro el acabado elegido.\n\n"
            "Abre /restorevideo y selecciona Natural, Natural HD o Natural HD+."
        )
        return False

    availability = VIDEO_RESTORER.availability_text()
    if availability != "disponible":
        logger.error("Restauración integral no disponible: %s", availability)
        await message.reply_text(
            "❌ La restauración integral todavía no está disponible en esta "
            "instalación.\n\nRevisa la consola de MediaLab para conocer los "
            "componentes pendientes."
        )
        return False

    media = _extract_received_video(message)
    if media is None:
        await message.reply_text(
            "❌ El archivo recibido no parece ser un video compatible."
        )
        return False

    file_size = int(getattr(media, "file_size", 0) or 0)
    if file_size > RESTORE_MAX_INPUT_BYTES:
        await message.reply_text(
            "⚠️ Ese video supera el límite de descarga del Bot API estándar.\n\n"
            f"📦 Máximo permitido: {RESTORE_MAX_INPUT_MB} MB\n"
            f"📄 Archivo recibido: {format_file_size(file_size)}"
        )
        return False

    known_duration = _telegram_duration_seconds(
        getattr(media, "duration", 0)
    )
    if (
        known_duration > 0
        and known_duration > RESTORE_MAX_DURATION_SECONDS
    ):
        await message.reply_text(
            "⚠️ El video supera la duración permitida para esta versión "
            "de prueba.\n\n"
            f"⏱️ Máximo: {RESTORE_MAX_DURATION_SECONDS} segundos\n"
            f"🎞️ Video recibido: {known_duration:.1f} segundos"
        )
        return False

    file_id = str(getattr(media, "file_id", "") or "")
    file_unique_id = str(getattr(media, "file_unique_id", "") or "")
    if not file_id or not file_unique_id:
        await message.reply_text(
            "❌ Telegram no entregó un identificador válido para ese video."
        )
        return False

    file_name = str(getattr(media, "file_name", "") or "video.mp4")
    status_message = await message.reply_text(
        "⏳ Registrando video en la cola de restauración integral…"
    )
    receipt = await _enqueue_restore(
        user_id=user_id,
        source_message=message,
        status_message=status_message,
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        announced_size=file_size,
        preset=preset,
    )
    return receipt.accepted


async def _enqueue_restore(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    file_id: str,
    file_unique_id: str,
    file_name: str,
    announced_size: int,
    preset: str,
) -> EnhancementQueueReceipt:
    profile = RESTORATION_PROFILES[preset]

    async def runner() -> None:
        await _process_restore_job(
            user_id=user_id,
            source_message=source_message,
            status_message=status_message,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_name=file_name,
            announced_size=announced_size,
            preset=preset,
        )

    job = EnhancementJob(
        key=f"restore:{user_id}:{file_unique_id}:{preset}",
        user_id=user_id,
        source=file_unique_id,
        label=f"Restauración · {profile.label}",
        status_message=status_message,
        started_text=(
            f"🪄 Iniciando restauración · {profile.label}…\n\n"
            "📍 Estado: descargando el video desde Telegram\n"
            "⚙️ Cola: mejoras de video\n\n"
            "🧠 Después se analizará fotograma por fotograma. Es normal que "
            "tarde notablemente más que una descarga."
        ),
        runner=runner,
    )
    receipt = await ENHANCEMENT_QUEUE.enqueue(job)
    await _announce_restore_queue_receipt(status_message, receipt)
    return receipt


async def _announce_restore_queue_receipt(
    status_message: Message,
    receipt: EnhancementQueueReceipt,
) -> None:
    if receipt.accepted:
        if receipt.position > 0:
            await _safe_edit(
                status_message,
                "🕒 Video añadido a la cola de restauración\n\n"
                f"📍 Posición en espera: {receipt.position}\n"
                "⚙️ Motor: PP-OCR + protección humana + OpenCV\n\n"
                "⏳ La restauración de alta calidad puede tardar varios "
                "minutos.\n"
                "Te avisaré cuando comience.",
            )
        return

    messages = {
        "duplicate": "ℹ️ Ese mismo video ya está en la cola de restauración.",
        "user_limit": (
            "🕒 Ya tienes una mejora activa o en espera.\n\n"
            "Cuando termine podrás enviar otro video."
        ),
        "full": (
            "⚠️ La cola de mejoras está llena en este momento.\n\n"
            "Intenta nuevamente cuando termine alguna solicitud."
        ),
        "unavailable": (
            "❌ La cola de mejoras todavía no está disponible.\n\n"
            "Reinicia MediaLab y vuelve a intentarlo."
        ),
    }
    await _safe_edit(
        status_message,
        messages.get(
            receipt.reason,
            "❌ No se pudo registrar la restauración.",
        ),
    )


async def _process_restore_job(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    file_id: str,
    file_unique_id: str,
    file_name: str,
    announced_size: int,
    preset: str,
) -> None:
    profile = RESTORATION_PROFILES[preset]
    input_path, output_path = VIDEO_RESTORER.create_paths(
        user_id=user_id,
        file_unique_id=file_unique_id,
        original_name=file_name,
    )

    try:
        telegram_file = await source_message.get_bot().get_file(
            file_id,
            read_timeout=SEND_TIMEOUT,
            write_timeout=SEND_TIMEOUT,
            connect_timeout=60,
            pool_timeout=60,
        )
        await telegram_file.download_to_drive(
            custom_path=input_path,
            read_timeout=SEND_TIMEOUT,
            write_timeout=SEND_TIMEOUT,
            connect_timeout=60,
            pool_timeout=60,
        )
    except Exception:
        logger.exception(
            "No se pudo descargar el video para restauración desde Telegram."
        )
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "❌ Telegram no permitió descargar ese video.\n\n"
            f"El límite de entrada de esta versión es {RESTORE_MAX_INPUT_MB} MB.",
        )
        return

    real_size = input_path.stat().st_size if input_path.is_file() else 0
    if real_size <= 0 or real_size > RESTORE_MAX_INPUT_BYTES:
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "⚠️ El archivo descargado no es válido o supera el límite.\n\n"
            f"📦 Máximo: {RESTORE_MAX_INPUT_MB} MB\n"
            f"📄 Recibido: {format_file_size(real_size or announced_size)}",
        )
        return

    await _safe_edit(
        status_message,
        f"🪄 Restaurando video · {profile.label}…\n\n"
        f"📦 Entrada: {format_file_size(real_size)}\n"
        "🔤 Texto: análisis conservador de todos los fotogramas\n"
        "🛡️ Identidad: rostro, cabello y cuerpo protegidos\n"
        "🎨 Color: balance temporal y saturación natural\n"
        "✨ Detalle: limpieza y enfoque suave fuera de la persona\n"
        "🎞️ Salida: máximo 1080p\n\n"
        "⏳ Este proceso es deliberadamente más lento que una descarga o "
        "Super rápido 1080. Puede tardar varios minutos.",
    )

    progress_queue: asyncio.Queue[RestorationProgress] = asyncio.Queue()
    event_loop = asyncio.get_running_loop()

    def report_progress(progress: RestorationProgress) -> None:
        event_loop.call_soon_threadsafe(
            progress_queue.put_nowait,
            progress,
        )

    processing_task = asyncio.create_task(
        VIDEO_RESTORER.process_async(
            input_path,
            output_path,
            preset=preset,
            progress_callback=report_progress,
        )
    )
    last_reported_percent = -10
    while not processing_task.done():
        try:
            progress = await asyncio.wait_for(
                progress_queue.get(),
                timeout=12.0,
            )
        except asyncio.TimeoutError:
            continue

        while not progress_queue.empty():
            progress = progress_queue.get_nowait()
        if (
            progress.percent < 95
            and progress.percent - last_reported_percent < 10
        ):
            continue

        frame_line = ""
        if progress.total_frames > 0:
            frame_line = (
                f"\n🎞️ Avance: {progress.frames_processed:,} de "
                f"{progress.total_frames:,} fotogramas"
            )
        await _safe_edit(
            status_message,
            f"🧠 Restauración de alta calidad · {profile.label}\n\n"
            f"📊 Progreso: {progress.percent}%\n"
            f"📍 Etapa: {progress.stage}"
            f"{frame_line}\n\n"
            "🛡️ La persona permanece protegida.\n"
            "⏳ Es normal que este proceso tarde varios minutos.",
        )
        last_reported_percent = progress.percent

    result = await processing_task
    if not result.success or result.output_path is None:
        if result.error:
            logger.error("Restauración integral falló: %s", result.error)
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            f"❌ {result.message}\n\n"
            "Revisa la consola de MediaLab para más información.",
        )
        return

    download_result = DownloadResult(
        success=True,
        message="Video restaurado correctamente.",
        file_path=result.output_path,
        title=f"Restauración integral · {result.preset}",
        platform="Telegram",
        engine=result.encoder,
        file_size=result.file_size,
        width=result.width,
        height=result.height,
        duration=result.duration,
        extension=".mp4",
        video_id=file_unique_id,
    )

    audio_text = (
        "audio original conservado"
        if result.audio_copied
        else "audio convertido para compatibilidad"
    )
    await _safe_edit(
        status_message,
        "📤 Enviando video restaurado a Telegram…\n\n"
        f"🎨 Acabado: {result.preset}\n"
        f"📐 Resolución: {result.width}×{result.height}\n"
        f"🎞️ Fotogramas revisados: {result.frames_processed}\n"
        f"🔤 Fotogramas con reconstrucción: {result.frames_with_text}\n"
        f"🔊 Audio: {audio_text}\n"
        f"📦 Tamaño: {format_file_size(result.file_size)}",
    )

    try:
        sent = await send_video(
            source_message,
            download_result,
            reply_to_message=False,
        )
    except Exception:
        logger.exception("Telegram no pudo enviar el video restaurado.")
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "❌ El video fue restaurado, pero Telegram no pudo enviarlo.\n\n"
            "Los archivos temporales fueron eliminados para proteger el disco.",
        )
        return

    if not sent:
        delete_sent_files([input_path, output_path])
        await _safe_edit(
            status_message,
            "⚠️ La salida superó el límite configurado para Telegram.\n\n"
            "Prueba con un video más corto o con el acabado Natural.",
        )
        return

    delete_sent_files([input_path, output_path])
    await _complete_and_remove_status(
        status_message,
        "✅ Video restaurado y enviado correctamente.",
    )


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return

    url = extract_first_url(message.text)
    if url is None:
        await message.reply_text(
            "❌ No encontré un enlace HTTP o HTTPS válido en el mensaje."
        )
        return

    platform = detect_platform(url)

    if platform == "tiktok":
        resolved_url = await resolve_tiktok_url(url)

        if is_tiktok_photo_url(resolved_url):
            status_message = await message.reply_text(
                "⏳ Registrando solicitud de TikTok…"
            )
            await _enqueue_tiktok_photo(
                user_id=user.id,
                source_message=message,
                status_message=status_message,
                url=resolved_url,
            )
            return

        request_id = _store_pending_tiktok_url(context, resolved_url)
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
            "🎬 Video de TikTok detectado.\n\n"
            "Selecciona el motor de descarga:",
            reply_markup=keyboard,
        )
        return

    if platform == "instagram":
        resolved_url = await resolve_instagram_url(url)
        normalized_url = normalize_instagram_url(resolved_url)

        if normalized_url is None or not is_instagram_single_media_url(
            normalized_url
        ):
            await message.reply_text(
                "❌ Por ahora MediaLab acepta únicamente enlaces individuales "
                "de Instagram:\n\n"
                "• /p/ publicaciones\n"
                "• /reel/ Reels\n"
                "• /tv/ videos\n\n"
                "Los perfiles completos no se procesan en este bot."
            )
            return

        status_message = await message.reply_text(
            "⏳ Registrando solicitud de Instagram…"
        )
        await _enqueue_instagram(
            user_id=user.id,
            source_message=message,
            status_message=status_message,
            url=normalized_url,
        )
        return

    await message.reply_text(
        "❌ Plataforma aún no soportada.\n\n"
        "Actualmente puedes enviar enlaces de TikTok e Instagram."
    )


async def handle_tiktok_engine_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Registra el motor elegido y añade el video a la cola global."""
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    if not await _safe_answer_query(query):
        return

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
    status_message = query.message
    if status_message is None:
        logger.error("El callback de TikTok no contiene mensaje.")
        return

    await status_message.edit_text("⏳ Registrando solicitud de TikTok…")
    await _enqueue_tiktok_video(
        user_id=user.id,
        source_message=status_message,
        status_message=status_message,
        url=url,
        engine=engine,
    )


async def _enqueue_tiktok_video(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    url: str,
    engine: Any,
) -> None:
    async def runner() -> None:
        await _process_tiktok_video_job(
            source_message=source_message,
            status_message=status_message,
            url=url,
            engine=engine,
        )

    job = DownloadJob(
        key=_job_key(user_id, url),
        user_id=user_id,
        url=url,
        platform="tiktok",
        label=engine.name,
        status_message=status_message,
        started_text=_processing_text(engine.name, "video de TikTok"),
        runner=runner,
    )
    receipt = await DOWNLOAD_QUEUE.enqueue(job)
    await _announce_queue_receipt(status_message, receipt, engine.name)


async def _enqueue_tiktok_photo(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    url: str,
) -> None:
    async def runner() -> None:
        await _process_tiktok_photo_job(
            source_message=source_message,
            status_message=status_message,
            url=url,
        )

    job = DownloadJob(
        key=_job_key(user_id, url),
        user_id=user_id,
        url=url,
        platform="tiktok",
        label="gallery-dl",
        status_message=status_message,
        started_text=_processing_text("gallery-dl", "carrusel de TikTok"),
        runner=runner,
    )
    receipt = await DOWNLOAD_QUEUE.enqueue(job)
    await _announce_queue_receipt(status_message, receipt, "gallery-dl")


async def _enqueue_instagram(
    *,
    user_id: int,
    source_message: Message,
    status_message: Message,
    url: str,
) -> None:
    async def runner() -> None:
        await _process_instagram_job(
            source_message=source_message,
            status_message=status_message,
            url=url,
        )

    content_type = instagram_content_type(url)
    job = DownloadJob(
        key=_job_key(user_id, url),
        user_id=user_id,
        url=url,
        platform="instagram",
        label="gallery-dl",
        status_message=status_message,
        started_text=_processing_text(
            "gallery-dl",
            f"{content_type} de Instagram",
        ),
        runner=runner,
    )
    receipt = await DOWNLOAD_QUEUE.enqueue(job)
    await _announce_queue_receipt(status_message, receipt, "gallery-dl")


async def _announce_queue_receipt(
    status_message: Message,
    receipt: QueueReceipt,
    engine_name: str,
) -> None:
    if receipt.accepted:
        if receipt.position > 0:
            await _safe_edit(
                status_message,
                "🕒 Solicitud añadida a la cola\n\n"
                f"📍 Posición en espera: {receipt.position}\n"
                f"⚙️ Motor: {engine_name}\n\n"
                "No necesitas volver a enviar el enlace. "
                "Te avisaré cuando comience.",
            )
        return

    messages = {
        "duplicate": (
            "ℹ️ Este enlace ya se está procesando.\n\n"
            "No necesitas enviarlo nuevamente."
        ),
        "user_limit": (
            "🕒 Ya tienes una solicitud activa o en espera.\n\n"
            "Cuando termine podrás enviar otra."
        ),
        "full": (
            "⚠️ La cola de MediaLab está llena en este momento.\n\n"
            "Intenta nuevamente cuando termine alguna solicitud."
        ),
        "unavailable": (
            "❌ La cola de descargas todavía no está disponible.\n\n"
            "Reinicia MediaLab y vuelve a intentarlo."
        ),
    }
    await _safe_edit(
        status_message,
        messages.get(
            receipt.reason,
            "❌ No se pudo registrar la solicitud.",
        ),
    )


# ==========================================================
# Procesadores ejecutados por la cola
# ==========================================================


async def _process_tiktok_video_job(
    *,
    source_message: Message,
    status_message: Message,
    url: str,
    engine: Any,
) -> None:
    result = await engine.download_async(url)

    if not result.success or result.file_path is None:
        if result.error:
            logger.error("Error del motor %s: %s", engine.name, result.error)
        await _safe_edit(
            status_message,
            f"❌ {result.message}\n\n"
            f"⚙️ Motor: {engine.name}",
        )
        return

    enrich_download_result(result)
    await _safe_edit(
        status_message,
        "📤 Enviando video a Telegram…\n\n"
        f"⚙️ Motor: {engine.name}\n"
        f"📦 Tamaño: {format_file_size(result.file_size)}",
    )

    try:
        sent = await send_video(
            source_message,
            result,
            reply_to_message=False,
        )
    except Exception:
        logger.exception("Telegram no pudo enviar el video.")
        await _safe_edit(
            status_message,
            "❌ El video se descargó, pero Telegram no pudo enviarlo.\n\n"
            "El archivo se conservará temporalmente. Revisa la consola.",
        )
        return

    if not sent:
        direct_url = result.direct_url.strip()
        if (
            result.engine == "TikWM Original"
            and _is_safe_direct_download_url(direct_url)
        ):
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬇️ Descargar original desde TikWM",
                            url=direct_url,
                        )
                    ]
                ]
            )
            await _safe_edit(
                status_message,
                "⚠️ El video original supera el límite de envío de Telegram.\n\n"
                f"{format_result_details(result)}\n\n"
                "Pulsa el botón para descargar el archivo original directamente "
                "desde TikWM. El enlace es temporal; ábrelo lo antes posible.",
                reply_markup=keyboard,
            )
            asyncio.create_task(
                _delete_oversized_file_later(result.file_path),
                name=f"delete-oversized-{result.video_id or int(time.time())}",
            )
            return

        await _safe_edit(
            status_message,
            "⚠️ El video fue descargado, pero supera el límite configurado "
            "para Telegram.\n\n"
            f"{format_result_details(result)}",
        )
        return

    if not delete_sent_file(result.file_path):
        logger.warning(
            "El video enviado no pudo eliminarse; se intentará en la limpieza periódica: %s",
            result.file_path,
        )

    await _complete_and_remove_status(
        status_message,
        "✅ Video enviado correctamente.",
    )


async def _process_tiktok_photo_job(
    *,
    source_message: Message,
    status_message: Message,
    url: str,
) -> None:
    result = await download_tiktok_photos(url)

    if not result.success:
        if result.error:
            logger.error("Error de gallery-dl para TikTok: %s", result.error)
        await _safe_edit(
            status_message,
            f"❌ {result.message}\n\n"
            "Revisa la consola de MediaLab para más información.",
        )
        return

    await _safe_edit(
        status_message,
        "📤 Enviando álbum a Telegram…\n\n"
        f"🖼️ Imágenes: {len(result.files)}\n"
        f"📦 Tamaño: {format_file_size(result.total_size)}",
    )

    try:
        sent = await send_photo_album(source_message, result)
    except Exception:
        logger.exception("Telegram no pudo enviar el álbum de TikTok.")
        await _safe_edit(
            status_message,
            "❌ Las imágenes se descargaron, pero Telegram no pudo enviarlas.\n\n"
            "Se conservarán temporalmente. Revisa la consola.",
        )
        return

    if not sent:
        return

    delete_sent_files(result.files)
    await _complete_and_remove_status(
        status_message,
        "✅ Álbum enviado correctamente.",
    )


async def _process_instagram_job(
    *,
    source_message: Message,
    status_message: Message,
    url: str,
) -> None:
    result = await download_instagram_media(url)

    if not result.success and is_instagram_video_url(url):
        if result.error:
            logger.warning(
                "gallery-dl falló en Instagram; se probará yt-dlp: %s",
                result.error,
            )

        await _safe_edit(
            status_message,
            "🔄 gallery-dl no pudo completar el video.\n\n"
            "Probando motor de respaldo yt-dlp en máxima calidad…",
        )
        fallback = await INSTAGRAM_YTDLP.download_async(url)

        if fallback.success and fallback.file_path is not None:
            enrich_download_result(fallback)
            delete_sent_files(result.files)
            result = InstagramDownloadResult(
                success=True,
                message=fallback.message,
                files=[fallback.file_path],
                working_directory=fallback.file_path.parent,
                content_type=instagram_content_type(url),
                engine=fallback.engine,
            )
        else:
            combined_error = " | ".join(
                value
                for value in (result.error, fallback.error)
                if value
            )
            if combined_error:
                logger.error(
                    "Instagram falló con gallery-dl y yt-dlp: %s",
                    combined_error,
                )
            await _safe_edit(
                status_message,
                "❌ No se pudo descargar ese contenido de Instagram con "
                "ninguno de los motores disponibles.\n\n"
                "Algunos contenidos requieren cookies válidas de Instagram.",
            )
            return

    if not result.success:
        if result.error:
            logger.error("Error de gallery-dl para Instagram: %s", result.error)
        await _safe_edit(
            status_message,
            f"❌ {result.message}\n\n"
            "Algunos contenidos requieren cookies válidas de Instagram. "
            "Revisa la consola.",
        )
        return

    delivery = (
        "archivos sin compresión"
        if INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS
        else "álbum visual"
    )
    await _safe_edit(
        status_message,
        "📤 Enviando contenido de Instagram…\n\n"
        f"📦 Archivos: {len(result.files)}\n"
        f"💾 Tamaño: {format_file_size(result.total_size)}\n"
        f"🧾 Entrega: {delivery}",
    )

    try:
        sent = await send_instagram_media(source_message, result)
    except Exception:
        logger.exception("Telegram no pudo enviar el contenido de Instagram.")
        await _safe_edit(
            status_message,
            "❌ El contenido se descargó, pero Telegram no pudo enviarlo.\n\n"
            "Los archivos se conservarán temporalmente. Revisa la consola.",
        )
        return

    if not sent:
        await _safe_edit(
            status_message,
            "⚠️ Uno o más archivos superan el límite configurado para "
            "Telegram.\n\n"
            f"📦 Archivos: {len(result.files)}\n"
            f"💾 Tamaño total: {format_file_size(result.total_size)}",
        )
        return

    delete_sent_files(result.files)
    await _complete_and_remove_status(
        status_message,
        "✅ Contenido de Instagram enviado correctamente.",
    )


# ==========================================================
# Formato y utilidades internas
# ==========================================================


def _format_photo_details(
    result: PhotoDownloadResult,
    heading: str = "📸 Publicación fotográfica",
) -> str:
    return (
        f"{heading}\n"
        f"🖼️ Imágenes: {len(result.files)}\n"
        f"📦 Peso total: {format_file_size(result.total_size)}\n"
        "⚙️ Motor: gallery-dl"
    )


def _format_instagram_details(result: InstagramDownloadResult) -> str:
    delivery = (
        "Archivos sin compresión de Telegram"
        if INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS
        else "Álbum visual"
    )
    return (
        "✅ Procesado por MediaLab\n"
        "🌐 Plataforma: Instagram\n"
        f"📦 Archivos: {len(result.files)}\n"
        f"💾 Peso total: {format_file_size(result.total_size)}\n"
        "✨ Calidad: máxima disponible\n"
        f"🧾 Entrega: {delivery}\n"
        f"⚙️ Motor: {result.engine}"
    )


def _is_safe_direct_download_url(url: str) -> bool:
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


async def _delete_oversized_file_later(
    path: Path | None,
    delay_seconds: int = 10 * 60,
) -> None:
    if path is None:
        return

    await asyncio.sleep(delay_seconds)

    try:
        if path.is_file():
            file_size = path.stat().st_size
            path.unlink()
            logger.info(
                "Archivo sobredimensionado eliminado después de %s segundos: "
                "%s (%s bytes)",
                delay_seconds,
                path,
                file_size,
            )
    except OSError as error:
        logger.warning(
            "No se pudo eliminar el archivo sobredimensionado %s: %s",
            path,
            error,
        )


def _processing_text(engine_name: str, content_label: str) -> str:
    return (
        "⬇️ Procesando tu solicitud…\n\n"
        f"📄 Contenido: {content_label}\n"
        f"⚙️ Motor: {engine_name}\n"
        "📍 Estado: Descargando\n\n"
        "No necesitas volver a enviar el enlace."
    )


def _job_key(user_id: int, url: str) -> str:
    clean_url = url.strip()
    try:
        parsed = urlparse(clean_url)
        host = (parsed.hostname or "").lower()
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{host}{port}"
        path = parsed.path.rstrip("/") or "/"
        canonical = urlunparse(
            (parsed.scheme.lower(), netloc, path, "", "", "")
        )
    except ValueError:
        canonical = clean_url
    return f"{user_id}:{canonical}"


async def _safe_answer_query(
    query: Any,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> bool:
    try:
        await query.answer(text=text, show_alert=show_alert)
        return True
    except BadRequest as error:
        message = str(error)
        if "Query is too old" in message or "query id is invalid" in message:
            logger.info("Callback expirado ignorado.")
            return False
        logger.warning("Telegram rechazó la respuesta del callback: %s", error)
        return False
    except TelegramError as error:
        logger.warning("No se pudo responder el callback: %s", error)
        return False


async def _safe_edit(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramError as error:
        logger.debug("No se pudo editar el mensaje temporal: %s", error)


async def _complete_and_remove_status(
    status_message: Message,
    text: str,
) -> None:
    await _safe_edit(status_message, text)
    asyncio.create_task(
        _delete_status_later(status_message),
        name=f"delete-status-{status_message.message_id}",
    )


async def _delete_status_later(status_message: Message) -> None:
    if STATUS_MESSAGE_DELETE_DELAY > 0:
        await asyncio.sleep(STATUS_MESSAGE_DELETE_DELAY)

    try:
        await status_message.delete()
    except TelegramError as error:
        logger.debug("No se pudo eliminar el mensaje temporal: %s", error)


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
