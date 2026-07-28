from pathlib import Path
import os

from dotenv import load_dotenv


# ==========================================================
# Carpetas del proyecto
# ==========================================================

ROOT = Path(r"C:\MediaLab")

BOT_FOLDER = ROOT / "Bot"
DOWNLOADS_FOLDER = ROOT / "Downloads"
COOKIES_FOLDER = ROOT / "Cookies"
LOGS_FOLDER = ROOT / "Logs"
TEMP_FOLDER = BOT_FOLDER / "temp"
CACHE_FOLDER = BOT_FOLDER / "cache"

TIKTOK_DOWNLOADS = DOWNLOADS_FOLDER / "TikTok"


# ==========================================================
# Telegram
# ==========================================================

load_dotenv(BOT_FOLDER / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("No se encontró TELEGRAM_BOT_TOKEN.")

ALLOWED_USERS = {
    int(value.strip())
    for value in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if value.strip()
}


# ==========================================================
# Límites y tiempos de espera
# ==========================================================

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = 600
SEND_TIMEOUT = 300
ENGINE_SELECTION_TTL = 10 * 60
MAX_PENDING_SELECTIONS_PER_USER = 10

FFPROBE_BINARY = os.getenv("FFPROBE_BINARY", "ffprobe").strip() or "ffprobe"


# ==========================================================
# Aplicación
# ==========================================================

APP_NAME = "MediaLab"
APP_VERSION = "2.1.0"
