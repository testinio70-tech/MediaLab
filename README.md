# MediaLab

Bot modular de descarga multimedia para Telegram. La versión actual se concentra en TikTok y permite elegir entre dos motores independientes:

- **TikWM Original**, que solicita el archivo original mediante TikWM.
- **yt-dlp**, que descarga con el motor oficial de Python y selecciona la mejor calidad disponible.

Después de cada descarga, MediaLab muestra en Telegram el motor utilizado, el peso total del archivo y su resolución.

## Estado del proyecto

- Plataforma disponible: TikTok.
- Selector de motores mediante botones en Telegram.
- Descargas guardadas en `C:\MediaLab\Downloads\TikTok`.
- Usuarios permitidos configurables desde `.env`.
- Preparado para incorporar Instagram, YouTube, Facebook y X en módulos separados.

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
│   └── engines
│       ├── base.py
│       └── tiktok
│           ├── tikwm.py
│           └── ytdlp.py
├── Cookies
├── Downloads
│   └── TikTok
└── Logs
```

## Requisitos

- Windows.
- Python instalado y disponible con el comando `python`.
- FFmpeg y `ffprobe` disponibles en `PATH`.
- Un bot creado con `@BotFather`.

Comprueba las herramientas:

```powershell
python --version
ffmpeg -version
ffprobe -version
```

## Instalación

Abre PowerShell:

```powershell
cd C:\MediaLab\Bot
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Dentro de `.env`, reemplaza el token de ejemplo:

```dotenv
TELEGRAM_BOT_TOKEN=PEGA_AQUI_TU_TOKEN
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
FFPROBE_BINARY=ffprobe
```

Nunca publiques el archivo `.env` ni el token del bot.

## Iniciar MediaLab

```powershell
cd C:\MediaLab\Bot
& .\.venv\Scripts\Activate.ps1
python bot.py
```

Para detenerlo, presiona `Ctrl + C`.

## Uso en Telegram

1. Envía un enlace de TikTok.
2. MediaLab mostrará dos botones:
   - `🌐 TikWM Original`
   - `🛠️ yt-dlp`
3. Pulsa un motor.
4. El bot descargará el video y mostrará:
   - motor utilizado;
   - peso total;
   - resolución;
   - título y autor cuando estén disponibles.

Cada selector dura 10 minutos y solo puede usarse una vez. Esto evita descargas duplicadas por pulsaciones repetidas.

## BotFather y los botones

Los botones de motores **no se crean en BotFather**. Son botones en línea generados directamente por `python-telegram-bot` desde `handlers.py`.

En BotFather solo es recomendable registrar los comandos visibles:

1. Abre `@BotFather`.
2. Envía `/setcommands`.
3. Selecciona tu bot.
4. Pega:

```text
start - Iniciar MediaLab
help - Mostrar ayuda
```

No necesitas crear comandos para TikWM o yt-dlp, porque se seleccionan mediante los botones que aparecen al enviar un enlace.

## Cookies opcionales para yt-dlp

Los videos públicos normalmente no necesitan cookies. Para casos que sí las requieran, coloca un archivo en formato Netscape dentro de:

```text
C:\MediaLab\Cookies\tiktok.txt
```

También se reconocen `cookies-tiktok.txt` y `cookies.txt`. La carpeta `Cookies` está excluida de Git para evitar publicar datos privados.

## Dependencias

`requirements.txt` usa versiones estables verificadas:

```text
python-telegram-bot>=22.8,<23
python-dotenv>=1.2.2,<2
requests>=2.34.2,<3
yt-dlp>=2026.7.4
```

Para actualizarlas dentro del entorno virtual:

```powershell
python -m pip install --upgrade -r requirements.txt
```

## Archivos grandes

MediaLab usa un límite configurado de 50 MB para el envío por Telegram. Cuando una descarga supera ese límite:

- el archivo permanece guardado en la computadora;
- el bot informa el peso, la resolución y la ruta local;
- no intenta enviarlo como video.

El límite se encuentra en `Bot\config.py`.

## Resolución

MediaLab toma primero los metadatos del motor. Cuando no están disponibles, usa `ffprobe` para inspeccionar el archivo final. Si `ffprobe` no puede ejecutarse, el bot mostrará `No disponible` sin detener la descarga.

## Seguridad

El repositorio ignora automáticamente:

- `.env`;
- entornos virtuales;
- cookies;
- descargas;
- registros;
- archivos temporales de yt-dlp.

Antes de publicar cambios, comprueba:

```powershell
git status
```

No confirmes archivos que contengan tokens, cookies o datos personales.

## Solución de problemas

### El bot no encuentra el token

Comprueba que exista:

```text
C:\MediaLab\Bot\.env
```

y que incluya:

```dotenv
TELEGRAM_BOT_TOKEN=TU_TOKEN_REAL
```

### `ffprobe` no se reconoce

Comprueba:

```powershell
where.exe ffprobe
```

Si no devuelve una ruta, revisa la instalación de FFmpeg o configura `FFPROBE_BINARY` con la ruta completa.

### yt-dlp deja de descargar

yt-dlp cambia con frecuencia. Actualízalo:

```powershell
python -m pip install --upgrade yt-dlp
```

### El selector expiró

Envía otra vez el enlace. Los botones vencen después de 10 minutos y dejan de ser válidos después del primer uso.

## Desarrollo con ramas

La rama `main` conserva la versión estable. Las mejoras se desarrollan en ramas separadas y se integran después de probarlas.

Ejemplo:

```text
main
└── feature/tiktok-engine-selector
```

## Uso responsable

Utiliza MediaLab únicamente para contenido que tengas derecho a descargar y respeta las condiciones de cada plataforma.
