from __future__ import annotations

import asyncio


# Serializa únicamente los envíos pesados a Telegram. El procesamiento de cada
# cola continúa de forma independiente mientras otra tarea está subiendo.
TELEGRAM_UPLOAD_LOCK = asyncio.Lock()
