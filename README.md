# MediaLab

Bot modular de descarga multimedia para Telegram. MediaLab 2.3.0 admite TikTok e Instagram, incorpora una cola global para varios usuarios y mantiene la limpieza de archivos únicamente en consola.

## Plataformas y contenido

### TikTok

- Videos con selector entre **TikWM Original** y **yt-dlp**.
- Carruseles de fotos mediante **gallery-dl**.
- Resolución automática de enlaces cortos `/t/`, `vm.tiktok.com` y `vt.tiktok.com`.

### Instagram

- Publicaciones individuales `/p/`.
- Reels `/reel/`.
- Videos `/tv/`.
- Carruseles de fotos, videos o contenido mixto.
- No descarga perfiles completos.

## Cola multiusuario

MediaLab registra cada solicitud y la procesa mediante una cola global. La configuración recomendada usa un solo trabajador para evitar que varias descargas compitan por la conexión de la PC.

```text
Usuario 1 -> trabajo activo
Usuario 2 -> posición 1 en espera
Usuario 1 termina
Usuario 2 comienza automáticamente
```

Por defecto:

- máximo 10 trabajos totales;
- un trabajo activo o pendiente por usuario;
- protección contra el mismo enlace repetido por el mismo usuario;
- un trabajador global.

Los mensajes temporales muestran estados como:

```text
Solicitud añadida a la cola
Procesando tu solicitud
Enviando a Telegram
Completado
```

Después de un envío exitoso, el mensaje temporal se elimina y únicamente permanece el contenido descargado.

## Calidad de Instagram

MediaLab solicita la máxima calidad que Instagram y sus endpoints expongan:

- `extractor.instagram.api=rest`, que gallery-dl documenta como el endpoint de mayor resolución;
- videos mediante yt-dlp con `bestvideo*+bestaudio/best`;
- sin transcodificar ni reducir resolución dentro de MediaLab;
- fallback automático a yt-dlp en Reels y videos cuando gallery-dl falla;
- advertencias de gallery-dl cuando una imagen o video parece tener calidad reducida.

Por defecto, los archivos de Instagram se envían como **documentos** para evitar la compresión de fotos que Telegram aplica al modo álbum visual. Esto conserva exactamente los bytes descargados por MediaLab.

> Instagram no siempre expone el archivo original exacto que se subió. MediaLab descarga la mejor versión disponible y no promete recuperar una fuente que Instagram no publique.

Para usar álbum visual en lugar de archivos sin compresión:

```dotenv
INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS=false
```

## Estructura principal

```text
C:\MediaLab
├── Bot
│   ├── bot.py
│   ├── config.py
│   ├── handlers.py
│   ├── requirements.txt
│   ├── engines
│   │   ├── instagram
│   │   │   └── ytdlp.py
│   │   └── tiktok
│   │       ├── tikwm.py
│   │       └── ytdlp.py
│   └── services
│       ├── download_queue.py
│       ├── file_cleanup.py
│       ├── instagram.py
│       ├── tiktok_photos.py
│       └── tiktok_urls.py
├── Cookies
├── Downloads
│   ├── Instagram
│   └── TikTok
└── Logs
```

## Instalación y actualización

Dentro del entorno virtual:

```powershell
cd C:\MediaLab\Bot
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade -r requirements.txt
```

Dependencias principales:

```text
python-telegram-bot>=22.8,<23
python-dotenv>=1.2.2,<2
requests>=2.34.2,<3
yt-dlp[default,curl-cffi]>=2026.7.4
gallery-dl>=1.32.8,<2
```

## Configuración `.env`

```dotenv
TELEGRAM_BOT_TOKEN=TU_TOKEN_REAL
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
TELEGRAM_PRIVILEGED_USER_IDS=123456789
DOWNLOAD_QUEUE_MAX_SIZE=10
DOWNLOAD_QUEUE_WORKERS=1
MAX_JOBS_PER_USER=1
STATUS_MESSAGE_DELETE_DELAY=2
INSTAGRAM_SEND_ORIGINALS_AS_DOCUMENTS=true
FFPROBE_BINARY=ffprobe
```

`TELEGRAM_PRIVILEGED_USER_IDS` queda preparado para funciones futuras como Stories o herramientas administrativas. MediaLab 2.3.0 no habilita descargas masivas ni perfiles completos.

## Cookies opcionales

### TikTok

```text
C:\MediaLab\Cookies\tiktok.txt
```

Alternativas:

```text
cookies-tiktok.txt
cookies.txt
```

### Instagram

```text
C:\MediaLab\Cookies\instagram.txt
```

Alternativas:

```text
cookies-instagram.txt
cookies.txt
```

Deben estar en formato Netscape. Nunca publiques cookies, `.env` ni el token de Telegram.

## Iniciar MediaLab

```powershell
cd C:\MediaLab\Bot
& .\.venv\Scripts\Activate.ps1
python bot.py
```

Para detenerlo, presiona `Ctrl + C`.

## Limpieza

Los archivos enviados correctamente se eliminan de la PC. El resultado de esa limpieza se registra únicamente en consola:

```text
INFO | services.file_cleanup | Archivo enviado y eliminado: ...
INFO | services.file_cleanup | Limpieza posterior al envío: ...
```

El bot ya no añade líneas como `Archivo local eliminado` a los mensajes de Telegram.

La limpieza periódica sigue ejecutándose al iniciar y cada ocho horas, eliminando archivos administrados con más de 24 horas.

## Límites actuales

```text
Archivo para Telegram: 50 MB
Foto en álbum visual: 10 MB
Tiempo de descarga: 10 minutos
Capacidad de cola: 10 trabajos
Trabajadores predeterminados: 1
Trabajos por usuario: 1
```

## Pruebas mínimas antes de integrar

1. TikTok video con TikWM Original.
2. TikTok video con yt-dlp.
3. TikTok carrusel fotográfico.
4. Instagram Post con una foto.
5. Instagram Reel.
6. Instagram carrusel mixto.
7. Dos usuarios enviando solicitudes al mismo tiempo.
8. Mismo usuario repitiendo el mismo enlace.
9. Confirmar que la limpieza solo aparezca en consola.
10. Confirmar que `git status` no incluya descargas, cookies ni `.env`.
