from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _read_int_env(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _read_float_env(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    if raw_value in {"1", "true", "yes", "si", "sí", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    return default


def _read_user_ids(name: str) -> set[int]:
    values: set[int] = set()
    for raw_value in os.getenv(name, "").split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            values.add(int(value))
        except ValueError:
            continue
    return values


ROOT = Path(r"C:\MediaLab")
BOT_FOLDER = ROOT / "Bot"
DOWNLOADS_FOLDER = ROOT / "Downloads"
COOKIES_FOLDER = ROOT / "Cookies"
LOGS_FOLDER = ROOT / "Logs"
TEMP_FOLDER = BOT_FOLDER / "temp"
CACHE_FOLDER = BOT_FOLDER / "cache"
STATE_FOLDER = BOT_FOLDER / "state"
FAST1080_FOLDER = TEMP_FOLDER / "Fast1080"
RESTORE_FOLDER = TEMP_FOLDER / "RestoreHD"
HEARTBEAT_FILE = STATE_FOLDER / "heartbeat.json"

TIKTOK_DOWNLOADS = DOWNLOADS_FOLDER / "TikTok"
TIKTOK_PHOTOS = TIKTOK_DOWNLOADS / "Photos"
INSTAGRAM_DOWNLOADS = DOWNLOADS_FOLDER / "Instagram"

load_dotenv(BOT_FOLDER / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("No se encontró TELEGRAM_BOT_TOKEN.")

ALLOWED_USERS = _read_user_ids("TELEGRAM_ALLOWED_USER_IDS")
PRIVILEGED_USERS = _read_user_ids("TELEGRAM_PRIVILEGED_USER_IDS")

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024
MAX_TELEGRAM_PHOTO_SIZE = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT = 600
SEND_TIMEOUT = 300
ENGINE_SELECTION_TTL = 10 * 60
MAX_PENDING_SELECTIONS_PER_USER = 10
CLEANUP_INTERVAL_SECONDS = 8 * 60 * 60
CLEANUP_MAX_AGE_SECONDS = 24 * 60 * 60

DOWNLOAD_QUEUE_MAX_SIZE = _read_int_env(
    "DOWNLOAD_QUEUE_MAX_SIZE", 10, minimum=1, maximum=100
)
DOWNLOAD_QUEUE_WORKERS = _read_int_env(
    "DOWNLOAD_QUEUE_WORKERS", 1, minimum=1, maximum=4
)
MAX_JOBS_PER_USER = _read_int_env(
    "MAX_JOBS_PER_USER", 1, minimum=1, maximum=10
)

FAST_QUEUE_MAX_SIZE = _read_int_env(
    "FAST_QUEUE_MAX_SIZE", 5, minimum=1, maximum=20
)
FAST_QUEUE_WORKERS = _read_int_env(
    "FAST_QUEUE_WORKERS", 1, minimum=1, maximum=2
)
FAST_MAX_JOBS_PER_USER = _read_int_env(
    "FAST_MAX_JOBS_PER_USER", 1, minimum=1, maximum=3
)
FAST1080_MAX_INPUT_MB = _read_int_env(
    "FAST1080_MAX_INPUT_MB", 20, minimum=1, maximum=20
)
FAST1080_MAX_INPUT_BYTES = FAST1080_MAX_INPUT_MB * 1024 * 1024
FAST1080_MAX_DURATION_SECONDS = _read_int_env(
    "FAST1080_MAX_DURATION_SECONDS", 60, minimum=1, maximum=600
)
FAST1080_TARGET_SIZE_MB = _read_int_env(
    "FAST1080_TARGET_SIZE_MB", 44, minimum=5, maximum=48
)
FAST1080_TARGET_SIZE_BYTES = FAST1080_TARGET_SIZE_MB * 1024 * 1024
FAST1080_PROCESS_TIMEOUT_SECONDS = _read_int_env(
    "FAST1080_PROCESS_TIMEOUT_SECONDS", 900, minimum=60, maximum=7200
)

RESTORE_MAX_INPUT_MB = _read_int_env(
    "RESTORE_MAX_INPUT_MB", 20, minimum=1, maximum=20
)
RESTORE_MAX_INPUT_BYTES = RESTORE_MAX_INPUT_MB * 1024 * 1024
RESTORE_MAX_DURATION_SECONDS = _read_int_env(
    "RESTORE_MAX_DURATION_SECONDS", 60, minimum=1, maximum=600
)
RESTORE_TARGET_SIZE_MB = _read_int_env(
    "RESTORE_TARGET_SIZE_MB", 44, minimum=5, maximum=48
)
RESTORE_TARGET_SIZE_BYTES = RESTORE_TARGET_SIZE_MB * 1024 * 1024
RESTORE_PROCESS_TIMEOUT_SECONDS = _read_int_env(
    "RESTORE_PROCESS_TIMEOUT_SECONDS", 1800, minimum=60, maximum=14400
)
RESTORE_MAX_TEXT_MASK_PERCENT = _read_float_env(
    "RESTORE_MAX_TEXT_MASK_PERCENT", 10.0, minimum=1.0, maximum=25.0
)

STATUS_MESSAGE_DELETE_DELAY = _read_float_env(
    "STATUS_MESSAGE_DELETE_DELAY", 2.0, minimum=0.0, maximum=60.0
)

INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS = _read_bool_env(
    "INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS", True
)

HEARTBEAT_WRITE_INTERVAL_SECONDS = _read_int_env(
    "HEARTBEAT_WRITE_INTERVAL_SECONDS", 60, minimum=30, maximum=3600
)
WATCHDOG_CHECK_INTERVAL_SECONDS = _read_int_env(
    "WATCHDOG_CHECK_INTERVAL_SECONDS", 300, minimum=60, maximum=3600
)
HEARTBEAT_STALE_SECONDS = _read_int_env(
    "HEARTBEAT_STALE_SECONDS", 600, minimum=120, maximum=7200
)
SUPERVISOR_RESTART_DELAY_SECONDS = _read_int_env(
    "SUPERVISOR_RESTART_DELAY_SECONDS", 10, minimum=1, maximum=300
)
SUPERVISOR_MAX_RESTARTS = _read_int_env(
    "SUPERVISOR_MAX_RESTARTS", 5, minimum=1, maximum=50
)
SUPERVISOR_RESTART_WINDOW_SECONDS = _read_int_env(
    "SUPERVISOR_RESTART_WINDOW_SECONDS", 600, minimum=60, maximum=86400
)

FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "ffmpeg").strip() or "ffmpeg"
FFPROBE_BINARY = os.getenv("FFPROBE_BINARY", "ffprobe").strip() or "ffprobe"

APP_NAME = "MediaLab"
APP_VERSION = "2.4.0-alpha.3"
