from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from telegram import Update
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
    handle_link,
    handle_tiktok_engine_selection,
    help_command,
    start,
)
from services.file_cleanup import periodic_cleanup_loop


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

# Evita mostrar cada petición HTTP y, especialmente, el token en la consola.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
_CLEANUP_TASK_KEY = "periodic_cleanup_task"


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Registra errores inesperados sin apagar el bot."""

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
    """Inicia el limpiador liviano sin agregar APScheduler."""

    cleanup_task = asyncio.create_task(
        periodic_cleanup_loop(),
        name="medialab-periodic-cleanup",
    )
    application.bot_data[_CLEANUP_TASK_KEY] = cleanup_task
    logger.info("Limpiador periódico iniciado: intervalo de 8 horas.")


async def post_stop(application: Application) -> None:
    """Cancela el limpiador para permitir un cierre ordenado."""

    task = application.bot_data.pop(_CLEANUP_TASK_KEY, None)
    if not isinstance(task, asyncio.Task):
        return

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def build_application() -> Application:
    """Construye y configura la aplicación de Telegram."""

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        CallbackQueryHandler(
            handle_tiktok_engine_selection,
            pattern=r"^tikeng:",
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
    """Punto de entrada de MediaLab."""

    print(f"✅ {APP_NAME} v{APP_VERSION} iniciado.")
    print("Presiona Ctrl + C para detenerlo.")

    application = build_application()
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
