"""
Scribd Download API Server
Simple FastAPI server for downloading Scribd documents.
Can be used standalone or alongside the Telegram bot.
"""

import asyncio
import os
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from downloader import download_scribd_document, extract_doc_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Scribd Downloader API",
    description="API server to download Scribd documents as PDF",
    version="1.0.0",
)

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
COOKIES_PATH = os.environ.get("COOKIES_PATH", "")

# Track active downloads
active_jobs: dict[str, dict] = {}


class DownloadRequest(BaseModel):
    url: str
    quality: int = 90


class DownloadResponse(BaseModel):
    success: bool
    message: str
    doc_id: str | None = None
    title: str | None = None
    pages: int | None = None
    download_url: str | None = None
    error: str | None = None


@app.get("/")
async def root():
    return {"status": "ok", "service": "Scribd Downloader API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy", "active_downloads": len(active_jobs)}


@app.post("/download", response_model=DownloadResponse)
async def download(req: DownloadRequest):
    """Download a Scribd document and return the PDF."""
    doc_id = extract_doc_id(req.url)
    if not doc_id:
        raise HTTPException(status_code=400, detail="Invalid Scribd URL")
    
    # Check if already downloading
    if doc_id in active_jobs:
        return DownloadResponse(
            success=False,
            message="This document is already being downloaded",
            doc_id=doc_id,
        )
    
    active_jobs[doc_id] = {"status": "downloading", "started": True}
    
    try:
        cookies_path = COOKIES_PATH if COOKIES_PATH and os.path.exists(COOKIES_PATH) else None
        result = await download_scribd_document(
            url=req.url,
            output_dir=DOWNLOAD_DIR,
            cookies_json=cookies_path,
            quality=req.quality,
        )
        
        if result["success"]:
            return DownloadResponse(
                success=True,
                message="Download complete",
                doc_id=doc_id,
                title=result["title"],
                pages=result["pages"],
                download_url=f"/file/{doc_id}",
            )
        else:
            return DownloadResponse(
                success=False,
                message="Download failed",
                doc_id=doc_id,
                error=result["error"],
            )
    finally:
        active_jobs.pop(doc_id, None)


@app.get("/file/{doc_id}")
async def get_file(doc_id: str):
    """Serve a downloaded PDF file."""
    # Find the file
    for f in os.listdir(DOWNLOAD_DIR):
        if f.endswith(f"_{doc_id}.pdf"):
            path = os.path.join(DOWNLOAD_DIR, f)
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=f,
            )
    raise HTTPException(status_code=404, detail="File not found")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
