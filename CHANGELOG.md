# Historial de cambios

## 2.2.1

- Impersonación de navegador Chrome para yt-dlp mediante `curl_cffi`.
- Resolución de desafíos JavaScript de TikTok y reducción de errores HTTP 403.
- Dependencias completas de yt-dlp guardadas en `requirements.txt`.
- Resolución automática de enlaces cortos de TikTok antes de clasificarlos.
- Compatibilidad con `www.tiktok.com/t/`, `vm.tiktok.com` y `vt.tiktok.com`.
- Detección correcta de carruseles `/photo/` compartidos mediante enlaces cortos.
- Envío de la URL final resuelta a TikWM Original y yt-dlp.
- Fallback seguro al enlace original cuando TikTok no entrega una redirección válida.
- Versión actualizada a MediaLab 2.2.1.
## 2.2.0

- Detección de publicaciones fotográficas de TikTok mediante rutas `/photo/`.
- Descarga de carruseles con gallery-dl sin modificar los motores de video.
- Envío de fotografías como álbumes de hasta 10 elementos.
- Borrado automático de videos e imágenes después de un envío exitoso.
- Conservación de archivos cuando Telegram falla o se supera un límite.
- Registro detallado de archivos eliminados y espacio liberado.
- Limpiador periódico cada 8 horas con retención de seguridad de 24 horas.
- Restricción de limpieza a las carpetas administradas por MediaLab.
- Ciclo de limpieza basado en asyncio, sin APScheduler.
- Versión actualizada a MediaLab 2.2.0.

## 2.1.0

- Selector de motores para enlaces de TikTok.
- Botones `TikWM Original` y `yt-dlp`.
- Implementación real e independiente de yt-dlp.
- Información de peso total y resolución en Telegram.
- Análisis de resolución mediante metadatos o ffprobe.
- Selectores de un solo uso con expiración de 10 minutos.
- Mensajes y manejo de errores mejorados.
- Dependencias verificadas y actualizadas.
- Nuevo archivo `.env.example`.
- README completo con instalación, BotFather y solución de problemas.
