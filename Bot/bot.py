from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import APP_NAME, APP_VERSION, TOKEN
from handlers import handle_link, help_command, start


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

# Evita mostrar cada petición HTTP y, especialmente, el token en la consola.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


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


def build_application() -> Application:
    """Construye y configura la aplicación de Telegram."""

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
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
