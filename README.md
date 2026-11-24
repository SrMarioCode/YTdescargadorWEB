# YTDescarga (FastAPI + Jinja2)

Aplicación web para descargar videos y audio de YouTube usando **FastAPI**, **Jinja2** y **yt-dlp**.

## Características

- Interfaz web con **Jinja2** y progreso en tiempo real.
- Descarga directa en un clic o vista previa con selección de calidad (4K, 1080p, 720p, 480p, 360p y solo audio MP3).
- **FFmpeg incluido automáticamente** vía `imageio-ffmpeg` (no necesitas instalarlo aparte para combinar video HD + audio ni para extraer MP3).
- Medidas para reducir bloqueos de YouTube:
  - Rotación automática de clientes de reproductor en `yt-dlp` (`default`, `android_vr`/`web`, `tv`/`ios`).
  - Cabeceras HTTP modernas, reintentos automáticos y `geo_bypass`.
  - Límite de peticiones por IP (rate limiting) para evitar saturar YouTube.
  - Soporte automático de `cookies.txt`: si alguna vez YouTube pide verificación en tu IP, coloca un archivo `cookies.txt` (formato Netscape) en la raíz del proyecto y la app lo detectará automáticamente.

## Cómo ejecutarlo

```powershell
.\.venv\Scripts\activate
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Luego abre `http://127.0.0.1:8000` en tu navegador.
