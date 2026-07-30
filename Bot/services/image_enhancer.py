from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import (
    MAX_TELEGRAM_PHOTO_SIZE,
    PHOTO_AI_FOLDER,
    PHOTO_AI_MAX_DIMENSION,
    RESTORE_FACE_MODEL,
    RESTORE_PERSON_MODEL,
    RESTORE_SUPERRES_MODEL,
)
from services.video_restore import (
    FaceIdentityProtector,
    IdentityLockedSuperResolver,
    PersonProtectionSegmenter,
)


logger = logging.getLogger(__name__)
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")
PHOTO_AI_MODES = {
    "faithful": "Restauración fiel x2",
    "detail": "Detalle IA local x2",
}


@dataclass(slots=True)
class ImageBatchResult:
    success: bool
    message: str
    output_paths: list[Path] = field(default_factory=list)
    provider: str = ""
    error: str = ""


def target_dimensions(width: int, height: int) -> tuple[int, int]:
    """Duplica la imagen sin exceder el límite visual seguro de Telegram."""
    if width <= 0 or height <= 0:
        return 0, 0
    scale = min(2.0, PHOTO_AI_MAX_DIMENSION / max(width, height))
    scale = max(1.0, scale)
    target_width = max(2, int(round(width * scale)))
    target_height = max(2, int(round(height * scale)))
    return target_width, target_height


class ImageBatchEnhancer:
    def availability_text(self) -> str:
        missing = [
            path.name
            for path in (
                RESTORE_SUPERRES_MODEL,
                RESTORE_PERSON_MODEL,
                RESTORE_FACE_MODEL,
            )
            if not path.is_file()
        ]
        return "disponible" if not missing else "faltan " + ", ".join(missing)

    def create_workspace(self, user_id: int, batch_id: str) -> Path:
        safe_batch = _SAFE_TOKEN.sub("_", batch_id).strip("._") or "batch"
        workspace = PHOTO_AI_FOLDER / str(user_id) / safe_batch
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    async def process_batch_async(
        self,
        input_paths: list[Path],
        output_folder: Path,
        mode: str = "detail",
    ) -> ImageBatchResult:
        return await asyncio.to_thread(
            self.process_batch,
            input_paths,
            output_folder,
            mode,
        )

    def process_batch(
        self,
        input_paths: list[Path],
        output_folder: Path,
        mode: str = "detail",
    ) -> ImageBatchResult:
        if not input_paths:
            return ImageBatchResult(False, "El lote no contiene imágenes.")
        if mode not in PHOTO_AI_MODES:
            return ImageBatchResult(False, "Modo Foto IA desconocido.")
        availability = self.availability_text()
        if availability != "disponible":
            return ImageBatchResult(
                False,
                "Foto IA x2 no está disponible.",
                error=availability,
            )

        output_folder.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        try:
            resolver = IdentityLockedSuperResolver()
            for index, input_path in enumerate(input_paths, start=1):
                frame = self._read_image(input_path)
                height, width = frame.shape[:2]
                target_width, target_height = target_dimensions(width, height)

                person_mask = PersonProtectionSegmenter(
                    margin_fraction=0.015,
                ).detect(frame)
                face_mask = FaceIdentityProtector().detect(frame)
                if mode == "faithful":
                    enhanced = resolver.apply(
                        frame,
                        protected_mask=person_mask,
                        face_mask=face_mask,
                        target_width=target_width,
                        target_height=target_height,
                    )
                    enhanced = self._apply_faithful_finish(
                        enhanced,
                        face_mask=face_mask,
                    )
                else:
                    enhanced = resolver.apply(
                        frame,
                        protected_mask=person_mask,
                        face_mask=face_mask,
                        target_width=target_width,
                        target_height=target_height,
                        background_ai_weight=0.70,
                        person_ai_weight=0.55,
                        face_ai_weight=0.14,
                        background_detail_weight=0.45,
                        person_detail_weight=0.36,
                        face_detail_weight=0.10,
                    )
                    enhanced = self._apply_reference_finish(
                        enhanced,
                        face_mask=face_mask,
                    )

                output_path = output_folder / f"foto_ia_{index:02d}.jpg"
                self._write_telegram_jpeg(output_path, enhanced)
                outputs.append(output_path)

            return ImageBatchResult(
                True,
                f"{len(outputs)} imagen(es) mejoradas correctamente.",
                output_paths=outputs,
                provider=f"{PHOTO_AI_MODES[mode]} · {resolver.provider}",
            )
        except Exception as error:
            logger.exception("Foto IA x2 no pudo completar el lote.")
            return ImageBatchResult(
                False,
                "No se pudo completar el lote Foto IA x2.",
                output_paths=outputs,
                error=str(error),
            )

    @staticmethod
    def _read_image(path: Path) -> Any:
        raw = np.fromfile(path, dtype=np.uint8)
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            raise ValueError(f"Imagen no compatible: {path.name}")
        return frame

    @staticmethod
    def _write_telegram_jpeg(path: Path, frame: Any) -> None:
        for quality in (95, 92, 88, 84, 80):
            success, encoded = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            if not success:
                continue
            encoded.tofile(path)
            if path.stat().st_size <= MAX_TELEGRAM_PHOTO_SIZE:
                return
        raise ValueError(
            "La imagen mejorada supera el límite de Telegram incluso tras "
            "optimizar el JPEG."
        )

    @staticmethod
    def _apply_reference_finish(
        frame: Any,
        *,
        face_mask: Any,
    ) -> Any:
        """Acabado nítido y natural calibrado con la referencia aprobada."""
        # Una reducción mínima de ruido evita amplificar bloques de compresión,
        # conservando por separado la alta frecuencia reconstruida por la IA.
        clean = cv2.bilateralFilter(frame, 5, 10, 10)

        # Curva tonal: más profundidad y luz, con saturación ligeramente menor.
        hsv = cv2.cvtColor(clean, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] *= 0.88
        hsv[:, :, 2] = np.clip(
            (hsv[:, :, 2] - 128.0) * 1.07 + 136.0,
            0.0,
            255.0,
        )
        toned = cv2.cvtColor(
            np.clip(hsv, 0, 255).astype(np.uint8),
            cv2.COLOR_HSV2BGR,
        )

        # Separación de frecuencias: el desenfoque representa volumen y luz;
        # la diferencia contiene cabello, tejido, piel y contornos finos.
        low_frequency = cv2.GaussianBlur(toned, (0, 0), sigmaX=1.8)
        high_frequency = (
            toned.astype(np.float32)
            - low_frequency.astype(np.float32)
        )
        detail_weight = np.full(
            toned.shape[:2],
            0.45,
            dtype=np.float32,
        )

        # Evita que la máscara de enfoque endurezca facciones o altere identidad.
        resized_face = cv2.resize(
            face_mask,
            (toned.shape[1], toned.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32) / 255.0
        resized_face = cv2.GaussianBlur(resized_face, (0, 0), sigmaX=3.0)
        detail_weight *= 1.0 - resized_face * 0.48

        focused = (
            toned.astype(np.float32)
            + high_frequency * detail_weight[:, :, None]
        )

        # Realce estructural selectivo para hebras, costuras y superficies con
        # textura. La máscara cromática/oscura evita granular paredes y piel.
        structure = cv2.detailEnhance(
            toned,
            sigma_s=10,
            sigma_r=0.15,
        ).astype(np.float32)
        saturation = hsv[:, :, 1] / 255.0
        darkness = np.clip((150.0 - hsv[:, :, 2]) / 150.0, 0.0, 1.0)
        texture_mask = np.clip(
            np.maximum(saturation * 1.25, darkness),
            0.0,
            1.0,
        )
        texture_mask *= 1.0 - resized_face * 0.85
        structural_detail = structure - toned.astype(np.float32)
        finished = (
            focused
            + structural_detail * (0.14 * texture_mask[:, :, None])
        )
        return np.clip(finished, 0, 255).astype(np.uint8)

    @staticmethod
    def _apply_faithful_finish(
        frame: Any,
        *,
        face_mask: Any,
    ) -> Any:
        """Limpieza x2 conservadora sin reinterpretar texturas personales."""
        clean = cv2.bilateralFilter(frame, 5, 8, 8)
        low_frequency = cv2.GaussianBlur(clean, (0, 0), sigmaX=1.4)
        high_frequency = (
            clean.astype(np.float32)
            - low_frequency.astype(np.float32)
        )
        resized_face = cv2.resize(
            face_mask,
            (clean.shape[1], clean.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32) / 255.0
        resized_face = cv2.GaussianBlur(resized_face, (0, 0), sigmaX=3.0)
        detail_weight = 0.18 * (1.0 - resized_face * 0.55)
        finished = (
            clean.astype(np.float32)
            + high_frequency * detail_weight[:, :, None]
        )
        return np.clip(finished, 0, 255).astype(np.uint8)


PHOTO_AI_ENHANCER = ImageBatchEnhancer()
