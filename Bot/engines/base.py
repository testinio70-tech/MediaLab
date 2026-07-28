from abc import ABC, abstractmethod

from models import DownloadResult


class DownloadEngine(ABC):
    """
    Clase base para todos los motores de descarga.

    Cualquier plataforma o motor deberá heredar de esta clase.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Nombre del motor.
        """

    @property
    @abstractmethod
    def platform(self) -> str:
        """
        Plataforma soportada.
        """

    @abstractmethod
    def download(self, url: str) -> DownloadResult:
        """
        Descarga el contenido y devuelve un DownloadResult.
        """