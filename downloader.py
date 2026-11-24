from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

import yt_dlp

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "cookies.txt"
TEMP_ROOT = Path(tempfile.gettempdir()) / "yt_fastapi_downloads"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)

YOUTUBE_URL_RE = re.compile(
    r"^https?://(www\.|m\.|music\.)?(youtube\.com/(watch\?.*v=[\w-]{11}|shorts/[\w-]{11}|embed/[\w-]{11}|live/[\w-]{11})|youtu\.be/[\w-]{11})",
    re.IGNORECASE,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def get_ffmpeg_path() -> str | None:
    """Return path to ffmpeg binary (system PATH first, then imageio-ffmpeg)."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return None


def has_cookies_file() -> bool:
    return COOKIES_FILE.is_file() and COOKIES_FILE.stat().st_size > 0


def validate_youtube_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("Por favor, pega un enlace de YouTube.")
    if not YOUTUBE_URL_RE.match(cleaned):
        raise ValueError(
            "El enlace no parece ser un video válido de YouTube "
            "(ejemplo: https://www.youtube.com/watch?v=... o https://youtu.be/...)."
        )
    return cleaned


def sanitize_filename(name: str, max_len: int = 120) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]+', "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "video_youtube"
    return cleaned[:max_len]


def _base_ydl_opts(client_override: list[str] | None = None) -> dict[str, Any]:
    """Build yt-dlp options tuned for stability and minimizing YouTube blocks."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "nocheckcertificate": False,
    }

    if has_cookies_file():
        opts["cookiefile"] = str(COOKIES_FILE)

    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path

    if client_override:
        opts["extractor_args"] = {"youtube": {"player_client": client_override}}

    return opts


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "sign in to confirm" in lower or "bot" in lower or "cookies" in lower:
        return (
            "YouTube solicitó verificación temporal para este video. "
            "Intenta de nuevo en unos segundos o coloca un archivo cookies.txt en la carpeta del proyecto."
        )
    if "private video" in lower:
        return "Este video es privado y no se puede descargar."
    if "video unavailable" in lower or "unavailable" in lower:
        return "El video no está disponible o fue eliminado."
    if "age" in lower and ("restrict" in lower or "confirm" in lower):
        return "Este video tiene restricción de edad. Requiere cookies.txt para descargarlo."
    if "429" in lower or "too many requests" in lower:
        return "YouTube limitó temporalmente las peticiones. Espera un momento e intenta de nuevo."
    return f"No se pudo procesar el video: {msg.splitlines()[0][:180]}"


def _run_with_fallback(action: Callable[[dict[str, Any]], Any]) -> Any:
    """Execute a yt-dlp action, retrying with alternate YouTube player clients on transient blocks."""
    client_strategies: list[list[str] | None] = [
        None,  # Default yt-dlp strategy (most up-to-date)
        ["android_vr", "web"],
        ["tv", "ios"],
    ]
    last_error: Exception | None = None
    for clients in client_strategies:
        try:
            return action(_base_ydl_opts(client_override=clients))
        except Exception as exc:
            last_error = exc
            lower = str(exc).lower()
            # Don't retry permanent errors like private/deleted videos
            if "private video" in lower or "video unavailable" in lower:
                break
    assert last_error is not None
    raise RuntimeError(_friendly_error(last_error)) from last_error


def get_video_info(url: str) -> dict[str, Any]:
    clean_url = validate_youtube_url(url)

    def _extract(opts: dict[str, Any]) -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(clean_url, download=False)

    info = _run_with_fallback(_extract)
    ffmpeg_available = get_ffmpeg_path() is not None

    formats_raw = info.get("formats") or []
    available_heights: set[int] = set()
    for fmt in formats_raw:
        height = fmt.get("height")
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        if not height or vcodec == "none":
            continue
        # Without ffmpeg, only progressive streams (video + audio) are selectable
        if not ffmpeg_available and acodec == "none":
            continue
        if height in (2160, 1440, 1080, 720, 480, 360):
            available_heights.add(height)

    quality_options: list[dict[str, str]] = [
        {
            "id": "best_mp4",
            "label": "Mejor calidad disponible (MP4)" if ffmpeg_available else "Mejor calidad MP4",
            "type": "video",
        }
    ]
    for h in sorted(available_heights, reverse=True):
        tag = "4K" if h == 2160 else ("2K" if h == 1440 else f"{h}p")
        quality_options.append(
            {
                "id": f"mp4_{h}",
                "label": f"Video MP4 - {tag}",
                "type": "video",
            }
        )

    if ffmpeg_available:
        quality_options.append(
            {"id": "audio_mp3", "label": "Solo Audio (MP3 - 192 kbps)", "type": "audio"}
        )
    else:
        quality_options.append(
            {"id": "audio_m4a", "label": "Solo Audio (M4A)", "type": "audio"}
        )

    duration = info.get("duration") or 0
    mins, secs = divmod(int(duration), 60)
    hours, mins = divmod(mins, 60)
    duration_str = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"

    return {
        "id": info.get("id", ""),
        "title": info.get("title", "Video de YouTube"),
        "channel": info.get("uploader") or info.get("channel") or "YouTube",
        "duration": duration_str,
        "thumbnail": info.get("thumbnail", ""),
        "view_count": info.get("view_count"),
        "qualities": quality_options,
        "ffmpeg_available": ffmpeg_available,
    }


def _format_selector(quality: str, ffmpeg_available: bool) -> dict[str, Any]:
    if quality == "audio_mp3" and ffmpeg_available:
        return {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    if quality == "audio_m4a" or quality == "audio_mp3":
        return {"format": "bestaudio[ext=m4a]/bestaudio/best"}

    if quality.startswith("mp4_"):
        try:
            height = int(quality.split("_", 1)[1])
        except ValueError:
            height = 720
        if ffmpeg_available:
            return {
                "format": (
                    f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
                ),
                "merge_output_format": "mp4",
            }
        return {
            "format": f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
        }

    # Default: best_mp4
    if ffmpeg_available:
        return {
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/best[ext=mp4]/best"
            ),
            "merge_output_format": "mp4",
        }
    return {"format": "best[ext=mp4]/best"}


def download_video(
    url: str,
    quality: str = "best_mp4",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Path, str, Path]:
    """Download video into an isolated temp folder. Returns (file_path, download_name, temp_dir)."""
    clean_url = validate_youtube_url(url)
    job_dir = TEMP_ROOT / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_available = get_ffmpeg_path() is not None

    def _hook(d: dict[str, Any]) -> None:
        if not progress_callback:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = round((downloaded / total) * 100, 1) if total else 0.0
            speed = d.get("speed")
            eta = d.get("eta")
            progress_callback(
                {
                    "status": "downloading",
                    "progress": min(pct, 99.0),
                    "speed": f"{speed / (1024 * 1024):.1f} MB/s" if speed else "",
                    "eta": f"{int(eta)}s" if eta is not None else "",
                }
            )
        elif status == "finished":
            progress_callback(
                {
                    "status": "processing",
                    "progress": 99.0,
                    "speed": "",
                    "eta": "",
                }
            )

    def _do_download(opts: dict[str, Any]) -> dict[str, Any]:
        opts.update(_format_selector(quality, ffmpeg_available))
        opts["outtmpl"] = str(job_dir / "%(title).100s.%(ext)s")
        opts["progress_hooks"] = [_hook]
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(clean_url, download=True)

    try:
        info = _run_with_fallback(_do_download)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    files = [p for p in job_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not files:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("La descarga terminó pero no se encontró el archivo generado.")

    # Pick the largest output file (the merged final file)
    output_file = max(files, key=lambda p: p.stat().st_size)
    title = sanitize_filename(info.get("title") or output_file.stem)
    ext = output_file.suffix.lstrip(".") or "mp4"
    download_name = f"{title}.{ext}"
    return output_file, download_name, job_dir


def cleanup_temp_root() -> None:
    """Remove leftover temporary download folders on startup."""
    if not TEMP_ROOT.exists():
        return
    for child in TEMP_ROOT.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except Exception:
            pass
