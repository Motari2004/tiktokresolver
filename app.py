import base64
import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from downloader import resolve_tiktok

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tikresolver")

# ------------------------------------------------------------------
# App
# ------------------------------------------------------------------
app = FastAPI(
    title="TikTok No-Watermark Resolver",
    version="1.0.0",
    description="Resolve TikTok URLs to direct MP4 download links.",
)

# CORS — allow any origin by default; tighten via env var if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

TEMPLATES = Path(__file__).parent / "templates"

# Optional API key protection (set API_KEY in Render env vars to enable)
API_KEY = os.getenv("API_KEY")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def filename_from_token(url: str) -> str:
    """Extract the original filename from the JWT payload in the URL."""
    try:
        token = url.split("token=")[1].split("&")[0]
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("filename", "video.mp4")
    except Exception:
        return "video.mp4"


def check_api_key(request: Request):
    """Reject requests without a valid X-API-Key when API_KEY is set."""
    if not API_KEY:
        return
    if request.headers.get("x-api-key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------
class URLInput(BaseModel):
    url: str


# ------------------------------------------------------------------
# Web UI
# ------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------
# API v1
# ------------------------------------------------------------------
@app.post("/api/v1/resolve")
async def resolve_v1(data: URLInput, request: Request):
    """
    Resolve a TikTok URL to a direct download link.
    """
    check_api_key(request)

    if not data.url.strip():
        raise HTTPException(status_code=400, detail="Empty URL")

    logger.info("Resolve request: %s", data.url)

    try:
        link = await resolve_tiktok(data.url)
    except Exception as e:
        logger.exception("Scrape failed for %s", data.url)
        raise HTTPException(status_code=500, detail=f"Scrape error: {e}")

    if not link:
        logger.warning("No link captured for %s", data.url)
        raise HTTPException(status_code=400, detail="Could not resolve video")

    filename = filename_from_token(link)
    logger.info("Captured download link: %s", link)

    return {
        "status": "ok",
        "download_url": link,
        "filename": filename,
        "source_url": data.url,
    }


@app.get("/api/v1/download")
async def download_v1(url: str, request: Request):
    """
    Stream the actual video file through this service.
    Hides the rapidcdn token and forces a clean filename.
    """
    check_api_key(request)

    if not url.startswith("https://d.rapidcdn.app/v2?token="):
        raise HTTPException(status_code=400, detail="Invalid download URL")

    filename = filename_from_token(url)

    async def stream():
        headers = {
            "User-Agent": "TelegramBot (like TwitterBot)",
            "Referer": "https://snaptik.app/",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            async with client.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes(65536):
                    yield chunk

    return StreamingResponse(
        stream(),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------
# Legacy alias (keeps old callers working)
# ------------------------------------------------------------------
@app.post("/api/resolve")
async def resolve_legacy(data: URLInput, request: Request):
    return await resolve_v1(data, request)


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ------------------------------------------------------------------
# Local dev
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)