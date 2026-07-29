from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(
    version: str,
    *,
    privileged: bool,
) -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [InlineKeyboardButton("📥 Descargar contenido", callback_data="menu:download")],
        [InlineKeyboardButton("✨ Mejorar contenido", callback_data="menu:enhance")],
        [InlineKeyboardButton("📊 Estado de mis trabajos", callback_data="menu:status")],
        [InlineKeyboardButton("❓ Ayuda", callback_data="menu:help")],
    ]
    if privileged:
        rows.append(
            [InlineKeyboardButton("🩺 Estado técnico", callback_data="menu:health")]
        )

    text = (
        f"🎬 MediaLab {version}\n\n"
        "Descarga y mejora contenido multimedia desde Telegram.\n\n"
        "Selecciona una opción:"
    )
    return text, InlineKeyboardMarkup(rows)


def download_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📥 Descargar contenido\n\n"
        "Envíame directamente un enlace compatible.\n\n"
        "• TikTok: videos y carruseles de fotos\n"
        "• Instagram: Posts, Reels y carruseles individuales\n\n"
        "Las solicitudes se procesan mediante una cola para proteger la PC."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❓ Ayuda de descargas", callback_data="help:downloads")],
            [InlineKeyboardButton("↩️ Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def enhancement_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "✨ Mejorar contenido\n\n"
        "⚡ Super rápido 1080 ya está disponible con una cola separada.\n"
        "Las herramientas de IA continuarán integrándose progresivamente.\n\n"
        "Selecciona una opción:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🪄 Restauración integral",
                    callback_data="menu:restore",
                )
            ],
            [
                InlineKeyboardButton(
                    "🖼️ Foto IA x2",
                    callback_data="menu:feature:photo",
                )
            ],
            [
                InlineKeyboardButton(
                    "⚡ Super rápido 1080",
                    callback_data="menu:feature:fast1080",
                )
            ],
            [
                InlineKeyboardButton(
                    "🐢 IA x2 · máx. 15 s · lento",
                    callback_data="menu:feature:videoai",
                )
            ],
            [InlineKeyboardButton("↩️ Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def restoration_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🪄 Restauración integral de video\n\n"
        "MediaLab revisará cada fotograma con detección de texto y protección "
        "de personas. Solo reconstruirá zonas seguras del fondo; rostro, "
        "cabello y cuerpo no se regeneran.\n\n"
        "También limpiará ruido, corregirá color y aplicará un enfoque "
        "suave. Es un proceso de alta calidad y puede tardar varios minutos.\n\n"
        "Selecciona el acabado:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🌿 Natural",
                    callback_data="restore:preset:natural",
                )
            ],
            [
                InlineKeyboardButton(
                    "✨ Natural HD · recomendado",
                    callback_data="restore:preset:natural_hd",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔎 Natural HD+ · hasta 1080p",
                    callback_data="restore:preset:natural_hd_plus",
                )
            ],
            [InlineKeyboardButton("↩️ Mejoras", callback_data="menu:enhance")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def restoration_prompt(
    preset_label: str,
    max_input_mb: int,
    max_duration_seconds: int,
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🪄 Restauración integral · {preset_label}\n\n"
        "Envíame ahora un video normal o un archivo de video.\n\n"
        "El proceso realizará:\n"
        "• revisión fotograma por fotograma;\n"
        "• detección de texto mediante un modelo especializado;\n"
        "• protección de rostro, cabello, manos y cuerpo;\n"
        "• eliminación de texto solo en zonas seguras del fondo;\n"
        "• restauración de temperatura, saturación y contraste;\n"
        "• reducción de ruido y enfoque suave sin superar 1080p.\n\n"
        f"📦 Entrada máxima: {max_input_mb} MB\n"
        f"⏱️ Duración máxima inicial: {max_duration_seconds} s\n\n"
        "⏳ Tardará notablemente más que una descarga normal.\n"
        "🛡️ Si un texto cruza una persona, MediaLab conservará esa parte "
        "antes que arriesgar su identidad o anatomía."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↩️ Elegir otro acabado",
                    callback_data="menu:restore",
                )
            ],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def fast1080_prompt(
    max_input_mb: int,
    max_duration_seconds: int,
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "⚡ Super rápido 1080\n\n"
        "Envíame ahora un video como video normal o como archivo.\n\n"
        "MediaLab lo escalará con FFmpeg y filtro Lanczos, mantendrá la "
        "proporción y generará un MP4 H.264 compatible con Telegram.\n\n"
        f"📦 Entrada máxima: {max_input_mb} MB\n"
        f"⏱️ Duración máxima inicial: {max_duration_seconds} s\n"
        "🎞️ Salida máxima: 1920×1080 o 1080×1920\n"
        "⚙️ Aceleración: NVIDIA NVENC cuando esté disponible; si falla, "
        "se usa libx264 automáticamente.\n\n"
        "Envía un solo video."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️ Mejoras", callback_data="menu:enhance")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def feature_menu(feature: str) -> tuple[str, InlineKeyboardMarkup]:
    texts = {
        "photo": (
            "🖼️ Foto IA x2\n\n"
            "Duplicará las dimensiones y reconstruirá detalles con IA.\n\n"
            "Estado: en preparación para MediaLab 2.4.0-alpha.2."
        ),
        "fast1080": (
            "⚡ Super rápido 1080\n\n"
            "Escalado rápido con FFmpeg hasta 1080p. "
            "No inventa detalles nuevos y es mucho más rápido que la IA.\n\n"
            "Estado: disponible."
        ),
        "videoai": (
            "🐢 IA x2 · máximo 15 s · lento\n\n"
            "Procesamiento por fragmentos, una sola tarea de GPU a la vez "
            "y salida máxima 1080p.\n\n"
            "Estado: planeado para MediaLab 2.4.1."
        ),
    }
    text = texts.get(feature, "❌ Opción de mejora desconocida.")
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️ Mejoras", callback_data="menu:enhance")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def help_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = "❓ Ayuda de MediaLab\n\nSelecciona el tema que deseas consultar:"
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Descargas", callback_data="help:downloads")],
            [InlineKeyboardButton("✨ Mejoras", callback_data="help:enhancements")],
            [InlineKeyboardButton("🕒 Colas y tiempos", callback_data="help:queues")],
            [InlineKeyboardButton("⚠️ Errores frecuentes", callback_data="help:errors")],
            [InlineKeyboardButton("🔐 Privacidad", callback_data="help:privacy")],
            [InlineKeyboardButton("📋 Comandos", callback_data="help:commands")],
            [InlineKeyboardButton("↩️ Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def help_section(section: str) -> tuple[str, InlineKeyboardMarkup]:
    sections = {
        "downloads": (
            "📥 Descargas\n\n"
            "TikTok:\n"
            "• Videos con TikWM Original o yt-dlp\n"
            "• Carruseles fotográficos con gallery-dl\n\n"
            "Instagram:\n"
            "• Posts, Reels, videos /tv/ y carruseles individuales\n"
            "• Máxima calidad que Instagram exponga\n"
            "• Algunos enlaces requieren cookies válidas"
        ),
        "enhancements": (
            "✨ Mejoras\n\n"
            "🪄 Restauración integral: revisa cada fotograma, protege a las "
            "personas, elimina texto únicamente del fondo seguro y recupera "
            "un acabado natural hasta 1080p. Es la opción más lenta.\n\n"
            "🖼️ Foto IA x2: restauración y ampliación con IA.\n\n"
            "⚡ Super rápido 1080: disponible; recibe videos y usa "
            "una cola separada de procesamiento.\n\n"
            "🐢 IA x2: video lento, máximo 15 segundos y salida 1080p."
        ),
        "queues": (
            "🕒 Colas y tiempos\n\n"
            "Cada usuario puede tener una solicitud activa o pendiente.\n"
            "Una descarga que falla libera la cola para que continúe la siguiente.\n\n"
            "Super rápido 1080 y Restauración integral usan la cola de mejoras. "
            "La restauración puede tardar varios minutos porque analiza todos "
            "los fotogramas con un solo trabajador."
        ),
        "errors": (
            "⚠️ Errores frecuentes\n\n"
            "• Enlace expirado o privado\n"
            "• Cookies de Instagram vencidas\n"
            "• Rechazo temporal de TikTok o Instagram\n"
            "• Archivo superior al límite configurado\n"
            "• Cola llena\n\n"
            "Un fallo individual no debe congelar las demás solicitudes."
        ),
        "privacy": (
            "🔐 Privacidad\n\n"
            "Los archivos enviados correctamente se eliminan de la PC.\n"
            "El token, la lista de usuarios y las cookies permanecen fuera de Git.\n"
            "La limpieza de archivos se registra únicamente en la consola."
        ),
        "commands": (
            "📋 Comandos\n\n"
            "/start — Abrir MediaLab\n"
            "/menu — Abrir el menú principal\n"
            "/restorevideo — Restaurar cada fotograma y retirar texto seguro\n"
            "/status — Ver el estado de tus trabajos\n"
            "/cancel — Cancelar selecciones pendientes\n"
            "/help — Abrir esta ayuda\n"
            "/health — Estado técnico para superusuarios"
        ),
    }
    text = sections.get(section, "❌ Sección de ayuda desconocida.")
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️ Volver a ayuda", callback_data="help:main")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Actualizar", callback_data="menu:status")],
            [InlineKeyboardButton("↩️ Menú principal", callback_data="menu:main")],
        ]
    )


def health_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Actualizar", callback_data="menu:health")],
            [InlineKeyboardButton("↩️ Menú principal", callback_data="menu:main")],
        ]
    )
