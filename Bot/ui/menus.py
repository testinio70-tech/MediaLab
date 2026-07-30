from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(
    version: str,
    *,
    privileged: bool,
) -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [
            InlineKeyboardButton("📥 Descargar video", callback_data="menu:download"),
            InlineKeyboardButton("🎵 Extraer MP3", callback_data="menu:audio"),
        ],
        [InlineKeyboardButton("✨ Mejorar video o fotos", callback_data="menu:enhance")],
        [
            InlineKeyboardButton("📊 Mis trabajos", callback_data="menu:status"),
            InlineKeyboardButton("❓ Ayuda", callback_data="menu:help"),
        ],
        [InlineKeyboardButton("👁 Auto-watchers", callback_data="watcher:menu")],
    ]
    if privileged:
        rows.append(
            [InlineKeyboardButton("🩺 Estado técnico", callback_data="menu:health")]
        )

    text = (
        f"🎬 MediaLab {version}\n"
        "━━━━━━━━━━━━━━━━\n"
        "Tu espacio para descargar, convertir y mejorar contenido.\n\n"
        "Elige una herramienta:"
    )
    return text, InlineKeyboardMarkup(rows)


def download_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📥 Descargar contenido\n\n"
        "Envíame directamente un enlace compatible.\n\n"
        "• TikTok: videos y carruseles de fotos\n"
        "• Instagram: Posts, Reels y carruseles individuales\n\n"
        "Para recibir solo audio, usa el botón 🎵 Extraer MP3.\n\n"
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
        "🖼️ Foto IA x2 procesa lotes de hasta 10 imágenes en una cola propia.\n"
        "⚡ Super rápido 1080 usa una cola separada para videos.\n\n"
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


def watcher_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "👁 Auto-watchers\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "Vigila perfiles de TikTok, Instagram y Facebook y recibe cada "
        "publicación nueva automáticamente.\n\n"
        "⏱️ TikTok: cada 10 minutos\n"
        "⏱️ Instagram/Facebook: cada 20 minutos\n"
        "🔐 Se probarán cookies autorizadas antes de marcar un perfil como inaccesible."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Crear watcher", callback_data="watcher:create")],
            [InlineKeyboardButton("📋 Mis watchers", callback_data="watcher:list")],
            [InlineKeyboardButton("🧪 Comprobar ahora", callback_data="watcher:check")],
            [InlineKeyboardButton("↩️ Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def watcher_create_prompt(step: str) -> tuple[str, InlineKeyboardMarkup]:
    prompts = {
        "title": "Escribe el título de tu watcher.",
        "tiktok": "Envía el perfil de TikTok o escribe `-` para omitirlo.",
        "instagram": "Envía el perfil de Instagram o escribe `-` para omitirlo.",
        "facebook": "Envía el perfil o página de Facebook o escribe `-` para omitirlo.",
        "destination": (
            "¿Dónde envío las publicaciones?\n\n"
            "Envía @nombre_del_canal o el ID numérico del chat.\n"
            "El bot debe estar dentro del grupo o ser administrador del canal."
        ),
    }
    text = "👁 Crear watcher\n\n" + prompts.get(step, prompts["title"])
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancelar", callback_data="watcher:cancel")]]
    )
    return text, keyboard


def audio_prompt() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🎵 Extraer MP3\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "Envíame un enlace de YouTube o TikTok y recibirás únicamente "
        "el audio en MP3 de alta calidad.\n\n"
        "• YouTube: videos públicos individuales\n"
        "• TikTok: videos públicos\n"
        "• Instagram: intento de compatibilidad para Reels públicos\n\n"
        "⏳ El MP3 se descarga y convierte en la cola normal. Puede tardar "
        "más que un enlace directo; te avisaré en cada etapa.\n"
        "📦 Límite: 30 minutos por archivo."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️ Descargas", callback_data="menu:download")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def restoration_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🪄 Restauración fotograma por fotograma\n\n"
        "Las dos opciones detectan filtros anormales, recuperan iluminación "
        "natural y eliminan texto y logotipos completos.\n\n"
        "🌿 Restauración fiel\n"
        "Conserva la resolución y la estructura original. Corrige color, "
        "ruido y nitidez sin reinterpretar a la persona.\n\n"
        "🧠 Restauración IA HD\n"
        "Añade superresolución 2× controlada y microdetalle en piel, cabello "
        "y extremidades. El rostro recibe una protección más fuerte para "
        "mantener identidad y proporciones.\n\n"
        "Selecciona el proceso:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🌿 Restauración fiel",
                    callback_data="restore:preset:faithful",
                )
            ],
            [
                InlineKeyboardButton(
                    "🧠 Restauración IA HD · recomendado",
                    callback_data="restore:preset:ai_hd",
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
    is_ai = "IA" in preset_label
    detail_text = (
        "• superresolución neuronal 2× con mezcla limitada;\n"
        "• microdetalle suave en piel, cabello y extremidades;\n"
        "• bloqueo reforzado de rostro, identidad y proporciones;\n"
        "• salida máxima de 1080p.\n"
        if is_ai
        else (
            "• conservación de la geometría y resolución original;\n"
            "• reducción de ruido y nitidez suave sin regenerar personas;\n"
            "• salida máxima de 1080p cuando sea necesario reducirla.\n"
        )
    )
    estimate_text = (
        "🕰️ Puede tardar entre 1 y 6 horas por cada minuto de video "
        "en este equipo."
        if is_ai
        else "⏳ Tardará notablemente más que una descarga normal."
    )
    text = (
        f"🪄 Restauración integral · {preset_label}\n\n"
        "Envíame ahora un video normal o un archivo de video.\n\n"
        "El proceso realizará:\n"
        "• revisión fotograma por fotograma;\n"
        "• detección de texto y logotipos asociados;\n"
        "• eliminación completa con reconstrucción local limitada;\n"
        "• detección y corrección de filtros de aplicación;\n"
        "• balance natural de iluminación, temperatura y saturación;\n"
        f"{detail_text}\n"
        f"📦 Entrada máxima: {max_input_mb} MB\n"
        f"⏱️ Duración máxima inicial: {max_duration_seconds} s\n\n"
        f"{estimate_text}\n"
        "🛡️ La reconstrucción se limita a la sobreimpresión y al "
        "microdetalle; no cambia forma, pose ni proporciones."
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


def photo_ai_prompt(
    mode_label: str,
    max_batch: int,
    max_input_mb: int,
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🖼️ Foto IA x2 · {mode_label}\n\n"
        f"Envíame de 1 a {max_batch} imágenes. Para un lote, selecciónalas "
        "juntas como álbum antes de enviarlas.\n\n"
        "MediaLab aplicará restauración local 2×, protegerá rostros y "
        "personas, y devolverá todas las imágenes en un solo álbum.\n\n"
        f"📦 Máximo por imagen: {max_input_mb} MB\n"
        "⚙️ Cola: exclusiva para imágenes\n"
        "🧠 Procesamiento: un lote a la vez para proteger DirectML\n"
        "🔒 Servicio local y gratuito; las fotos no se envían a terceros."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↩️ Elegir otro acabado",
                    callback_data="menu:feature:photo",
                )
            ],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def photo_ai_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🖼️ Foto IA x2\n\n"
        "Las dos opciones son gratuitas, locales y aceptan lotes de hasta "
        "10 imágenes.\n\n"
        "🌿 Restauración fiel x2\n"
        "Amplía, limpia y enfoca con una mezcla conservadora que mantiene "
        "el aspecto original.\n\n"
        "🧠 Detalle IA local x2\n"
        "Refuerza cabello, ropa y texturas con el acabado más cercano a la "
        "referencia, protegiendo especialmente el rostro.\n\n"
        "Selecciona el acabado:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🌿 Restauración fiel x2",
                    callback_data="photo:preset:faithful",
                )
            ],
            [
                InlineKeyboardButton(
                    "🧠 Detalle IA local x2 · recomendado",
                    callback_data="photo:preset:detail",
                )
            ],
            [InlineKeyboardButton("↩️ Mejoras", callback_data="menu:enhance")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
        ]
    )
    return text, keyboard


def feature_menu(feature: str) -> tuple[str, InlineKeyboardMarkup]:
    texts = {
        "photo": (
            "🖼️ Foto IA x2\n\n"
            "Disponible desde el menú de mejoras para lotes de hasta 10 imágenes."
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
            [InlineKeyboardButton("👁 Auto-watchers", callback_data="help:watchers")],
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
            "• Carruseles fotográficos con gallery-dl\n"
            "• Audio MP3 desde videos públicos\n\n"
            "YouTube:\n"
            "• Audio MP3 desde videos públicos individuales\n\n"
            "Instagram:\n"
            "• Posts, Reels, videos /tv/ y carruseles individuales\n"
            "• Máxima calidad que Instagram exponga\n"
            "• Algunos enlaces requieren cookies válidas\n"
            "• MP3 de Reels públicos: disponibilidad variable"
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
            "/audio — Abrir la extracción MP3\n"
            "/watcher — Crear un auto-watcher con botones\n"
            "/sendwatcher — Crear watcher con formato avanzado\n"
            "/watchers — Ver y administrar tus watchers\n"
            "/restorevideo — Restaurar cada fotograma y retirar texto seguro\n"
            "/status — Ver el estado de tus trabajos\n"
            "/cancel — Cancelar selecciones pendientes\n"
            "/help — Abrir esta ayuda\n"
            "/health — Estado técnico para superusuarios"
        ),
        "watchers": (
            "👁 Auto-watchers\n\n"
            "TikTok se revisa cada 10 minutos e Instagram/Facebook cada 20.\n"
            "La primera revisión crea una línea base para no enviar contenido antiguo.\n"
            "Los temporales se eliminan después de enviarse.\n\n"
            "TikTok utiliza TikWM; Instagram y Facebook prueban cookies autorizadas "
            "cuando están disponibles."
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
