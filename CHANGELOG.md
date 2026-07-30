# Historial de cambios

## 2.4.0-alpha.5

- Herramienta reducida a dos modos claros: Restauración fiel y Restauración IA
  HD.
- Detección adaptativa de filtros de aplicación, tinte dominante, saturación
  excesiva e iluminación plana.
- Corrección cromática temporal con fuerza separada para fondo y persona.
- Detección de logotipos pequeños asociados a texto y eliminación completa de
  la sobreimpresión.
- LaMa local para reconstruir trazos que cruzan piel o ropa; OpenCV conserva la
  ruta rápida cuando el texto está solamente sobre el fondo.
- Real-ESRGAN 2× local mediante DirectML para el modo IA HD.
- Mezcla conservadora del resultado neuronal con el fotograma real: mayor
  fuerza en fondo, menor en cuerpo y mínima en rostro.
- Protección facial adicional con YuNet para bloquear cambios de identidad,
  forma y proporciones.
- Cola de un trabajo y mensajes de Telegram con estimación explícita de proceso
  prolongado.
- Instalador verificable para los modelos grandes guardados fuera de Git.
- Eliminación durante desarrollo de motores x4 y conversiones x2 incompatibles
  que producían mosaicos, suavizado excesivo o errores de carga.

## 2.4.0-alpha.4

- Sustitución del detector heurístico de bordes por PP-OCRv3 de OpenCV Zoo.
- Protección fotograma por fotograma de rostro, cabello, manos y cuerpo con
  PP-HumanSeg antes de construir cualquier máscara de texto.
- Regla de identidad bloqueada: la reconstrucción nunca puede intersectar la
  silueta humana protegida.
- Eliminación del contraste local CLAHE y del enfoque global que amplificaban
  grano, halos y defectos de compresión.
- Limpieza y microenfoque suave aplicados solamente al fondo.
- Codificación de restauraciones con libx264 `slow` para priorizar calidad.
- Mensajes de Telegram con advertencia de proceso lento, etapa y porcentaje.
- Modelos ONNX locales, huellas SHA-256 y licencia Apache 2.0 documentados.
- Pruebas de exclusión de personas, modelos DNN, progreso, audio, dimensiones
  y procesamiento integral.

## 2.4.0-alpha.3

- Nuevo comando `/restorevideo` y acceso desde el menú de mejoras.
- Tres acabados: Natural, Natural HD y Natural HD+.
- Revisión de todos los fotogramas para detectar trazos de texto sobrepuesto.
- Reconstrucción conservadora mediante máscaras e inpainting de OpenCV.
- Protección que descarta máscaras superiores al porcentaje configurado.
- Balance de blancos y saturación suavizados entre fotogramas.
- Contraste local, reducción ligera de ruido y enfoque moderado.
- Ampliación opcional de 720p a 1080p, sin 2K ni 4K.
- Copia del audio original cuando su códec es compatible con MP4.
- Codificación mediante NVENC únicamente después de una prueba real de capacidad,
  con respaldo automático en libx264.
- Pruebas unitarias de color, texto y dimensiones, más una prueba integral con
  video y audio sintéticos.

## 2.4.0-alpha.2

- Menú interactivo de mejoras y nueva opción Super rápido 1080.
- Cola independiente para tareas de mejora.
- Escalado Lanczos con FFmpeg hasta 1080p.
- Aceleración NVENC con respaldo libx264.
- Recepción de videos normales y documentos de video desde Telegram.
- Heartbeat y supervisor de proceso para mejorar la disponibilidad.

## 2.3.0

- Cola global de descargas para varios usuarios con uno o más trabajadores configurables.
- Un solo trabajo activo o pendiente por usuario de forma predeterminada.
- Protección contra enlaces duplicados y saturación de la cola.
- Mensajes temporales con estados: en espera, descargando y enviando.
- Eliminación automática del mensaje temporal después de un envío exitoso.
- Los resultados de limpieza permanecen únicamente en la consola y en los logs.
- Soporte para enlaces individuales de Instagram: publicaciones, Reels, videos y carruseles.
- Descarga de Instagram mediante gallery-dl con API REST de mayor resolución.
- Selección explícita de `bestvideo*+bestaudio/best` para videos de Instagram.
- Fallback automático a yt-dlp cuando gallery-dl falla en Reels o videos.
- Entrega de Instagram como documentos sin compresión de Telegram por defecto.
- Cookies opcionales separadas para Instagram.
- Preparación de una lista de usuarios privilegiados para funciones futuras.
- Versión actualizada a MediaLab 2.3.0.

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
