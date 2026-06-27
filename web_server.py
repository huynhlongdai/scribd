"""
Scribd Downloader Web Server
Full-featured web interface with download, history, queue, stats, and account management.
"""

import asyncio
import os
import time
import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from downloader import download_scribd_document, get_document_info, extract_doc_id, extract_doc_title
import database as db
import account_manager as acct_mgr
import ai_helper
import scheduler
import gdrive

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scribd Downloader", version="4.0.0")

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
COOKIES_PATH = os.environ.get("COOKIES_PATH", "")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2"))

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Track active downloads
_active_count = 0
_active_lock = asyncio.Lock()


class DownloadRequest(BaseModel):
    url: str


class AddAccountRequest(BaseModel):
    email: str
    password: str = ""
    label: str = ""
    cookies_json: str = ""  # Optional: paste cookies JSON directly


class UpdateAccountRequest(BaseModel):
    status: str = ""       # active/disabled
    label: str = ""
    cookies_json: str = ""


# ═══════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the main web page."""
    return get_html()


@app.get("/api/health")
async def health():
    active_accounts = db.get_active_account_count()
    return {"status": "ok", "active_downloads": _active_count, "active_accounts": active_accounts}


@app.post("/api/info")
async def api_info(req: DownloadRequest):
    """Get document info without downloading (title, pages, etc.)."""
    # AI-powered URL parsing: try smart parse first
    parsed = ai_helper.smart_parse_input(req.url)
    if parsed["fixed_url"]:
        req.url = parsed["fixed_url"]
    doc_id = parsed.get("doc_id") or extract_doc_id(req.url)
    if not doc_id:
        raise HTTPException(400, "Link Scribd không hợp lệ")

    # Check if already downloaded (cache) — return info from DB
    cached = db.get_cached_download(doc_id)
    if cached:
        return {
            "success": True,
            "doc_id": doc_id,
            "title": cached["title"],
            "pages": cached["pages"],
            "file_size": cached["file_size"],
            "cached": True,
            "download_url": f"/api/file/{doc_id}",
        }

    # Get account cookies for probing
    cookies, account_id = acct_mgr.get_cookies_for_download()
    info = await get_document_info(req.url, cookies_list=cookies)

    if info["success"]:
        return {
            "success": True,
            "doc_id": info["doc_id"],
            "title": info["title"],
            "pages": info["pages"],
            "thumbnail": info.get("thumbnail"),
            "cached": False,
        }
    else:
        raise HTTPException(400, info.get("error", "Không lấy được thông tin tài liệu"))


@app.post("/api/download")
async def api_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    """Start a download or return cached result."""
    # AI-powered URL parsing
    parsed = ai_helper.smart_parse_input(req.url)
    if parsed["fixed_url"]:
        req.url = parsed["fixed_url"]
    doc_id = parsed.get("doc_id") or extract_doc_id(req.url)
    if not doc_id:
        raise HTTPException(400, "Link Scribd không hợp lệ")

    # Check cache first
    cached = db.get_cached_download(doc_id)
    if cached:
        return {
            "status": "cached",
            "doc_id": doc_id,
            "title": cached["title"],
            "pages": cached["pages"],
            "file_size": cached["file_size"],
            "download_url": f"/api/file/{doc_id}",
        }

    # Check if already downloading
    existing = db.get_download_by_doc_id(doc_id)
    if existing and existing["status"] == "downloading":
        return {"status": "downloading", "doc_id": doc_id, "message": "Đang tải..."}

    # Check concurrent limit
    global _active_count
    async with _active_lock:
        if _active_count >= MAX_CONCURRENT:
            queue_id = db.add_to_queue(doc_id, req.url, source="web")
            return {
                "status": "queued",
                "doc_id": doc_id,
                "queue_id": queue_id,
                "message": f"Đã thêm vào hàng đợi (vị trí #{db.get_queue_status()['waiting']})"
            }

    # Get account cookies (round-robin)
    cookies, account_id = acct_mgr.get_cookies_for_download()

    record_id = db.add_download(doc_id, req.url, source="web", account_id=account_id)
    background_tasks.add_task(_do_download, doc_id, req.url, record_id, cookies, account_id)
    return {"status": "started", "doc_id": doc_id, "record_id": record_id}


@app.get("/api/status/{doc_id}")
async def check_status(doc_id: str):
    """Check download status."""
    dl = db.get_download_by_doc_id(doc_id)
    if not dl:
        return {"status": "not_found"}
    result = {
        "status": dl["status"],
        "doc_id": doc_id,
        "title": dl["title"],
        "pages": dl["pages"],
    }
    if dl["status"] == "completed":
        result["download_url"] = f"/api/file/{doc_id}"
        result["file_size"] = dl["file_size"]
        result["duration"] = dl["duration_seconds"]
    elif dl["status"] == "failed":
        result["error"] = dl["error_message"]
    return result


@app.get("/api/file/{doc_id}")
async def get_file(doc_id: str):
    """Serve a downloaded PDF file."""
    cached = db.get_cached_download(doc_id)
    if cached and os.path.exists(cached["file_path"]):
        return FileResponse(
            cached["file_path"],
            media_type="application/pdf",
            filename=os.path.basename(cached["file_path"]),
        )
    for f in os.listdir(DOWNLOAD_DIR):
        if f.endswith(f"_{doc_id}.pdf"):
            return FileResponse(
                os.path.join(DOWNLOAD_DIR, f),
                media_type="application/pdf",
                filename=f,
            )
    raise HTTPException(404, "File không tìm thấy")


@app.get("/api/history")
async def get_history(limit: int = 50, offset: int = 0,
                      status: str = None, source: str = None):
    items = db.get_download_history(limit=limit, offset=offset,
                                     status=status, source=source)
    total = db.get_total_downloads()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/search")
async def search(q: str, limit: int = 20):
    items = db.search_downloads(q, limit=limit)
    return {"items": items, "query": q}


@app.get("/api/queue")
async def get_queue():
    status = db.get_queue_status()
    waiting = db.get_queue_items("waiting")
    processing = db.get_queue_items("processing")
    return {"status": status, "waiting": waiting, "processing": processing}


@app.get("/api/stats")
async def get_stats():
    summary = db.get_stats_summary()
    daily = db.get_daily_stats(30)
    return {"summary": summary, "daily": daily}


# ═══════════════════════════════════════════
# AI Helper API Endpoints
# ═══════════════════════════════════════════

class AIParseRequest(BaseModel):
    text: str


class AIRetryRequest(BaseModel):
    doc_id: str
    error: str = ""
    url: str = ""


@app.post("/api/ai/parse")
async def ai_parse(req: AIParseRequest):
    """AI-powered smart input parser. Extracts/fixes Scribd URLs from any text."""
    result = ai_helper.smart_parse_input(req.text)
    return result


@app.post("/api/ai/fix")
async def ai_fix(req: AIParseRequest):
    """AI URL fixer. Normalizes and repairs broken Scribd URLs."""
    result = ai_helper.fix_scribd_url(req.text)
    return result


@app.post("/api/ai/diagnose")
async def ai_diagnose(req: AIRetryRequest):
    """Diagnose a download error and suggest fixes."""
    diagnosis = ai_helper.diagnose_download_error(req.error, req.url, req.doc_id)
    alternatives = ai_helper.get_alternative_urls(req.doc_id) if req.doc_id else []
    return {"diagnosis": diagnosis, "alternatives": alternatives}


@app.post("/api/ai/retry")
async def ai_retry(req: AIRetryRequest, background_tasks: BackgroundTasks):
    """
    AI-assisted smart retry: diagnose error, fix URL, and retry download.
    """
    # Get diagnosis
    diagnosis = ai_helper.diagnose_download_error(req.error, req.url, req.doc_id)
    action = diagnosis.get("auto_fix_action", "retry")

    # Determine URL to use
    retry_url = req.url
    if action and action.startswith("retry_with_url:"):
        retry_url = action.split(":", 1)[1]

    doc_id = req.doc_id or extract_doc_id(retry_url)
    if not doc_id:
        return {"success": False, "diagnosis": diagnosis, "error": "Không có doc_id để retry"}

    # Handle cookie refresh
    if action == "refresh_cookies_and_retry":
        accounts = db.get_all_accounts()
        for acct in accounts:
            if acct["status"] == "active":
                try:
                    await acct_mgr.refresh_account_cookies(acct["id"])
                except Exception:
                    pass

    # Get fresh cookies
    cookies, account_id = acct_mgr.get_cookies_for_download()

    # Set longer timeout if needed
    extra_kwargs = {}
    if action == "retry_with_longer_timeout":
        extra_kwargs["timeout"] = 240  # Double the default

    # Start retry download
    record_id = db.add_download(doc_id, retry_url, source="web-ai-retry", account_id=account_id)
    background_tasks.add_task(_do_download, doc_id, retry_url, record_id, cookies, account_id)

    return {
        "success": True,
        "status": "retrying",
        "doc_id": doc_id,
        "url": retry_url,
        "diagnosis": diagnosis,
        "action_taken": action,
        "record_id": record_id,
    }


# ═══════════════════════════════════════════
# Account API Endpoints
# ═══════════════════════════════════════════

@app.get("/api/accounts")
async def get_accounts():
    """Get all accounts summary."""
    return acct_mgr.get_accounts_summary()


@app.post("/api/accounts")
async def add_account(req: AddAccountRequest):
    """Add a new Scribd account."""
    if not req.email:
        raise HTTPException(400, "Email là bắt buộc")

    # If cookies JSON provided directly, use that
    if req.cookies_json:
        try:
            cookies = json.loads(req.cookies_json)
            if not isinstance(cookies, list):
                raise ValueError("Cookies phải là danh sách")
            acct_id = acct_mgr.add_account_with_cookies(
                email=req.email,
                cookies=cookies,
                password=req.password,
                label=req.label
            )
            return {
                "success": True,
                "account_id": acct_id,
                "message": f"Đã thêm {req.email} với {len(cookies)} cookies"
            }
        except json.JSONDecodeError:
            raise HTTPException(400, "Cookies JSON không hợp lệ")

    # Otherwise save with password (login can be done later)
    if req.password:
        # Try auto-login
        result = await acct_mgr.add_account_with_login(
            email=req.email,
            password=req.password,
            label=req.label
        )
        return result
    else:
        # Just save email
        acct_id = db.add_account(email=req.email, label=req.label)
        return {
            "success": True,
            "account_id": acct_id,
            "message": f"Đã thêm {req.email} (chưa có cookies, cần login)"
        }


@app.put("/api/accounts/{account_id}")
async def update_account(account_id: int, req: UpdateAccountRequest):
    """Update an account."""
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "Tài khoản không tìm thấy")

    if req.status == "active":
        db.enable_account(account_id)
    elif req.status == "disabled":
        db.disable_account(account_id)

    if req.label:
        db.update_account(account_id, label=req.label)

    if req.cookies_json:
        try:
            cookies = json.loads(req.cookies_json)
            db.update_account_cookies(account_id, cookies)
        except json.JSONDecodeError:
            raise HTTPException(400, "Cookies JSON không hợp lệ")

    return {"success": True, "message": "Đã cập nhật tài khoản"}


@app.delete("/api/accounts/{account_id}")
async def remove_account(account_id: int):
    """Delete an account."""
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "Tài khoản không tìm thấy")
    db.delete_account(account_id)
    return {"success": True, "message": f"Đã xóa {account['email']}"}


@app.post("/api/accounts/{account_id}/refresh")
async def refresh_account(account_id: int):
    """Re-login and refresh cookies for an account."""
    result = await acct_mgr.refresh_account_cookies(account_id)
    return result


@app.post("/api/accounts/{account_id}/toggle")
async def toggle_account(account_id: int):
    """Toggle account active/disabled."""
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "Tài khoản không tìm thấy")

    if account["status"] == "active":
        db.disable_account(account_id)
        return {"success": True, "new_status": "disabled"}
    else:
        db.enable_account(account_id)
        return {"success": True, "new_status": "active"}


# ═══════════════════════════════════════════
# Scheduler API
# ═══════════════════════════════════════════

@app.get("/api/schedules")
async def list_schedules():
    return scheduler.get_all_schedules()


@app.get("/api/schedules/{schedule_id}")
async def get_schedule_detail(schedule_id: int):
    s = scheduler.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, "Lịch tải không tìm thấy")
    return s


@app.post("/api/schedules")
async def create_schedule_endpoint(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    name = data.get("name", "").strip()
    urls = data.get("urls", [])
    if not name or not urls:
        raise HTTPException(400, "Thiếu tên hoặc danh sách link")

    result = scheduler.create_schedule(
        name=name,
        urls=urls,
        schedule_type=data.get("schedule_type", "now"),
        scheduled_at=data.get("scheduled_at"),
        repeat_hours=int(data.get("repeat_hours", 0)),
        upload_gdrive=data.get("upload_gdrive", False),
        gdrive_folder=data.get("gdrive_folder", "Scribd Downloads"),
        max_concurrent=int(data.get("max_concurrent", MAX_CONCURRENT)),
    )

    # If type is 'now', start immediately
    if data.get("schedule_type") == "now":
        background_tasks.add_task(scheduler.run_schedule, result["id"])

    return result


@app.post("/api/schedules/{schedule_id}/run")
async def run_schedule_endpoint(schedule_id: int, background_tasks: BackgroundTasks):
    s = scheduler.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, "Lịch tải không tìm thấy")
    background_tasks.add_task(scheduler.run_schedule, schedule_id)
    return {"success": True, "message": "Đã bắt đầu chạy"}


@app.post("/api/schedules/{schedule_id}/pause")
async def pause_schedule_endpoint(schedule_id: int):
    scheduler.pause_schedule(schedule_id)
    return {"success": True}


@app.post("/api/schedules/{schedule_id}/resume")
async def resume_schedule_endpoint(schedule_id: int, background_tasks: BackgroundTasks):
    scheduler.resume_schedule(schedule_id)
    background_tasks.add_task(scheduler.run_schedule, schedule_id)
    return {"success": True}


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule_endpoint(schedule_id: int):
    scheduler.delete_schedule(schedule_id)
    return {"success": True}


@app.post("/api/schedules/{schedule_id}/urls")
async def add_urls_endpoint(schedule_id: int, request: Request):
    data = await request.json()
    urls = data.get("urls", [])
    if not urls:
        raise HTTPException(400, "Thiếu danh sách link")
    count = scheduler.add_urls_to_schedule(schedule_id, urls)
    return {"success": True, "added": count}


# ═══════════════════════════════════════════
# Google Drive API
# ═══════════════════════════════════════════

@app.get("/api/gdrive/status")
async def gdrive_status():
    return gdrive.get_status()


@app.get("/api/gdrive/auth-url")
async def gdrive_auth_url():
    url = gdrive.get_auth_url()
    if not url:
        raise HTTPException(400, "Credentials chưa được cấu hình")
    return {"url": url}


@app.post("/api/gdrive/credentials")
async def save_gdrive_credentials(request: Request):
    data = await request.json()
    creds_text = data.get("credentials", "")
    try:
        creds_json = json.loads(creds_text)
        with open(gdrive.CREDENTIALS_PATH, "w") as f:
            json.dump(creds_json, f)
        return {"success": True, "message": "Đã lưu credentials. Bước tiếp: xác thực Google."}
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON không hợp lệ")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/gdrive/authorize")
async def authorize_gdrive(request: Request):
    data = await request.json()
    code = data.get("code", "").strip()
    if not code:
        raise HTTPException(400, "Thiếu mã xác thực")
    result = gdrive.authorize_with_code(code)
    return result


@app.post("/api/gdrive/disconnect")
async def disconnect_gdrive():
    gdrive.disconnect()
    return {"success": True, "message": "Đã ngắt kết nối Google Drive"}


@app.get("/api/gdrive/files")
async def list_gdrive_files():
    try:
        files = gdrive.list_uploaded_files()
        return files
    except Exception as e:
        return []


# ═══════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Start the scheduler on app startup."""
    scheduler.start_scheduler()
    logger.info("📅 Scheduler started with web server")


# ═══════════════════════════════════════════
# Background Download Worker
# ═══════════════════════════════════════════

async def _do_download(doc_id: str, url: str, record_id: int,
                       cookies: list = None, account_id: int = 0):
    """Background download task with account rotation."""
    global _active_count
    async with _active_lock:
        _active_count += 1

    start_time = time.time()
    try:
        # Build download kwargs
        dl_kwargs = {"url": url, "output_dir": DOWNLOAD_DIR}
        if cookies:
            dl_kwargs["cookies_list"] = cookies
        elif COOKIES_PATH and os.path.exists(COOKIES_PATH):
            dl_kwargs["cookies_json"] = COOKIES_PATH

        result = await download_scribd_document(**dl_kwargs)
        duration = time.time() - start_time

        if result["success"]:
            file_size = os.path.getsize(result["pdf_path"])
            db.mark_download_success(
                record_id,
                title=result["title"],
                pages=result["pages"],
                file_size=file_size,
                file_path=result["pdf_path"],
                duration=duration,
            )
            logger.info(f"✅ Downloaded {doc_id}: {result['title']} "
                        f"({result['pages']}p, {duration:.1f}s, acct#{account_id})")
        else:
            # AI diagnosis on failure
            diagnosis = ai_helper.diagnose_download_error(result["error"], url, doc_id)
            error_detail = f"{result['error']} | AI: {diagnosis['diagnosis']}"
            db.mark_download_failed(record_id, error_detail, duration)
            logger.error(f"❌ Failed {doc_id}: {result['error']} | AI: {diagnosis['error_type']}")
    except Exception as e:
        duration = time.time() - start_time
        db.mark_download_failed(record_id, str(e), duration)
        logger.error(f"❌ Error {doc_id}: {e}")
    finally:
        async with _active_lock:
            _active_count -= 1
        await _process_queue()


async def _process_queue():
    """Process next item in queue if capacity available."""
    global _active_count
    async with _active_lock:
        if _active_count >= MAX_CONCURRENT:
            return

    item = db.get_next_in_queue()
    if item:
        cookies, account_id = acct_mgr.get_cookies_for_download()
        record_id = db.add_download(
            item["doc_id"], item["url"],
            source=item["source"], user_id=item["user_id"],
            account_id=account_id
        )
        db.complete_queue_item(item["id"], "completed")
        await _do_download(item["doc_id"], item["url"], record_id, cookies, account_id)


# ═══════════════════════════════════════════
# HTML Frontend
# ═══════════════════════════════════════════

def get_html() -> str:
    return """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scribd Downloader</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg: #0f1117; --surface: #1a1d27; --surface2: #252836;
            --border: #2d3041; --text: #e4e6eb; --text2: #8b8fa3;
            --accent: #6c5ce7; --accent2: #a29bfe; --success: #00b894;
            --danger: #ff6b6b; --warning: #feca57;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg); color: var(--text);
            min-height: 100vh;
        }
        .container { max-width: 960px; margin: 0 auto; padding: 20px; }

        /* Header */
        .header { text-align: center; padding: 40px 0 30px; }
        .header h1 {
            font-size: 2rem; font-weight: 700;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .header p { color: var(--text2); margin-top: 8px; font-size: 0.95rem; }

        /* Download Box */
        .download-box {
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 16px; padding: 28px; margin-bottom: 24px;
        }
        .input-row { display: flex; gap: 12px; }
        .input-row input {
            flex: 1; padding: 14px 18px; border-radius: 10px;
            border: 1px solid var(--border); background: var(--surface2);
            color: var(--text); font-size: 1rem; outline: none;
            transition: border-color 0.2s;
        }
        .input-row input:focus { border-color: var(--accent); }
        .input-row input::placeholder { color: var(--text2); }
        .btn-primary {
            padding: 14px 28px; border-radius: 10px; border: none;
            background: linear-gradient(135deg, var(--accent), #5a4bd1);
            color: white; font-size: 1rem; font-weight: 600;
            cursor: pointer; transition: transform 0.1s, opacity 0.2s;
            white-space: nowrap;
        }
        .btn-primary:hover { transform: translateY(-1px); opacity: 0.9; }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-sm {
            padding: 6px 14px; border-radius: 6px; border: none;
            font-size: 0.8rem; font-weight: 500; cursor: pointer;
            transition: opacity 0.2s;
        }
        .btn-sm:hover { opacity: 0.8; }
        .btn-success { background: var(--success); color: #fff; }
        .btn-danger { background: var(--danger); color: #fff; }
        .btn-warning { background: var(--warning); color: #222; }
        .btn-ghost {
            background: var(--surface2); color: var(--text2); border: 1px solid var(--border);
        }

        /* Status */
        .status-area {
            margin-top: 16px; padding: 16px; border-radius: 10px;
            display: none; animation: fadeIn 0.3s;
        }
        .status-area.show { display: block; }
        .status-downloading { background: rgba(108,92,231,0.1); border: 1px solid rgba(108,92,231,0.3); }
        .status-success { background: rgba(0,184,148,0.1); border: 1px solid rgba(0,184,148,0.3); }
        .status-error { background: rgba(255,107,107,0.1); border: 1px solid rgba(255,107,107,0.3); }
        .status-cached { background: rgba(254,202,87,0.1); border: 1px solid rgba(254,202,87,0.3); }
        .spinner {
            display: inline-block; width: 18px; height: 18px;
            border: 2px solid var(--accent2); border-top-color: transparent;
            border-radius: 50%; animation: spin 0.8s linear infinite;
            vertical-align: middle; margin-right: 8px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: none; } }

        /* Tabs */
        .tabs {
            display: flex; gap: 4px; margin-bottom: 20px;
            background: var(--surface); border-radius: 10px; padding: 4px;
        }
        .tab {
            flex: 1; padding: 10px; text-align: center; border-radius: 8px;
            cursor: pointer; color: var(--text2); font-size: 0.9rem;
            font-weight: 500; transition: all 0.2s; border: none; background: none;
        }
        .tab.active { background: var(--accent); color: white; }
        .tab:hover:not(.active) { background: var(--surface2); }

        /* Table */
        .panel {
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 16px; overflow: hidden;
        }
        table { width: 100%; border-collapse: collapse; }
        th {
            padding: 12px 16px; text-align: left; font-size: 0.8rem;
            text-transform: uppercase; letter-spacing: 0.05em;
            color: var(--text2); background: var(--surface2); font-weight: 600;
        }
        td {
            padding: 12px 16px; border-top: 1px solid var(--border);
            font-size: 0.9rem;
        }
        tr:hover td { background: rgba(108,92,231,0.03); }
        .badge {
            display: inline-block; padding: 3px 10px; border-radius: 20px;
            font-size: 0.75rem; font-weight: 600;
        }
        .badge-completed { background: rgba(0,184,148,0.15); color: var(--success); }
        .badge-failed { background: rgba(255,107,107,0.15); color: var(--danger); }
        .badge-downloading { background: rgba(108,92,231,0.15); color: var(--accent2); }
        .badge-queued, .badge-waiting { background: rgba(254,202,87,0.15); color: var(--warning); }
        .badge-active { background: rgba(0,184,148,0.15); color: var(--success); }
        .badge-disabled { background: rgba(139,143,163,0.15); color: var(--text2); }
        .badge-error { background: rgba(255,107,107,0.15); color: var(--danger); }
        .badge-web { background: rgba(108,92,231,0.15); color: var(--accent2); }
        .badge-telegram { background: rgba(0,184,148,0.15); color: var(--success); }

        .title-cell {
            max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .dl-link {
            display: inline-block; margin-top: 12px; padding: 10px 24px;
            background: var(--accent); color: #fff; border-radius: 8px;
            font-weight: 600; text-decoration: none; transition: all 0.2s;
        }
        .dl-link:hover { background: #5a4bd1; text-decoration: none; }

        /* File info card */
        .file-info-card {
            display: flex; align-items: center; gap: 14px;
            text-align: left; margin-bottom: 8px;
        }
        .file-icon { font-size: 2.2rem; }
        .file-details { flex: 1; min-width: 0; }
        .file-title {
            font-weight: 700; font-size: 1.05rem; color: var(--text);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .file-meta { font-size: 0.85rem; color: var(--text2); margin-top: 2px; }

        /* Progress bar */
        .progress-section {
            margin-top: 10px; font-size: 0.9rem; color: var(--text2);
        }
        .progress-bar {
            width: 100%; height: 6px; background: var(--surface); border-radius: 3px;
            margin: 8px 0; overflow: hidden;
        }
        .progress-fill {
            height: 100%; width: 0%; background: var(--accent);
            border-radius: 3px; transition: width 2s ease;
        }

        /* Status info type */
        .status-info {
            background: rgba(108,92,231,0.1); border: 1px solid rgba(108,92,231,0.3);
            border-radius: 10px; padding: 18px;
        }

        /* AI Diagnosis */
        .ai-diagnosis {
            margin-top: 12px; text-align: left;
            background: var(--surface2); border-radius: 10px;
            padding: 16px; border-left: 3px solid var(--accent);
        }
        .ai-header {
            font-size: 0.95rem; margin-bottom: 10px;
            display: flex; align-items: center; gap: 8px;
        }
        .ai-body p { font-size: 0.9rem; margin-bottom: 8px; line-height: 1.5; }
        .ai-suggestion {
            font-size: 0.85rem; padding: 6px 10px;
            background: rgba(108,92,231,0.08); border-radius: 6px;
            margin-bottom: 4px; color: var(--text2);
        }
        .ai-retry-btn {
            margin-top: 12px; padding: 10px 22px !important;
            font-size: 0.9rem !important;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(108,92,231,0.4); }
            50% { box-shadow: 0 0 0 8px rgba(108,92,231,0); }
        }

        /* Stats */
        .stats-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px; margin-bottom: 20px;
        }
        .stat-card {
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; padding: 20px; text-align: center;
        }
        .stat-card .number {
            font-size: 1.8rem; font-weight: 700;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .stat-card .label { color: var(--text2); font-size: 0.85rem; margin-top: 4px; }

        /* Search */
        .search-bar {
            padding: 10px 16px; border-radius: 10px; border: 1px solid var(--border);
            background: var(--surface2); color: var(--text); width: 100%;
            font-size: 0.9rem; outline: none; margin-bottom: 16px;
        }
        .search-bar:focus { border-color: var(--accent); }

        .empty-state {
            padding: 40px; text-align: center; color: var(--text2);
        }
        .empty-state .icon { font-size: 2.5rem; margin-bottom: 8px; }

        /* Account Form */
        .add-account-form {
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 16px; padding: 20px; margin-bottom: 16px;
        }
        .form-row { display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
        .form-row input, .form-row textarea {
            flex: 1; min-width: 160px; padding: 10px 14px; border-radius: 8px;
            border: 1px solid var(--border); background: var(--surface2);
            color: var(--text); font-size: 0.9rem; outline: none;
        }
        .form-row input:focus, .form-row textarea:focus { border-color: var(--accent); }
        .form-row textarea { min-height: 60px; resize: vertical; font-family: monospace; font-size: 0.8rem; }

        .account-actions { display: flex; gap: 6px; flex-wrap: wrap; }
        .email-cell { font-family: monospace; font-size: 0.85rem; }
        .cookie-indicator {
            display: inline-block; width: 8px; height: 8px; border-radius: 50%;
            margin-right: 6px;
        }
        .cookie-yes { background: var(--success); }
        .cookie-no { background: var(--danger); }

        /* Responsive */
        @media (max-width: 640px) {
            .input-row { flex-direction: column; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .header h1 { font-size: 1.5rem; }
            th, td { padding: 10px 12px; font-size: 0.8rem; }
            .form-row { flex-direction: column; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 Scribd Downloader</h1>
            <p>Dán link Scribd — nhận file PDF ngay lập tức</p>
        </div>

        <div class="download-box">
            <div class="input-row">
                <input type="text" id="urlInput" placeholder="https://www.scribd.com/document/..." autofocus>
                <button class="btn-primary" id="downloadBtn" onclick="startDownload()">
                    ⬇️ Tải PDF
                </button>
            </div>
            <div class="status-area" id="statusArea"></div>
        </div>

        <div class="tabs">
            <button class="tab active" data-tab="history" onclick="switchTab('history')">📋 Lịch sử</button>
            <button class="tab" data-tab="queue" onclick="switchTab('queue')">🔄 Hàng đợi</button>
            <button class="tab" data-tab="accounts" onclick="switchTab('accounts')">👤 Tài khoản</button>
            <button class="tab" data-tab="stats" onclick="switchTab('stats')">📊 Thống kê</button>
            <button class="tab" data-tab="scheduler" onclick="switchTab('scheduler')">📅 Lịch tải</button>
            <button class="tab" data-tab="gdrive" onclick="switchTab('gdrive')">☁️ GDrive</button>
        </div>

        <!-- History Tab -->
        <div id="tab-history">
            <input type="text" class="search-bar" id="searchInput"
                   placeholder="🔍 Tìm theo tên tài liệu..." oninput="debounceSearch()">
            <div class="panel" id="historyPanel">
                <div class="empty-state"><div class="icon">📭</div><p>Chưa có lịch sử tải</p></div>
            </div>
        </div>

        <!-- Queue Tab -->
        <div id="tab-queue" style="display:none">
            <div class="panel" id="queuePanel">
                <div class="empty-state"><div class="icon">✅</div><p>Hàng đợi trống</p></div>
            </div>
        </div>

        <!-- Accounts Tab -->
        <div id="tab-accounts" style="display:none">
            <div class="add-account-form">
                <h3 style="margin-bottom:12px;font-size:1rem;">➕ Thêm tài khoản Scribd</h3>
                <div class="form-row">
                    <input type="email" id="acctEmail" placeholder="Email Scribd">
                    <input type="password" id="acctPassword" placeholder="Mật khẩu (tuỳ chọn)">
                    <input type="text" id="acctLabel" placeholder="Nhãn (tuỳ chọn)">
                </div>
                <div class="form-row">
                    <textarea id="acctCookies" placeholder='Paste cookies JSON (tuỳ chọn) — [{&quot;name&quot;:&quot;...&quot;, &quot;value&quot;:&quot;...&quot;, &quot;domain&quot;:&quot;.scribd.com&quot;}]'></textarea>
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                    <button class="btn-primary" style="padding:10px 20px;font-size:0.9rem;" onclick="addAccount()">
                        ➕ Thêm tài khoản
                    </button>
                    <span id="acctAddStatus" style="color:var(--text2);font-size:0.85rem;"></span>
                </div>
            </div>
            <div class="panel" id="accountsPanel">
                <div class="empty-state"><div class="icon">👤</div><p>Chưa có tài khoản nào</p></div>
            </div>
        </div>

        <!-- Stats Tab -->
        <div id="tab-stats" style="display:none">
            <div class="stats-grid" id="statsGrid"></div>
        </div>

        <!-- Scheduler Tab -->
        <div id="tab-scheduler" style="display:none">
            <div class="panel" style="margin-bottom:16px">
                <h3 style="margin:0 0 12px">📅 Tạo lịch tải mới</h3>
                <input type="text" class="input-field" id="schedName" placeholder="Tên lịch tải (VD: Tài liệu học tập)..." style="margin-bottom:8px">
                <textarea class="input-field" id="schedUrls" rows="5" placeholder="Dán danh sách link Scribd (mỗi dòng 1 link)..." style="margin-bottom:8px;resize:vertical;font-family:inherit"></textarea>
                <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
                    <select id="schedType" class="input-field" style="width:auto;min-width:140px" onchange="toggleSchedTime()">
                        <option value="now">⚡ Tải ngay</option>
                        <option value="once">⏰ Hẹn giờ</option>
                        <option value="recurring">🔄 Lặp lại</option>
                    </select>
                    <input type="datetime-local" id="schedTime" class="input-field" style="width:auto;display:none">
                    <input type="number" id="schedRepeat" class="input-field" placeholder="Mỗi N giờ" min="1" value="24" style="width:100px;display:none">
                    <label style="display:flex;align-items:center;gap:6px;color:var(--text2);font-size:0.9rem;cursor:pointer">
                        <input type="checkbox" id="schedGdrive"> ☁️ Lưu Google Drive
                    </label>
                </div>
                <button class="btn-primary" onclick="createSchedule()">📅 Tạo lịch tải</button>
            </div>
            <div id="schedulesList">
                <div class="empty-state"><div class="icon">📅</div><p>Chưa có lịch tải nào</p></div>
            </div>
        </div>

        <!-- Google Drive Tab -->
        <div id="tab-gdrive" style="display:none">
            <div class="panel" id="gdriveStatus">
                <div class="empty-state"><div class="icon">☁️</div><p>Đang tải...</p></div>
            </div>
            <div class="panel" id="gdriveFiles" style="margin-top:16px;display:none">
                <h3 style="margin:0 0 12px">📁 Files trên Google Drive</h3>
                <div id="gdriveFilesList"></div>
            </div>
        </div>
    </div>

    <script>
        let currentTab = 'history';
        let pollInterval = null;
        let searchTimeout = null;

        // ═══ Download Flow: AI Parse → Get Info → Download → Show File ═══
        let _currentDocId = null;
        let _lastError = null;
        let _lastUrl = null;

        async function startDownload() {
            let url = document.getElementById('urlInput').value.trim();
            if (!url) return;

            const btn = document.getElementById('downloadBtn');
            btn.disabled = true;
            btn.textContent = '🤖 AI đang phân tích...';
            showStatus('downloading', '<span class="spinner"></span> 🤖 AI đang phân tích link...');

            // ── Step 0: AI Smart Parse ──
            try {
                const parseRes = await fetch('/api/ai/parse', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: url})
                });
                const parsed = await parseRes.json();

                if (parsed.type === 'search_query' || (!parsed.fixed_url && parsed.confidence < 0.5)) {
                    let html = '<div class="ai-diagnosis"><div class="ai-header">🤖 <b>AI Analysis</b></div>';
                    html += '<div class="ai-body">';
                    (parsed.suggestions || []).forEach(s => html += `<div class="ai-suggestion">${s}</div>`);
                    html += '</div></div>';
                    showStatus('error', html);
                    resetBtn(); return;
                }

                if (parsed.fixed_url && parsed.fixed_url !== url) {
                    url = parsed.fixed_url;
                    document.getElementById('urlInput').value = url;
                    const fixes = (parsed.issues || []).join(', ');
                    if (fixes) {
                        showStatus('downloading', `<span class="spinner"></span> 🤖 AI đã sửa link: <em>${fixes}</em><br>Bước 1/3 — Đang lấy thông tin...`);
                    }
                } else {
                    showStatus('downloading', '<span class="spinner"></span> Bước 1/3 — Đang lấy thông tin tài liệu...');
                }
            } catch(e) {
                // AI parse failed — continue with original URL
            }

            _lastUrl = url;
            btn.textContent = '⏳ Đang lấy thông tin...';

            try {
                // === Step 1: Get document info ===
                const infoRes = await fetch('/api/info', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url})
                });
                const info = await infoRes.json();

                if (!infoRes.ok || !info.success) {
                    showStatus('error', `❌ ${info.detail || info.error || 'Không lấy được thông tin'}`);
                    resetBtn(); return;
                }

                _currentDocId = info.doc_id;

                // If cached — show file info + download link immediately
                if (info.cached) {
                    const sizeMB = info.file_size ? (info.file_size/1024/1024).toFixed(1) + 'MB' : '';
                    showStatus('success', `
                        <div class="file-info-card">
                            <div class="file-icon">📄</div>
                            <div class="file-details">
                                <div class="file-title">${info.title}</div>
                                <div class="file-meta">${info.pages} trang${sizeMB ? ' · ' + sizeMB : ''} · Đã có sẵn!</div>
                            </div>
                        </div>
                        <a class="dl-link" href="${info.download_url}" target="_blank">⬇️ Tải PDF ngay</a>
                    `);
                    resetBtn(); loadHistory(); return;
                }

                // === Step 2: Show document info, start download ===
                showStatus('info', `
                    <div class="file-info-card">
                        <div class="file-icon">📄</div>
                        <div class="file-details">
                            <div class="file-title">${info.title}</div>
                            <div class="file-meta">${info.pages} trang · doc_id: ${info.doc_id}</div>
                        </div>
                    </div>
                    <div class="progress-section">
                        <span class="spinner"></span> Bước 2/3 — Đang tải tài liệu... (30s-2 phút)
                        <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
                    </div>
                `);
                btn.textContent = '⏳ Đang tải...';

                // Start actual download
                const dlRes = await fetch('/api/download', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url})
                });
                const dlData = await dlRes.json();

                if (dlData.status === 'cached') {
                    const sizeMB = dlData.file_size ? (dlData.file_size/1024/1024).toFixed(1) + 'MB' : '';
                    showStatus('success', `
                        <div class="file-info-card">
                            <div class="file-icon">✅</div>
                            <div class="file-details">
                                <div class="file-title">${dlData.title}</div>
                                <div class="file-meta">${dlData.pages} trang${sizeMB ? ' · ' + sizeMB : ''}</div>
                            </div>
                        </div>
                        <a class="dl-link" href="${dlData.download_url}" target="_blank">⬇️ Tải PDF ngay</a>
                    `);
                    resetBtn(); loadHistory();
                } else if (dlData.status === 'queued') {
                    startPolling(dlData.doc_id, info.title, info.pages);
                } else if (dlData.status === 'started' || dlData.status === 'downloading') {
                    startPolling(dlData.doc_id, info.title, info.pages);
                } else {
                    showStatus('error', `❌ ${dlData.detail || dlData.message || 'Lỗi'}`);
                    resetBtn();
                }
            } catch (e) {
                showStatus('error', `❌ Lỗi kết nối: ${e.message}`);
                resetBtn();
            }
        }

        function startPolling(docId, title, pages) {
            if (pollInterval) clearInterval(pollInterval);
            let elapsed = 0;
            // Animate progress bar
            const animProgress = setInterval(() => {
                elapsed += 3;
                const pct = Math.min(90, (elapsed / 120) * 100);
                const fill = document.getElementById('progressFill');
                if (fill) fill.style.width = pct + '%';
            }, 3000);

            pollInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/status/${docId}`);
                    const data = await res.json();
                    if (data.status === 'completed') {
                        clearInterval(pollInterval); pollInterval = null;
                        clearInterval(animProgress);
                        const sizeMB = data.file_size ? (data.file_size/1024/1024).toFixed(1) + 'MB' : '';
                        // === Step 3: Show completed file ===
                        showStatus('success', `
                            <div class="file-info-card">
                                <div class="file-icon">✅</div>
                                <div class="file-details">
                                    <div class="file-title">${data.title || title}</div>
                                    <div class="file-meta">${data.pages || pages} trang · ${sizeMB} · ${data.duration ? data.duration.toFixed(0) + 's' : ''}</div>
                                </div>
                            </div>
                            <div class="progress-section">
                                <div class="progress-bar"><div class="progress-fill" style="width:100%;background:var(--success)"></div></div>
                                Bước 3/3 — Hoàn tất! ✅
                            </div>
                            <a class="dl-link" href="${data.download_url}" target="_blank">⬇️ Tải PDF ngay</a>
                        `);
                        resetBtn(); loadHistory();
                    } else if (data.status === 'failed') {
                        clearInterval(pollInterval); pollInterval = null;
                        clearInterval(animProgress);
                        _lastError = data.error || 'Tải thất bại';
                        // AI Diagnosis
                        showAIDiagnosis(docId, _lastError, _lastUrl, title);
                        resetBtn(); loadHistory();
                    }
                } catch(e) {}
            }, 3000);
        }

        async function showAIDiagnosis(docId, errorMsg, url, title) {
            try {
                const res = await fetch('/api/ai/diagnose', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({doc_id: docId, error: errorMsg, url: url || ''})
                });
                const data = await res.json();
                const d = data.diagnosis;
                let html = `
                    <div class="file-info-card">
                        <div class="file-icon">❌</div>
                        <div class="file-details">
                            <div class="file-title">${title || 'Tải thất bại'}</div>
                            <div class="file-meta">${d.error_type}</div>
                        </div>
                    </div>
                    <div class="ai-diagnosis">
                        <div class="ai-header">🤖 <b>AI Chẩn đoán</b> <span class="badge badge-${d.severity === 'low' ? 'completed' : d.severity === 'high' ? 'failed' : 'downloading'}">${d.severity}</span></div>
                        <div class="ai-body">
                            <p>${d.diagnosis}</p>
                `;
                d.suggestions.forEach(s => {
                    html += `<div class="ai-suggestion">${s}</div>`;
                });
                if (d.can_retry) {
                    html += `<button class="btn-primary ai-retry-btn" onclick="aiRetry('${docId}')">🤖 AI Thử lại thông minh</button>`;
                }
                html += '</div></div>';
                showStatus('error', html);
            } catch(e) {
                showStatus('error', `
                    <div class="file-info-card">
                        <div class="file-icon">❌</div>
                        <div class="file-details">
                            <div class="file-title">${title || 'Tải thất bại'}</div>
                            <div class="file-meta">${errorMsg}</div>
                        </div>
                    </div>
                    <button class="btn-primary ai-retry-btn" onclick="startDownload()">🔄 Thử lại</button>
                `);
            }
        }

        async function aiRetry(docId) {
            showStatus('downloading', '<span class="spinner"></span> 🤖 AI đang thử lại với phương pháp khác...');
            const btn = document.getElementById('downloadBtn');
            btn.disabled = true;
            btn.textContent = '🤖 AI đang retry...';
            try {
                const res = await fetch('/api/ai/retry', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({doc_id: docId, error: _lastError || '', url: _lastUrl || ''})
                });
                const data = await res.json();
                if (data.success && data.status === 'retrying') {
                    const actionText = data.action_taken ? data.action_taken.replace(/_/g, ' ') : 'retry';
                    showStatus('downloading', `
                        <div class="ai-diagnosis">
                            <div class="ai-header">🤖 <b>AI Retry</b></div>
                            <div class="ai-body">
                                <p>Hành động: <b>${actionText}</b></p>
                                <p>${data.diagnosis.diagnosis}</p>
                            </div>
                        </div>
                        <div class="progress-section">
                            <span class="spinner"></span> Đang tải lại...
                            <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
                        </div>
                    `);
                    startPolling(data.doc_id, '', 0);
                } else {
                    showStatus('error', '❌ AI retry thất bại: ' + (data.error || 'Unknown'));
                    resetBtn();
                }
            } catch(e) {
                showStatus('error', '❌ Lỗi kết nối AI retry');
                resetBtn();
            }
        }

        function resetBtn() {
            const btn = document.getElementById('downloadBtn');
            btn.disabled = false;
            btn.textContent = '⬇️ Tải PDF';
        }

        function showStatus(type, html) {
            const area = document.getElementById('statusArea');
            area.className = `status-area show status-${type}`;
            area.innerHTML = html;
        }

        // ═══ History ═══
        async function loadHistory() {
            try {
                const res = await fetch('/api/history?limit=30');
                const data = await res.json();
                renderHistory(data.items);
            } catch(e) {}
        }

        function renderHistory(items) {
            const panel = document.getElementById('historyPanel');
            if (!items.length) {
                panel.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>Chưa có lịch sử tải</p></div>';
                return;
            }
            let html = `<table><thead><tr>
                <th>Tài liệu</th><th>Trang</th><th>Nguồn</th>
                <th>Trạng thái</th><th>Thời gian</th><th></th>
            </tr></thead><tbody>`;
            for (const item of items) {
                const title = item.title || item.url.split('/').pop() || item.doc_id;
                const time = new Date(item.created_at + 'Z').toLocaleString('vi-VN', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
                const badge = `<span class="badge badge-${item.status}">${item.status}</span>`;
                const srcBadge = `<span class="badge badge-${item.source}">${item.source}</span>`;
                const dl = item.status === 'completed' ? `<a class="dl-link" href="/api/file/${item.doc_id}" target="_blank">⬇️</a>` : '';
                html += `<tr><td class="title-cell" title="${title}">${title}</td><td>${item.pages||'-'}</td><td>${srcBadge}</td><td>${badge}</td><td style="color:var(--text2)">${time}</td><td>${dl}</td></tr>`;
            }
            html += '</tbody></table>';
            panel.innerHTML = html;
        }

        function debounceSearch() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(doSearch, 400);
        }

        async function doSearch() {
            const q = document.getElementById('searchInput').value.trim();
            if (!q) { loadHistory(); return; }
            try {
                const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
                const data = await res.json();
                renderHistory(data.items);
            } catch(e) {}
        }

        // ═══ Queue ═══
        async function loadQueue() {
            try {
                const res = await fetch('/api/queue');
                const data = await res.json();
                const panel = document.getElementById('queuePanel');
                const all = [...data.processing, ...data.waiting];
                if (!all.length) {
                    panel.innerHTML = '<div class="empty-state"><div class="icon">✅</div><p>Hàng đợi trống</p></div>';
                    return;
                }
                let html = `<table><thead><tr><th>URL</th><th>Nguồn</th><th>Trạng thái</th><th>Thời gian</th></tr></thead><tbody>`;
                for (const item of all) {
                    const time = new Date(item.created_at + 'Z').toLocaleString('vi-VN', {hour:'2-digit',minute:'2-digit'});
                    html += `<tr><td class="title-cell">${item.url}</td><td>${item.source}</td><td><span class="badge badge-${item.status}">${item.status}</span></td><td>${time}</td></tr>`;
                }
                html += '</tbody></table>';
                panel.innerHTML = html;
            } catch(e) {}
        }

        // ═══ Accounts ═══
        async function loadAccounts() {
            try {
                const res = await fetch('/api/accounts');
                const data = await res.json();
                renderAccounts(data);
            } catch(e) {}
        }

        function renderAccounts(data) {
            const panel = document.getElementById('accountsPanel');
            if (!data.accounts || !data.accounts.length) {
                panel.innerHTML = '<div class="empty-state"><div class="icon">👤</div><p>Chưa có tài khoản nào. Thêm tài khoản Scribd ở trên để bắt đầu.</p></div>';
                return;
            }
            let html = `<table><thead><tr>
                <th>Email</th><th>Nhãn</th><th>Cookies</th>
                <th>Đã tải</th><th>Trạng thái</th><th>Hành động</th>
            </tr></thead><tbody>`;
            for (const a of data.accounts) {
                const cookieIcon = a.has_cookies
                    ? '<span class="cookie-indicator cookie-yes"></span>Có'
                    : '<span class="cookie-indicator cookie-no"></span>Chưa';
                const badge = `<span class="badge badge-${a.status}">${a.status}</span>`;
                const toggleText = a.status === 'active' ? 'Tắt' : 'Bật';
                const toggleClass = a.status === 'active' ? 'btn-warning' : 'btn-success';
                html += `<tr>
                    <td class="email-cell">${a.email}</td>
                    <td>${a.label || '-'}</td>
                    <td>${cookieIcon}</td>
                    <td>${a.download_count}</td>
                    <td>${badge}${a.error ? '<br><small style="color:var(--danger)">'+a.error.substring(0,40)+'</small>' : ''}</td>
                    <td>
                        <div class="account-actions">
                            <button class="btn-sm ${toggleClass}" onclick="toggleAccount(${a.id})">${toggleText}</button>
                            <button class="btn-sm btn-ghost" onclick="refreshAccount(${a.id})">🔄</button>
                            <button class="btn-sm btn-danger" onclick="deleteAccount(${a.id},'${a.email}')">🗑️</button>
                        </div>
                    </td>
                </tr>`;
            }
            html += '</tbody></table>';
            // Summary bar
            html = `<div style="padding:12px 16px;background:var(--surface2);font-size:0.85rem;color:var(--text2);">
                Tổng: ${data.total} · <span style="color:var(--success)">Active: ${data.active}</span> · Error: ${data.error} · Disabled: ${data.disabled} · Tổng tải: ${data.total_downloads}
            </div>` + html;
            panel.innerHTML = html;
        }

        async function addAccount() {
            const email = document.getElementById('acctEmail').value.trim();
            const password = document.getElementById('acctPassword').value;
            const label = document.getElementById('acctLabel').value.trim();
            const cookiesRaw = document.getElementById('acctCookies').value.trim();
            const statusEl = document.getElementById('acctAddStatus');

            if (!email) { statusEl.textContent = '⚠️ Cần nhập email'; return; }

            statusEl.textContent = '⏳ Đang thêm...';
            try {
                const body = {email, password, label};
                if (cookiesRaw) body.cookies_json = cookiesRaw;
                const res = await fetch('/api/accounts', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                statusEl.textContent = data.message || (data.success ? '✅ Thành công' : '❌ Thất bại');
                if (data.success || data.account_id) {
                    document.getElementById('acctEmail').value = '';
                    document.getElementById('acctPassword').value = '';
                    document.getElementById('acctLabel').value = '';
                    document.getElementById('acctCookies').value = '';
                    loadAccounts();
                }
                setTimeout(() => { statusEl.textContent = ''; }, 5000);
            } catch(e) {
                statusEl.textContent = `❌ ${e.message}`;
            }
        }

        async function toggleAccount(id) {
            try {
                await fetch(`/api/accounts/${id}/toggle`, {method:'POST'});
                loadAccounts();
            } catch(e) {}
        }

        async function refreshAccount(id) {
            try {
                const res = await fetch(`/api/accounts/${id}/refresh`, {method:'POST'});
                const data = await res.json();
                alert(data.message || 'Done');
                loadAccounts();
            } catch(e) { alert('Lỗi: ' + e.message); }
        }

        async function deleteAccount(id, email) {
            if (!confirm(`Xóa tài khoản ${email}?`)) return;
            try {
                await fetch(`/api/accounts/${id}`, {method:'DELETE'});
                loadAccounts();
            } catch(e) {}
        }

        // ═══ Stats ═══
        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                const s = data.summary;
                const mb = ((s.total_bytes || 0) / 1024 / 1024).toFixed(1);
                // Also get account info
                const acctRes = await fetch('/api/accounts');
                const acctData = await acctRes.json();
                document.getElementById('statsGrid').innerHTML = `
                    <div class="stat-card"><div class="number">${s.total || 0}</div><div class="label">Tổng tải</div></div>
                    <div class="stat-card"><div class="number">${s.successful || 0}</div><div class="label">Thành công</div></div>
                    <div class="stat-card"><div class="number">${s.failed || 0}</div><div class="label">Thất bại</div></div>
                    <div class="stat-card"><div class="number">${s.total_pages || 0}</div><div class="label">Tổng trang</div></div>
                    <div class="stat-card"><div class="number">${mb}MB</div><div class="label">Dung lượng</div></div>
                    <div class="stat-card"><div class="number">${(s.avg_duration || 0).toFixed(0)}s</div><div class="label">TB thời gian</div></div>
                    <div class="stat-card"><div class="number">${acctData.active || 0}</div><div class="label">TK Active</div></div>
                    <div class="stat-card"><div class="number">${acctData.total || 0}</div><div class="label">Tổng TK</div></div>
                `;
            } catch(e) {}
        }

        // ═══ Tabs ═══
        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
            ['history','queue','accounts','stats','scheduler','gdrive'].forEach(t => {
                const el = document.getElementById('tab-' + t);
                if (el) el.style.display = t === tab ? '' : 'none';
            });
            if (tab === 'history') loadHistory();
            if (tab === 'queue') loadQueue();
            if (tab === 'accounts') loadAccounts();
            if (tab === 'stats') loadStats();
            if (tab === 'scheduler') loadSchedules();
            if (tab === 'gdrive') loadGDrive();
        }

        // ═══ Scheduler Functions ═══
        function toggleSchedTime() {
            const type = document.getElementById('schedType').value;
            document.getElementById('schedTime').style.display = type === 'once' ? '' : 'none';
            document.getElementById('schedRepeat').style.display = type === 'recurring' ? '' : 'none';
        }

        async function createSchedule() {
            const name = document.getElementById('schedName').value.trim();
            const urlsText = document.getElementById('schedUrls').value.trim();
            if (!name || !urlsText) {
                alert('Vui lòng nhập tên và danh sách link!'); return;
            }
            const urls = urlsText.split('\n').map(u => u.trim()).filter(u => u);
            if (urls.length === 0) { alert('Không có link hợp lệ!'); return; }

            const type = document.getElementById('schedType').value;
            const body = {
                name, urls, schedule_type: type,
                upload_gdrive: document.getElementById('schedGdrive').checked,
            };
            if (type === 'once') body.scheduled_at = document.getElementById('schedTime').value;
            if (type === 'recurring') body.repeat_hours = parseInt(document.getElementById('schedRepeat').value) || 24;

            try {
                const res = await fetch('/api/schedules', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (data.id) {
                    document.getElementById('schedName').value = '';
                    document.getElementById('schedUrls').value = '';
                    loadSchedules();
                } else {
                    alert('Lỗi: ' + (data.error || 'Unknown'));
                }
            } catch(e) { alert('Lỗi kết nối'); }
        }

        async function loadSchedules() {
            try {
                const res = await fetch('/api/schedules');
                const data = await res.json();
                const el = document.getElementById('schedulesList');
                if (!data.length) {
                    el.innerHTML = '<div class="empty-state"><div class="icon">📅</div><p>Chưa có lịch tải nào</p></div>';
                    return;
                }
                el.innerHTML = data.map(s => {
                    const statusIcons = {pending:'⏳',running:'🔄',completed:'✅',completed_with_errors:'⚠️',failed:'❌',paused:'⏸'};
                    const icon = statusIcons[s.status] || '📅';
                    const progress = s.total_items > 0 ? Math.round((s.completed_items / s.total_items) * 100) : 0;
                    return `<div class="panel" style="margin-bottom:10px;cursor:pointer" onclick="viewSchedule(${s.id})">
                        <div style="display:flex;justify-content:space-between;align-items:center">
                            <div>
                                <b>${icon} ${s.name}</b>
                                <div style="font-size:0.85rem;color:var(--text2);margin-top:4px">
                                    📄 ${s.total_items} files · ✅ ${s.completed_items} · ❌ ${s.failed_items}
                                    ${s.upload_to_gdrive ? ' · ☁️ GDrive' : ''}
                                    ${s.schedule_type === 'recurring' ? ' · 🔄 Lặp lại mỗi ' + s.repeat_interval_hours + 'h' : ''}
                                </div>
                            </div>
                            <div style="display:flex;gap:6px">
                                ${s.status === 'pending' ? `<button class="btn-primary" style="padding:6px 12px;font-size:0.8rem" onclick="event.stopPropagation();runScheduleNow(${s.id})">▶ Chạy</button>` : ''}
                                ${s.status === 'running' ? `<button class="btn-primary" style="padding:6px 12px;font-size:0.8rem;background:#e67e22" onclick="event.stopPropagation();pauseSchedule(${s.id})">⏸ Tạm dừng</button>` : ''}
                                ${s.status === 'paused' ? `<button class="btn-primary" style="padding:6px 12px;font-size:0.8rem" onclick="event.stopPropagation();resumeSchedule(${s.id})">▶ Tiếp tục</button>` : ''}
                                <button class="btn-primary" style="padding:6px 12px;font-size:0.8rem;background:#e74c3c" onclick="event.stopPropagation();deleteSchedule(${s.id})">🗑</button>
                            </div>
                        </div>
                        ${s.total_items > 0 ? `<div class="progress-bar" style="margin-top:8px"><div class="progress-fill" style="width:${progress}%"></div></div>` : ''}
                    </div>`;
                }).join('');
            } catch(e) { console.error(e); }
        }

        async function viewSchedule(id) {
            try {
                const res = await fetch('/api/schedules/' + id);
                const s = await res.json();
                if (!s || !s.items) return;

                let html = `<div class="panel" style="margin-bottom:12px">
                    <h3 style="margin:0 0 8px">📅 ${s.name}</h3>
                    <p style="color:var(--text2);font-size:0.85rem">
                        Loại: ${s.schedule_type} · Trạng thái: ${s.status} · 
                        Tạo: ${new Date(s.created_at).toLocaleString('vi-VN')}
                        ${s.upload_to_gdrive ? ' · ☁️ Upload GDrive' : ''}
                    </p>
                    <div style="margin-top:8px">
                        <textarea class="input-field" id="addUrlsArea" rows="2" placeholder="Thêm link mới (mỗi dòng 1 link)..." style="resize:vertical;font-family:inherit"></textarea>
                        <button class="btn-primary" style="margin-top:4px;padding:6px 14px;font-size:0.85rem" onclick="addUrlsToSchedule(${id})">➕ Thêm URLs</button>
                    </div>
                </div>`;

                html += s.items.map(item => {
                    const icons = {pending:'⏳',downloading:'🔄',completed:'✅',failed:'❌'};
                    const icon = icons[item.status] || '📄';
                    return `<div class="history-item">
                        <div class="history-icon">${icon}</div>
                        <div class="history-details">
                            <div class="history-title">${item.title || item.url.substring(0, 60) + '...'}</div>
                            <div class="history-meta">
                                ${item.pages ? item.pages + ' trang · ' : ''}
                                ${item.file_size ? (item.file_size/1024/1024).toFixed(1) + 'MB · ' : ''}
                                ${item.duration_seconds ? item.duration_seconds.toFixed(0) + 's' : ''}
                                ${item.gdrive_link ? ' · <a href="' + item.gdrive_link + '" target="_blank" style="color:var(--accent)">☁️ GDrive</a>' : ''}
                                ${item.error_message ? ' · <span style="color:#e74c3c">' + item.error_message.substring(0, 50) + '</span>' : ''}
                            </div>
                        </div>
                    </div>`;
                }).join('');

                document.getElementById('schedulesList').innerHTML = 
                    `<button class="btn-primary" style="margin-bottom:12px;padding:6px 14px;font-size:0.85rem" onclick="loadSchedules()">← Quay lại</button>` + html;
            } catch(e) { console.error(e); }
        }

        async function runScheduleNow(id) {
            await fetch('/api/schedules/' + id + '/run', {method: 'POST'});
            loadSchedules();
        }
        async function pauseSchedule(id) {
            await fetch('/api/schedules/' + id + '/pause', {method: 'POST'});
            loadSchedules();
        }
        async function resumeSchedule(id) {
            await fetch('/api/schedules/' + id + '/resume', {method: 'POST'});
            loadSchedules();
        }
        async function deleteSchedule(id) {
            if (!confirm('Xóa lịch tải này?')) return;
            await fetch('/api/schedules/' + id, {method: 'DELETE'});
            loadSchedules();
        }
        async function addUrlsToSchedule(id) {
            const text = document.getElementById('addUrlsArea').value.trim();
            if (!text) return;
            const urls = text.split('\n').map(u => u.trim()).filter(u => u);
            await fetch('/api/schedules/' + id + '/urls', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({urls})
            });
            viewSchedule(id);
        }

        // ═══ Google Drive Functions ═══
        async function loadGDrive() {
            try {
                const res = await fetch('/api/gdrive/status');
                const status = await res.json();
                const el = document.getElementById('gdriveStatus');

                if (!status.configured) {
                    el.innerHTML = `<div class="empty-state">
                        <div class="icon">☁️</div>
                        <h3>Kết nối Google Drive</h3>
                        <p style="max-width:500px;margin:0 auto 16px;line-height:1.6">
                            Để tự động lưu file PDF lên Google Drive, bạn cần:<br>
                            1. Vào <a href="https://console.cloud.google.com" target="_blank" style="color:var(--accent)">Google Cloud Console</a><br>
                            2. Tạo project → Enable "Google Drive API"<br>
                            3. Tạo OAuth2 credentials (Desktop app)<br>
                            4. Tải <b>credentials.json</b>
                        </p>
                        <div style="margin-top:8px">
                            <textarea class="input-field" id="gdriveCredsInput" rows="4" placeholder='Dán nội dung credentials.json vào đây...' style="resize:vertical;font-family:monospace;font-size:0.8rem"></textarea>
                            <button class="btn-primary" style="margin-top:8px" onclick="saveGDriveCreds()">💾 Lưu Credentials</button>
                        </div>
                    </div>`;
                } else if (!status.authorized) {
                    // Show auth URL
                    const authRes = await fetch('/api/gdrive/auth-url');
                    const authData = await authRes.json();
                    el.innerHTML = `<div class="empty-state">
                        <div class="icon">🔑</div>
                        <h3>Xác thực Google Drive</h3>
                        <p>Click link bên dưới để đăng nhập Google và cấp quyền:</p>
                        <a href="${authData.url}" target="_blank" class="btn-primary" style="display:inline-block;margin:12px 0;text-decoration:none">🔗 Đăng nhập Google</a>
                        <div style="margin-top:12px">
                            <input type="text" class="input-field" id="gdriveAuthCode" placeholder="Dán mã xác thực từ Google...">
                            <button class="btn-primary" style="margin-top:8px" onclick="submitGDriveAuth()">✅ Xác thực</button>
                        </div>
                    </div>`;
                } else {
                    el.innerHTML = `<div style="text-align:center;padding:20px">
                        <div style="font-size:2rem;margin-bottom:8px">✅</div>
                        <h3 style="margin:0 0 8px;color:var(--accent)">Google Drive đã kết nối</h3>
                        <p style="color:var(--text2)">File PDF sẽ tự động upload khi bật trong lịch tải</p>
                        <button class="btn-primary" style="margin-top:12px;background:#e74c3c;padding:8px 16px;font-size:0.85rem" onclick="disconnectGDrive()">🔌 Ngắt kết nối</button>
                    </div>`;

                    // Load files
                    document.getElementById('gdriveFiles').style.display = '';
                    loadGDriveFiles();
                }
            } catch(e) {
                document.getElementById('gdriveStatus').innerHTML = '<p style="color:#e74c3c">Lỗi tải trạng thái GDrive</p>';
            }
        }

        async function saveGDriveCreds() {
            const text = document.getElementById('gdriveCredsInput').value.trim();
            if (!text) return;
            try {
                JSON.parse(text); // validate JSON
            } catch(e) { alert('JSON không hợp lệ!'); return; }
            try {
                const res = await fetch('/api/gdrive/credentials', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({credentials: text})
                });
                const data = await res.json();
                alert(data.message);
                loadGDrive();
            } catch(e) { alert('Lỗi kết nối'); }
        }

        async function submitGDriveAuth() {
            const code = document.getElementById('gdriveAuthCode').value.trim();
            if (!code) return;
            try {
                const res = await fetch('/api/gdrive/authorize', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code})
                });
                const data = await res.json();
                alert(data.message);
                loadGDrive();
            } catch(e) { alert('Lỗi kết nối'); }
        }

        async function disconnectGDrive() {
            if (!confirm('Ngắt kết nối Google Drive?')) return;
            await fetch('/api/gdrive/disconnect', {method: 'POST'});
            document.getElementById('gdriveFiles').style.display = 'none';
            loadGDrive();
        }

        async function loadGDriveFiles() {
            try {
                const res = await fetch('/api/gdrive/files');
                const files = await res.json();
                const el = document.getElementById('gdriveFilesList');
                if (!files.length) {
                    el.innerHTML = '<p style="color:var(--text2);text-align:center">Chưa có file nào</p>';
                    return;
                }
                el.innerHTML = files.map(f => `
                    <div class="history-item">
                        <div class="history-icon">📄</div>
                        <div class="history-details">
                            <div class="history-title"><a href="${f.link}" target="_blank" style="color:var(--text1);text-decoration:none">${f.name}</a></div>
                            <div class="history-meta">${(f.size/1024/1024).toFixed(1)}MB · ${new Date(f.created).toLocaleString('vi-VN')}</div>
                        </div>
                    </div>`).join('');
            } catch(e) { console.error(e); }
        }

        // ═══ Init ═══
        document.getElementById('urlInput').addEventListener('keydown', e => {
            if (e.key === 'Enter') startDownload();
        });
        loadHistory();
    </script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEB_PORT", os.environ.get("API_PORT", "8000")))
    uvicorn.run(app, host="0.0.0.0", port=port)
