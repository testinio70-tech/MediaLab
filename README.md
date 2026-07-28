# MediaLab

Bot modular de descarga multimedia para Telegram. La versión 2.2.0 concentra el soporte en TikTok y separa cada tipo de contenido:

- **Videos:** selector entre TikWM Original y yt-dlp.
- **Publicaciones fotográficas `/photo/`:** descarga mediante gallery-dl y envío como álbum de Telegram.
- **Limpieza automática:** elimina archivos enviados correctamente y limpia restos antiguos cada ocho horas.

## Estado del proyecto

- Plataforma disponible: TikTok.
- Videos con selector de motores mediante botones.
- Carruseles fotográficos enviados en grupos de hasta 10 imágenes.
- Peso total y resolución de videos cuando están disponibles.
- Borrado inmediato después de un envío exitoso.
- Conservación de archivos cuando Telegram falla o el archivo supera el límite.
- Limpieza preventiva cada 8 horas para archivos con más de 24 horas.
- Usuarios permitidos configurables desde `.env`.

## Estructura

```text
C:\MediaLab
├── Bot
│   ├── bot.py
│   ├── config.py
│   ├── handlers.py
│   ├── media_info.py
│   ├── models.py
│   ├── requirements.txt
│   ├── utils.py
│   ├── engines
│   │   ├── base.py
│   │   └── tiktok
│   │       ├── tikwm.py
│   │       └── ytdlp.py
│   └── services
│       ├── __init__.py
│       ├── file_cleanup.py
│       └── tiktok_photos.py
├── Cookies
├── Downloads
│   └── TikTok
│       └── Photos
├── Logs
└── Tools
    └── FFmpeg
```

Los motores de video conservan su estructura. Las fotos y la limpieza se implementan como servicios independientes.

## Requisitos

- Windows.
- Python 3.8 o posterior.
- FFmpeg y ffprobe disponibles en `PATH`.
- Un bot creado con `@BotFather`.

Comprueba las herramientas:

```powershell
python --version
ffmpeg -version
ffprobe -version
```

## Instalación

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
yt-dlp>=2026.7.4
gallery-dl>=1.32.7,<2
```

`gallery-dl` se ejecuta únicamente al recibir una publicación fotográfica. No queda trabajando en segundo plano.

## Configuración

El archivo `Bot\.env` debe contener:

```dotenv
TELEGRAM_BOT_TOKEN=TU_TOKEN_REAL
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
FFPROBE_BINARY=ffprobe
```

Nunca publiques `.env`, cookies ni tokens.

## Iniciar MediaLab

```powershell
cd C:\MediaLab\Bot
& .\.venv\Scripts\Activate.ps1
python bot.py
```

Para detenerlo, presiona `Ctrl + C`.

## Videos de TikTok

1. Envía un enlace de video.
2. MediaLab mostrará:
   - `🌐 TikWM Original`
   - `🛠️ yt-dlp`
3. Selecciona un motor.
4. Telegram recibe el video.
5. Cuando Telegram confirma el envío, MediaLab elimina el archivo local y lo registra en consola.

Cuando el envío falla, el archivo se conserva para diagnóstico o reintento. Los videos que superan el límite configurado también permanecen en la computadora.

## Publicaciones fotográficas

Los enlaces que contienen `/photo/` no muestran el selector de video. MediaLab los procesa directamente con gallery-dl:

```text
Enlace /photo/
└── gallery-dl
    ├── descarga todas las imágenes
    ├── crea álbumes de 2 a 10 fotos
    ├── usa envío individual cuando solo existe una foto
    └── elimina los archivos después de completar todos los envíos
```

Telegram admite hasta 10 elementos por álbum. Una publicación de 17 imágenes se divide en dos envíos: 10 y 7.

Cada fotografía debe respetar el límite de 10 MB de Telegram. Si una imagen excede ese límite o el envío falla, los archivos se conservan temporalmente y el bot informa la carpeta local.

## Cookies opcionales

Los contenidos públicos normalmente funcionan sin cookies. Cuando sean necesarias, coloca un archivo Netscape en:

```text
C:\MediaLab\Cookies\tiktok.txt
```

También se reconocen:

```text
cookies-tiktok.txt
cookies.txt
```

El mismo archivo puede ser utilizado por yt-dlp y gallery-dl.

## Limpieza automática

MediaLab aplica dos niveles de limpieza:

### Después de un envío exitoso

- Cierra el archivo.
- Lo elimina de la computadora.
- Registra la ruta y el tamaño en el log.
- Intenta eliminar las carpetas temporales vacías.

### Limpieza periódica

- Se ejecuta al iniciar el bot.
- Vuelve a ejecutarse cada 8 horas.
- Examina únicamente `Downloads` y `Bot\temp`.
- Borra archivos con más de 24 horas.
- No carga videos o imágenes en RAM.
- No ejecuta FFmpeg.
- No necesita APScheduler ni otra dependencia de programación.

Ejemplos de log:

```text
INFO | services.file_cleanup | Archivo enviado y eliminado: ...
INFO | services.file_cleanup | Limpieza periódica terminada: 4 archivos eliminados, 90534122 bytes liberados.
```

## Límites configurados

En `Bot\config.py`:

```text
Videos para Telegram: 50 MB
Fotos para Telegram: 10 MB por imagen
Tiempo de descarga: 10 minutos
Intervalo de limpieza: 8 horas
Retención de seguridad: 24 horas
```

## Seguridad de la limpieza

El servicio solamente permite eliminar rutas ubicadas dentro de:

```text
C:\MediaLab\Downloads
C:\MediaLab\Bot\temp
```

Una ruta fuera de esas carpetas se rechaza y se registra como error. Esto evita que un resultado defectuoso pueda borrar archivos ajenos al proyecto.

## Solución de problemas

### gallery-dl no está instalado

Dentro de `.venv`:

```powershell
python -m pip install --upgrade gallery-dl
python -m gallery_dl --version
```

### Una publicación `/photo/` no descarga

Actualiza gallery-dl:

```powershell
python -m pip install --upgrade gallery-dl
```

Después prueba nuevamente. Algunos contenidos pueden requerir cookies.

### El archivo no fue eliminado

Revisa la consola. MediaLab conserva el archivo cuando:

- Telegram no confirma el envío;
- el archivo excede el límite;
- Windows mantiene el archivo bloqueado;
- la ruta no pertenece a las carpetas administradas.

La limpieza periódica volverá a intentarlo después de que el archivo alcance 24 horas.

### yt-dlp muestra `Unsupported URL` en `/photo/`

Es esperado. Los enlaces fotográficos son desviados a gallery-dl antes de llegar a los motores de video.

## Desarrollo con ramas

La rama estable es `main`. Esta actualización debe probarse primero en:

```text
feature/tiktok-photos-cleanup
```

Pruebas mínimas antes de integrar:

1. Video con TikWM Original.
2. Video con yt-dlp.
3. Carrusel de 2 a 10 fotos.
4. Carrusel de más de 10 fotos.
5. Confirmar eliminación después de envío exitoso.
6. Simular un fallo de Telegram y comprobar que los archivos permanezcan.
7. Verificar que `git status` no incluya descargas, cookies ni `.env`.

## Uso responsable

Utiliza MediaLab únicamente para contenido que tengas derecho a descargar y respeta las condiciones de cada plataforma.
