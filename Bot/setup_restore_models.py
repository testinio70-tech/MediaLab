from __future__ import annotations

import hashlib
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODEL_ROOT = ROOT / "Tools" / "Models"


@dataclass(frozen=True, slots=True)
class DownloadableModel:
    name: str
    url: str
    destination: Path
    sha256: str


MODELS = (
    DownloadableModel(
        name="LaMa de OpenCV",
        url=(
            "https://huggingface.co/opencv/inpainting_lama/resolve/main/"
            "inpainting_lama_2025jan.onnx?download=true"
        ),
        destination=(
            MODEL_ROOT / "LaMa" / "inpainting_lama_2025jan.onnx"
        ),
        sha256=(
            "7df918ac3921d3daf0aae1d219776cf0dc4e4935f035af81841b40adcf74fdf2"
        ),
    ),
    DownloadableModel(
        name="Real-ESRGAN 2× ONNX",
        url=(
            "https://huggingface.co/SceneWorks/real-esrgan-onnx/resolve/main/"
            "real_esrgan_x2.onnx?download=true"
        ),
        destination=(
            MODEL_ROOT / "RealESRGAN" / "real_esrgan_x2.onnx"
        ),
        sha256=(
            "7115ba92e8a1bfa63d68558ef006ef3d91273a068d321b1439f8bb1c9179002c"
        ),
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _install(model: DownloadableModel) -> None:
    if (
        model.destination.is_file()
        and _sha256(model.destination) == model.sha256
    ):
        print(f"[OK] {model.name}: ya está instalado y verificado.")
        return

    model.destination.parent.mkdir(parents=True, exist_ok=True)
    partial = model.destination.with_suffix(
        model.destination.suffix + ".part"
    )
    partial.unlink(missing_ok=True)
    model.destination.unlink(missing_ok=True)
    print(f"[DESCARGA] Descargando {model.name}...")
    try:
        with urllib.request.urlopen(model.url, timeout=120) as response:
            with partial.open("wb") as target:
                shutil.copyfileobj(response, target, 1024 * 1024)
        actual_hash = _sha256(partial)
        if actual_hash != model.sha256:
            raise RuntimeError(
                f"Huella incorrecta para {model.name}: {actual_hash}"
            )
        partial.replace(model.destination)
    finally:
        partial.unlink(missing_ok=True)
    print(f"[OK] {model.name}: instalado y verificado.")


def main() -> None:
    for model in MODELS:
        _install(model)
    print("\n[OK] Modelos de restauración listos.")


if __name__ == "__main__":
    main()
