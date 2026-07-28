from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path

from config import (
    CLEANUP_INTERVAL_SECONDS,
    CLEANUP_MAX_AGE_SECONDS,
    DOWNLOADS_FOLDER,
    TEMP_FOLDER,
)


logger = logging.getLogger(__name__)

_MANAGED_ROOTS = (DOWNLOADS_FOLDER, TEMP_FOLDER)


def delete_sent_file(file_path: Path | None) -> bool:
    """Elimina un archivo enviado, siempre que pertenezca a MediaLab."""

    if file_path is None:
        return False

    path = Path(file_path)
    if not _is_managed_path(path):
        logger.error(
            "Se rechazó la eliminación de una ruta fuera de MediaLab: %s",
            path,
        )
        return False

    try:
        if not path.exists():
            logger.info("El archivo enviado ya no existe: %s", path)
            return True

        if not path.is_file():
            logger.warning("La ruta enviada no es un archivo: %s", path)
            return False

        size = path.stat().st_size
        path.unlink()
        logger.info(
            "Archivo enviado y eliminado: %s (%s bytes)",
            path,
            size,
        )
        _remove_empty_parents(path.parent)
        return True
    except OSError:
        logger.exception("No se pudo eliminar el archivo enviado: %s", path)
        return False


def delete_sent_files(file_paths: Iterable[Path]) -> tuple[int, int]:
    """Elimina varios archivos enviados y devuelve (eliminados, fallidos)."""

    deleted = 0
    failed = 0

    for file_path in file_paths:
        if delete_sent_file(file_path):
            deleted += 1
        else:
            failed += 1

    logger.info(
        "Limpieza posterior al envío: %s eliminados, %s pendientes.",
        deleted,
        failed,
    )
    return deleted, failed


def cleanup_old_files() -> tuple[int, int]:
    """Elimina archivos administrados con más de la retención configurada."""

    cutoff = time.time() - CLEANUP_MAX_AGE_SECONDS
    deleted_files = 0
    released_bytes = 0

    for root in _MANAGED_ROOTS:
        if not root.exists():
            continue

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            try:
                stat = path.stat()
                if stat.st_mtime >= cutoff:
                    continue

                size = stat.st_size
                path.unlink()
                deleted_files += 1
                released_bytes += size
            except FileNotFoundError:
                continue
            except OSError:
                logger.exception(
                    "No se pudo eliminar un archivo antiguo: %s",
                    path,
                )

        _remove_empty_directories(root)

    logger.info(
        "Limpieza periódica terminada: %s archivos eliminados, "
        "%s bytes liberados.",
        deleted_files,
        released_bytes,
    )
    return deleted_files, released_bytes


async def periodic_cleanup_loop() -> None:
    """Ejecuta una limpieza al iniciar y después cada ocho horas."""

    while True:
        try:
            await asyncio.to_thread(cleanup_old_files)
        except asyncio.CancelledError:
            logger.info("Limpiador periódico detenido correctamente.")
            raise
        except Exception:
            logger.exception("Falló una ejecución del limpiador periódico.")

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def _is_managed_path(path: Path) -> bool:
    absolute_path = os.path.normcase(os.path.abspath(path))

    for root in _MANAGED_ROOTS:
        absolute_root = os.path.normcase(os.path.abspath(root))
        try:
            if os.path.commonpath((absolute_path, absolute_root)) == absolute_root:
                return True
        except ValueError:
            continue

    return False


def _remove_empty_parents(directory: Path) -> None:
    current = directory

    while _is_managed_path(current):
        if any(_same_path(current, root) for root in _MANAGED_ROOTS):
            return

        try:
            current.rmdir()
        except OSError:
            return

        current = current.parent


def _remove_empty_directories(root: Path) -> None:
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )

    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
        os.path.abspath(second)
    )
