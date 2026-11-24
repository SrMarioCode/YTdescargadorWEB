from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.background import BackgroundTask

from downloader import (
    cleanup_temp_root,
    download_video,
    get_ffmpeg_path,
    get_video_info,
    has_cookies_file,
    validate_youtube_url,
)

BASE_DIR = Path(__file__).resolve().parent

# In-memory job store and simple per-IP rate limiter
JOBS: dict[str, dict[str, Any]] = {}
RATE_LIMITS: dict[str, deque[float]] = defaultdict(deque)
JOB_TTL_SECONDS = 60 * 30  # Clean abandoned jobs after 30 minutes


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    quality: str = "best_mp4"


def _check_rate_limit(client_ip: str, bucket: str, max_requests: int, window_sec: int) -> None:
    key = f"{bucket}:{client_ip}"
    now = time.monotonic()
    q = RATE_LIMITS[key]
    while q and now - q[0] > window_sec:
        q.popleft()
    if len(q) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes seguidas. Espera unos segundos para evitar bloqueos.",
        )
    q.append(now)


def _prune_old_jobs() -> None:
    now = time.time()
    expired = [jid for jid, job in JOBS.items() if now - job["created_at"] > JOB_TTL_SECONDS]
    for jid in expired:
        job = JOBS.pop(jid, None)
        if job and job.get("temp_dir"):
            shutil.rmtree(job["temp_dir"], ignore_errors=True)


def _cleanup_job_files(job_id: str, temp_dir: Path) -> None:
    shutil.rmtree(temp_dir, ignore_errors=True)
    JOBS.pop(job_id, None)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_temp_root()
    yield
    cleanup_temp_root()


app = FastAPI(title="YTDescarga - Descargador de YouTube", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": "YTDescarga",
            "has_ffmpeg": get_ffmpeg_path() is not None,
            "has_cookies": has_cookies_file(),
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "ffmpeg": get_ffmpeg_path() is not None,
        "cookies_loaded": has_cookies_file(),
    }


@app.post("/api/info")
async def api_info(payload: InfoRequest, request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip, "info", max_requests=20, window_sec=60)
    _prune_old_jobs()

    try:
        validate_youtube_url(payload.url)
        info = await asyncio.to_thread(get_video_info, payload.url)
        return info
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _run_download_job(job_id: str, url: str, quality: str) -> None:
    def _on_progress(data: dict[str, Any]) -> None:
        job = JOBS.get(job_id)
        if job:
            job.update(data)

    try:
        file_path, download_name, temp_dir = await asyncio.to_thread(
            download_video, url, quality, _on_progress
        )
        job = JOBS.get(job_id)
        if job:
            job.update(
                {
                    "status": "ready",
                    "progress": 100.0,
                    "file_path": file_path,
                    "filename": download_name,
                    "temp_dir": temp_dir,
                    "speed": "",
                    "eta": "",
                }
            )
    except Exception as exc:
        job = JOBS.get(job_id)
        if job:
            job.update({"status": "error", "error": str(exc)})


@app.post("/api/download")
async def api_download(payload: DownloadRequest, request: Request) -> dict[str, str]:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip, "download", max_requests=6, window_sec=60)
    _prune_old_jobs()

    try:
        clean_url = validate_youtube_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "speed": "",
        "eta": "",
        "filename": "",
        "error": "",
        "file_path": None,
        "temp_dir": None,
        "created_at": time.time(),
    }

    asyncio.create_task(_run_download_job(job_id, clean_url, payload.quality))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def api_status(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Descarga no encontrada o expirada.")
    return {
        "status": job["status"],
        "progress": job["progress"],
        "speed": job["speed"],
        "eta": job["eta"],
        "filename": job["filename"],
        "error": job["error"],
    }


@app.get("/api/file/{job_id}")
async def api_file(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if not job or job["status"] != "ready" or not job["file_path"]:
        raise HTTPException(status_code=404, detail="El archivo aún no está listo o ya fue descargado.")

    file_path: Path = job["file_path"]
    temp_dir: Path = job["temp_dir"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="El archivo temporal ya no existe.")

    return FileResponse(
        path=file_path,
        filename=job["filename"],
        media_type="application/octet-stream",
        background=BackgroundTask(_cleanup_job_files, job_id, temp_dir),
    )
