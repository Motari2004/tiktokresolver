from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from downloader import resolve_tiktok

app = FastAPI()

TEMPLATES = Path(__file__).parent / "templates"

class URLInput(BaseModel):
    url: str

@app.get("/", response_class=HTMLResponse)
async def index():
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")

@app.post("/api/resolve")
async def resolve(data: URLInput):
    if not data.url.strip():
        raise HTTPException(status_code=400, detail="Empty URL")
    link = await resolve_tiktok(data.url)
    if not link:
        raise HTTPException(status_code=400, detail="Could not resolve video")
    return {"status": "ok", "download_url": link}

@app.get("/healthz")
async def healthz():
    return {"ok": True}