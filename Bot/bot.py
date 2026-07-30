from __future__ import annotations

import asyncio
import ctypes
import logging
import os
from ctypes import wintypes
from contextlib import suppress

from telegram import BotCommand, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import APP_NAME, APP_VERSION, TOKEN
from handlers import (
    audio_command,
    cancel_command,
    handle_link,
    handle_media,
    handle_navigation,
    handle_tiktok_engine_selection,
    health_command,
    help_command,
    menu_command,
    restorevideo_command,
    start,
    status_command,
)
from services.download_queue import DOWNLOAD_QUEUE
from services.enhancement_queue import ENHANCEMENT_QUEUE
from services.file_cleanup import periodic_cleanup_loop
from services.heartbeat import HEARTBEAT_SERVICE
from services.image_queue import IMAGE_QUEUE


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
_CLEANUP_TASK_KEY = "periodic_cleanup_task"
_INSTANCE_MUTEX_NAME = r"Local\MediaLabTelegramPolling"
_INSTANCE_MUTEX_HANDLE: int | None = None


def acquire_instance_lock() -> None:
    """Impide que dos procesos locales consuman getUpdates a la vez."""
    global _INSTANCE_MUTEX_HANDLE

    if os.name != "nt":
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, _INSTANCE_MUTEX_NAME)
    if not handle:
        raise OSError(
            ctypes.get_last_error(),
            "No se pudo crear el bloqueo de instancia de MediaLab.",
        )

    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        raise RuntimeError(
            "Ya hay una instancia local de MediaLab en ejecución. "
            "Usa el supervisor; no ejecutes bot.py por separado.",
        )

    _INSTANCE_MUTEX_HANDLE = handle


def release_instance_lock() -> None:
    global _INSTANCE_MUTEX_HANDLE

    if _INSTANCE_MUTEX_HANDLE is None or os.name != "nt":
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(_INSTANCE_MUTEX_HANDLE)
    _INSTANCE_MUTEX_HANDLE = None


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if isinstance(context.error, BadRequest):
        message = str(context.error)
        if "Query is too old" in message or "query id is invalid" in message:
            logger.info("Callback expirado ignorado por el manejador global.")
            return

    logger.exception(
        "Error no controlado al procesar una actualización.",
        exc_info=context.error,
    )

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Ocurrió un error inesperado. Revisa la consola de MediaLab."
            )
        except Exception:
            logger.exception("No se pudo enviar el mensaje de error a Telegram.")


async def post_init(application: Application) -> None:
    await DOWNLOAD_QUEUE.start()
    await ENHANCEMENT_QUEUE.start()
    await IMAGE_QUEUE.start()
    await HEARTBEAT_SERVICE.start()

    cleanup_task = asyncio.create_task(
        periodic_cleanup_loop(),
        name="medialab-periodic-cleanup",
    )
    application.bot_data[_CLEANUP_TASK_KEY] = cleanup_task

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Abrir MediaLab"),
            BotCommand("menu", "Abrir el menú principal"),
            BotCommand("audio", "Extraer MP3 de YouTube o TikTok"),
            BotCommand("restorevideo", "Restaurar color y eliminar textos"),
            BotCommand("status", "Ver el estado de tus trabajos"),
            BotCommand("cancel", "Cancelar una selección pendiente"),
            BotCommand("help", "Abrir la ayuda interactiva"),
            BotCommand("health", "Estado técnico para superusuarios"),
        ]
    )

    logger.info("Limpiador periódico iniciado: intervalo de 8 horas.")
    logger.info("Heartbeat iniciado: actualización cada 60 segundos.")


async def post_stop(application: Application) -> None:
    await HEARTBEAT_SERVICE.stop()
    await IMAGE_QUEUE.stop()
    await ENHANCEMENT_QUEUE.stop()
    await DOWNLOAD_QUEUE.stop()

    task = application.bot_data.pop(_CLEANUP_TASK_KEY, None)
    if not isinstance(task, asyncio.Task):
        return

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def build_application() -> Application:
    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("audio", audio_command))
    application.add_handler(CommandHandler("restorevideo", restorevideo_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(
        CallbackQueryHandler(
            handle_tiktok_engine_selection,
            pattern=r"^tikeng:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_navigation,
            pattern=r"^(menu|help|restore|photo):",
        )
    )
    application.add_handler(
        MessageHandler(
            filters.VIDEO
            | filters.Document.VIDEO
            | filters.PHOTO
            | filters.Document.IMAGE,
            handle_media,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_link,
        )
    )

    application.add_error_handler(error_handler)
    return application


def main() -> None:
    try:
        acquire_instance_lock()
    except RuntimeError as error:
        print(f"MediaLab no se inició: {error}")
        return

    try:
        print(f"✅ {APP_NAME} v{APP_VERSION} iniciado.")
        print("Presiona Ctrl + C para detenerlo.")

        application = build_application()
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()
