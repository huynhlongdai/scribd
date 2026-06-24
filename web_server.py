"""
Scribd Downloader Web Server
Full-featured web interface with download, history, queue, and stats.
"""

import asyncio
import os
import time
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from downloader import download_scribd_document, extract_doc_id, extract_doc_title
import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scribd Downloader", version="2.0.0")

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
COOKIES_PATH = os.environ.get("COOKIES_PATH", "")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2"))

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Track active downloads
_active_count = 0
_active_lock = asyncio.Lock()


class DownloadRequest(BaseModel):
    url: str


# ═══════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the main web page."""
    return get_html()


@app.get("/api/health")
async def health():
    return {"status": "ok", "active": _active_count}


@app.post("/api/download")
async def api_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    """Start a download or return cached result."""
    doc_id = extract_doc_id(req.url)
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
            # Add to queue
            queue_id = db.add_to_queue(doc_id, req.url, source="web")
            return {
                "status": "queued",
                "doc_id": doc_id,
                "queue_id": queue_id,
                "message": f"Đã thêm vào hàng đợi (vị trí #{db.get_queue_status()['waiting']})"
            }

    # Start download
    record_id = db.add_download(doc_id, req.url, source="web")
    background_tasks.add_task(_do_download, doc_id, req.url, record_id)
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
    # Fallback: search in download dir
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
    """Get download history."""
    items = db.get_download_history(limit=limit, offset=offset,
                                     status=status, source=source)
    total = db.get_total_downloads()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/search")
async def search(q: str, limit: int = 20):
    """Search download history."""
    items = db.search_downloads(q, limit=limit)
    return {"items": items, "query": q}


@app.get("/api/queue")
async def get_queue():
    """Get queue status and items."""
    status = db.get_queue_status()
    waiting = db.get_queue_items("waiting")
    processing = db.get_queue_items("processing")
    return {"status": status, "waiting": waiting, "processing": processing}


@app.get("/api/stats")
async def get_stats():
    """Get download statistics."""
    summary = db.get_stats_summary()
    daily = db.get_daily_stats(30)
    return {"summary": summary, "daily": daily}


@app.delete("/api/history/{record_id}")
async def delete_record(record_id: int):
    """Delete a download record."""
    db.update_download(record_id, status="deleted")
    return {"ok": True}


# ═══════════════════════════════════════════
# Background Download Worker
# ═══════════════════════════════════════════

async def _do_download(doc_id: str, url: str, record_id: int):
    """Background download task."""
    global _active_count
    async with _active_lock:
        _active_count += 1

    start_time = time.time()
    try:
        cookies_path = COOKIES_PATH if COOKIES_PATH and os.path.exists(COOKIES_PATH) else None
        result = await download_scribd_document(
            url=url,
            output_dir=DOWNLOAD_DIR,
            cookies_json=cookies_path,
        )
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
            logger.info(f"✅ Downloaded {doc_id}: {result['title']} ({result['pages']}p, {duration:.1f}s)")
        else:
            db.mark_download_failed(record_id, result["error"], duration)
            logger.error(f"❌ Failed {doc_id}: {result['error']}")
    except Exception as e:
        duration = time.time() - start_time
        db.mark_download_failed(record_id, str(e), duration)
        logger.error(f"❌ Error {doc_id}: {e}")
    finally:
        async with _active_lock:
            _active_count -= 1
        # Process queue
        await _process_queue()


async def _process_queue():
    """Process next item in queue if capacity available."""
    global _active_count
    async with _active_lock:
        if _active_count >= MAX_CONCURRENT:
            return

    item = db.get_next_in_queue()
    if item:
        record_id = db.add_download(
            item["doc_id"], item["url"],
            source=item["source"], user_id=item["user_id"]
        )
        db.complete_queue_item(item["id"], "completed")
        await _do_download(item["doc_id"], item["url"], record_id)


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
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }

        /* Header */
        .header {
            text-align: center; padding: 40px 0 30px;
        }
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
        .input-row {
            display: flex; gap: 12px;
        }
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
        .badge-web { background: rgba(108,92,231,0.15); color: var(--accent2); }
        .badge-telegram { background: rgba(0,184,148,0.15); color: var(--success); }

        .title-cell {
            max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .dl-link {
            color: var(--accent2); text-decoration: none; font-weight: 500;
        }
        .dl-link:hover { text-decoration: underline; }

        /* Stats */
        .stats-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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

        /* Responsive */
        @media (max-width: 640px) {
            .input-row { flex-direction: column; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .header h1 { font-size: 1.5rem; }
            th, td { padding: 10px 12px; font-size: 0.8rem; }
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
            <button class="tab" data-tab="stats" onclick="switchTab('stats')">📊 Thống kê</button>
        </div>

        <div id="tab-history">
            <input type="text" class="search-bar" id="searchInput"
                   placeholder="🔍 Tìm theo tên tài liệu..." oninput="debounceSearch()">
            <div class="panel" id="historyPanel">
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <p>Chưa có lịch sử tải</p>
                </div>
            </div>
        </div>

        <div id="tab-queue" style="display:none">
            <div class="panel" id="queuePanel">
                <div class="empty-state">
                    <div class="icon">✅</div>
                    <p>Hàng đợi trống</p>
                </div>
            </div>
        </div>

        <div id="tab-stats" style="display:none">
            <div class="stats-grid" id="statsGrid"></div>
        </div>
    </div>

    <script>
        let currentTab = 'history';
        let pollInterval = null;
        let searchTimeout = null;

        // ═══ Download ═══
        async function startDownload() {
            const url = document.getElementById('urlInput').value.trim();
            if (!url) return;
            if (!url.includes('scribd.com')) {
                showStatus('error', '❌ Vui lòng nhập link Scribd hợp lệ');
                return;
            }

            const btn = document.getElementById('downloadBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Đang xử lý...';
            showStatus('downloading', '<span class="spinner"></span> Đang bắt đầu tải...');

            try {
                const res = await fetch('/api/download', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url})
                });
                const data = await res.json();

                if (data.status === 'cached') {
                    showStatus('cached', `✅ <b>${data.title}</b> (${data.pages} trang) — đã có sẵn!
                        <br><a class="dl-link" href="${data.download_url}" target="_blank">⬇️ Tải PDF</a>`);
                    btn.disabled = false;
                    btn.textContent = '⬇️ Tải PDF';
                    loadHistory();
                } else if (data.status === 'queued') {
                    showStatus('downloading', `🔄 ${data.message}`);
                    startPolling(data.doc_id);
                } else if (data.status === 'started') {
                    showStatus('downloading', '<span class="spinner"></span> Đang tải tài liệu... (có thể mất 30s-2 phút)');
                    startPolling(data.doc_id);
                } else if (data.status === 'downloading') {
                    showStatus('downloading', '<span class="spinner"></span> Đang tải...');
                    startPolling(data.doc_id);
                } else {
                    showStatus('error', `❌ ${data.detail || data.message || 'Lỗi không xác định'}`);
                    btn.disabled = false;
                    btn.textContent = '⬇️ Tải PDF';
                }
            } catch (e) {
                showStatus('error', `❌ Lỗi kết nối: ${e.message}`);
                btn.disabled = false;
                btn.textContent = '⬇️ Tải PDF';
            }
        }

        function startPolling(docId) {
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/status/${docId}`);
                    const data = await res.json();
                    if (data.status === 'completed') {
                        clearInterval(pollInterval);
                        pollInterval = null;
                        showStatus('success', `✅ <b>${data.title}</b> — ${data.pages} trang (${(data.file_size/1024/1024).toFixed(1)}MB, ${data.duration.toFixed(0)}s)
                            <br><a class="dl-link" href="${data.download_url}" target="_blank">⬇️ Tải PDF ngay</a>`);
                        document.getElementById('downloadBtn').disabled = false;
                        document.getElementById('downloadBtn').textContent = '⬇️ Tải PDF';
                        loadHistory();
                    } else if (data.status === 'failed') {
                        clearInterval(pollInterval);
                        pollInterval = null;
                        showStatus('error', `❌ ${data.error || 'Tải thất bại'}`);
                        document.getElementById('downloadBtn').disabled = false;
                        document.getElementById('downloadBtn').textContent = '⬇️ Tải PDF';
                        loadHistory();
                    }
                } catch(e) {}
            }, 3000);
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
                const dl = item.status === 'completed'
                    ? `<a class="dl-link" href="/api/file/${item.doc_id}" target="_blank">⬇️</a>`
                    : '';
                html += `<tr>
                    <td class="title-cell" title="${title}">${title}</td>
                    <td>${item.pages || '-'}</td>
                    <td>${srcBadge}</td>
                    <td>${badge}</td>
                    <td style="color:var(--text2)">${time}</td>
                    <td>${dl}</td>
                </tr>`;
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
                    panel.innerHTML = '<div class="empty-state"><div class="icon">✅</div><p>Hàng đợi trống — tất cả đã xử lý</p></div>';
                    return;
                }
                let html = `<table><thead><tr><th>URL</th><th>Nguồn</th><th>Trạng thái</th><th>Thời gian</th></tr></thead><tbody>`;
                for (const item of all) {
                    const time = new Date(item.created_at + 'Z').toLocaleString('vi-VN', {hour:'2-digit',minute:'2-digit'});
                    const badge = `<span class="badge badge-${item.status}">${item.status}</span>`;
                    html += `<tr><td class="title-cell">${item.url}</td><td>${item.source}</td><td>${badge}</td><td>${time}</td></tr>`;
                }
                html += '</tbody></table>';
                panel.innerHTML = html;
            } catch(e) {}
        }

        // ═══ Stats ═══
        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                const s = data.summary;
                const grid = document.getElementById('statsGrid');
                const mb = ((s.total_bytes || 0) / 1024 / 1024).toFixed(1);
                grid.innerHTML = `
                    <div class="stat-card"><div class="number">${s.total || 0}</div><div class="label">Tổng tải</div></div>
                    <div class="stat-card"><div class="number">${s.successful || 0}</div><div class="label">Thành công</div></div>
                    <div class="stat-card"><div class="number">${s.failed || 0}</div><div class="label">Thất bại</div></div>
                    <div class="stat-card"><div class="number">${s.total_pages || 0}</div><div class="label">Tổng trang</div></div>
                    <div class="stat-card"><div class="number">${mb}MB</div><div class="label">Dung lượng</div></div>
                    <div class="stat-card"><div class="number">${(s.avg_duration || 0).toFixed(0)}s</div><div class="label">TB thời gian</div></div>
                `;
            } catch(e) {}
        }

        // ═══ Tabs ═══
        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
            document.getElementById('tab-history').style.display = tab === 'history' ? '' : 'none';
            document.getElementById('tab-queue').style.display = tab === 'queue' ? '' : 'none';
            document.getElementById('tab-stats').style.display = tab === 'stats' ? '' : 'none';
            if (tab === 'history') loadHistory();
            if (tab === 'queue') loadQueue();
            if (tab === 'stats') loadStats();
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
