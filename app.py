import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from downloader import resolve_tiktok

# ------------------------------------------------------------------
# Logging setup — prints to stdout, which Render captures in the
# "Logs" tab of your service dashboard.
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tikresolver")

# ------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------
app = FastAPI(title="TikTok No-Watermark Resolver")

TEMPLATES = Path(__file__).parent / "templates"


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------
class URLInput(BaseModel):
    url: str


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the single-page UI."""
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


@app.post("/api/resolve")
async def resolve(data: URLInput):
    """
    Accept a TikTok URL, scrape Snaptik, and return the direct
    rapidcdn download link.
    """
    if not data.url.strip():
        logger.warning("Empty URL received")
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

    logger.info("Captured download link: %s", link)
    return {"status": "ok", "download_url": link}


@app.get("/healthz")
async def healthz():
    """Health check endpoint used by Render."""
    return {"ok": True}


# ------------------------------------------------------------------
# Local dev entrypoint (optional)
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)