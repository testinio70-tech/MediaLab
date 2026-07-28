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
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import (
    ALLOWED_USERS,
    ENGINE_SELECTION_TTL,
    INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS,
    MAX_PENDING_SELECTIONS_PER_USER,
    MAX_TELEGRAM_FILE_SIZE,
    MAX_TELEGRAM_PHOTO_SIZE,
    SEND_TIMEOUT,
    STATUS_MESSAGE_DELETE_DELAY,
)
from engines.instagram.ytdlp import InstagramYTDLPEngine
from engines.tiktok.tikwm import TikWMEngine
from engines.tiktok.ytdlp import TikTokYTDLPEngine
from media_info import enrich_download_result, format_result_details
from models import DownloadResult
from services.download_queue import DOWNLOAD_QUEUE, DownloadJob, QueueReceipt
from services.file_cleanup import delete_sent_file, delete_sent_files
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

    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        "✅ MediaLab está en línea.\n\n"
        "Envíame un enlace compatible.\n\n"
        "Plataformas disponibles:\n"
        "• TikTok: videos y carruseles de fotos\n"
        "• Instagram: Posts, Reels y carruseles individuales\n\n"
        "Las solicitudes se procesan mediante una cola para evitar que "
        "varias descargas saturen la computadora."
    )


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

    await message.reply_text(
        "📥 MediaLab Downloader\n\n"
        "TikTok:\n"
        "• Videos con TikWM Original o yt-dlp\n"
        "• Carruseles fotográficos con gallery-dl\n\n"
        "Instagram:\n"
        "• Posts /p/\n"
        "• Reels /reel/\n"
        "• Videos /tv/\n"
        "• Carruseles de fotos y videos\n"
        "• Máxima calidad que Instagram exponga\n\n"
        "Mientras una solicitud está activa, no necesitas volver a enviar "
        "el enlace. Los archivos enviados se eliminan de la PC y la limpieza "
        "solo se registra en consola.\n\n"
        "Comandos:\n"
        "/start - Iniciar MediaLab\n"
        "/help - Mostrar esta ayuda"
    )


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


async def _safe_edit(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
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
