"""
Batch Download Scheduler for Scribd Downloader
Schedule downloads of multiple files with timing control.

Features:
- Create schedules with list of URLs
- One-time or recurring (daily/weekly) schedules
- Auto-upload to Google Drive after download
- Progress tracking per schedule
- Concurrent download control
- Web UI and Telegram bot integration
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import threading

import database as db
import gdrive
from downloader import download_scribd_document, extract_doc_id
import account_manager as acct_mgr
import ai_helper

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2"))

# In-memory scheduler state
_scheduler_task: Optional[asyncio.Task] = None
_running = False


# ═══════════════════════════════════════════
# Database Schema Extension
# ═══════════════════════════════════════════

def init_scheduler_db():
    """Create scheduler tables."""
    with db.get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                schedule_type TEXT DEFAULT 'once',
                cron_expression TEXT DEFAULT '',
                scheduled_at TEXT,
                repeat_interval_hours INTEGER DEFAULT 0,
                upload_to_gdrive INTEGER DEFAULT 0,
                gdrive_folder TEXT DEFAULT 'Scribd Downloads',
                max_concurrent INTEGER DEFAULT 2,
                status TEXT DEFAULT 'pending',
                created_by TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                completed_at TEXT,
                last_run_at TEXT,
                run_count INTEGER DEFAULT 0,
                error_message TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS schedule_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                doc_id TEXT DEFAULT '',
                title TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                pages INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                file_path TEXT DEFAULT '',
                gdrive_link TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                completed_at TEXT,
                duration_seconds REAL DEFAULT 0,
                FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_schedule_status ON schedules(status);
            CREATE INDEX IF NOT EXISTS idx_schedule_items_schedule ON schedule_items(schedule_id);
            CREATE INDEX IF NOT EXISTS idx_schedule_items_status ON schedule_items(status);
        """)


# Initialize on import
init_scheduler_db()


# ═══════════════════════════════════════════
# Schedule CRUD
# ═══════════════════════════════════════════

def create_schedule(name: str, urls: list[str], schedule_type: str = "once",
                    scheduled_at: str = None, repeat_hours: int = 0,
                    upload_gdrive: bool = False, gdrive_folder: str = "Scribd Downloads",
                    max_concurrent: int = 2, created_by: str = "") -> dict:
    """
    Create a new download schedule.
    
    Args:
        name: Schedule name
        urls: List of Scribd URLs to download
        schedule_type: 'now', 'once' (at scheduled_at), 'recurring'
        scheduled_at: ISO datetime for scheduled execution
        repeat_hours: Hours between runs (for recurring)
        upload_gdrive: Upload to Google Drive after download
        gdrive_folder: Google Drive folder name
        max_concurrent: Max concurrent downloads
        created_by: User ID who created
    """
    with db.get_db() as conn:
        status = "running" if schedule_type == "now" else "pending"
        cursor = conn.execute(
            """INSERT INTO schedules 
               (name, schedule_type, scheduled_at, repeat_interval_hours,
                upload_to_gdrive, gdrive_folder, max_concurrent, status, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, schedule_type, scheduled_at, repeat_hours,
             1 if upload_gdrive else 0, gdrive_folder, max_concurrent, status, created_by)
        )
        schedule_id = cursor.lastrowid

        # Parse and add URLs
        parsed_items = []
        for i, raw_url in enumerate(urls):
            parsed = ai_helper.smart_parse_input(raw_url)
            url = parsed.get("fixed_url") or raw_url
            doc_id = parsed.get("doc_id") or extract_doc_id(url) or ""

            conn.execute(
                """INSERT INTO schedule_items 
                   (schedule_id, url, doc_id, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (schedule_id, url, doc_id, i)
            )
            parsed_items.append({"url": url, "doc_id": doc_id})

    return {
        "id": schedule_id,
        "name": name,
        "items_count": len(parsed_items),
        "status": status,
        "items": parsed_items,
    }


def get_schedule(schedule_id: int) -> Optional[dict]:
    """Get schedule with its items."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if not row:
            return None
        schedule = dict(row)

        items = conn.execute(
            "SELECT * FROM schedule_items WHERE schedule_id = ? ORDER BY sort_order",
            (schedule_id,)
        ).fetchall()
        schedule["items"] = [dict(i) for i in items]

        # Summary
        statuses = [i["status"] for i in schedule["items"]]
        schedule["total"] = len(statuses)
        schedule["completed"] = statuses.count("completed")
        schedule["failed"] = statuses.count("failed")
        schedule["pending"] = statuses.count("pending")
        schedule["downloading"] = statuses.count("downloading")

        return schedule


def get_all_schedules(limit: int = 50) -> list[dict]:
    """Get all schedules with summary stats."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT s.*,
                (SELECT COUNT(*) FROM schedule_items WHERE schedule_id = s.id) as total_items,
                (SELECT COUNT(*) FROM schedule_items WHERE schedule_id = s.id AND status = 'completed') as completed_items,
                (SELECT COUNT(*) FROM schedule_items WHERE schedule_id = s.id AND status = 'failed') as failed_items
               FROM schedules s
               ORDER BY s.created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_schedule(schedule_id: int):
    """Delete a schedule and its items."""
    with db.get_db() as conn:
        conn.execute("DELETE FROM schedule_items WHERE schedule_id = ?", (schedule_id,))
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def add_urls_to_schedule(schedule_id: int, urls: list[str]) -> int:
    """Add more URLs to an existing schedule. Returns count added."""
    with db.get_db() as conn:
        max_order = conn.execute(
            "SELECT MAX(sort_order) as mx FROM schedule_items WHERE schedule_id = ?",
            (schedule_id,)
        ).fetchone()["mx"] or 0

        added = 0
        for i, raw_url in enumerate(urls):
            parsed = ai_helper.smart_parse_input(raw_url)
            url = parsed.get("fixed_url") or raw_url
            doc_id = parsed.get("doc_id") or extract_doc_id(url) or ""

            conn.execute(
                """INSERT INTO schedule_items 
                   (schedule_id, url, doc_id, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (schedule_id, url, doc_id, max_order + i + 1)
            )
            added += 1

    return added


def pause_schedule(schedule_id: int):
    """Pause a running schedule."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE schedules SET status = 'paused' WHERE id = ? AND status = 'running'",
            (schedule_id,)
        )


def resume_schedule(schedule_id: int):
    """Resume a paused schedule."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE schedules SET status = 'running' WHERE id = ? AND status = 'paused'",
            (schedule_id,)
        )


# ═══════════════════════════════════════════
# Download Execution
# ═══════════════════════════════════════════

async def _download_item(item: dict, upload_gdrive: bool = False,
                         gdrive_folder: str = "Scribd Downloads") -> dict:
    """Download a single schedule item."""
    item_id = item["id"]
    url = item["url"]
    doc_id = item["doc_id"]

    # Mark as downloading
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE schedule_items SET status = 'downloading', started_at = ? WHERE id = ?",
            (now, item_id)
        )

    start = time.time()

    try:
        # Get account cookies
        cookies, account_id = acct_mgr.get_cookies_for_download()

        dl_kwargs = {"url": url, "output_dir": DOWNLOAD_DIR}
        if cookies:
            dl_kwargs["cookies_list"] = cookies

        result = await download_scribd_document(**dl_kwargs)
        duration = time.time() - start

        if result["success"]:
            pdf_path = result["pdf_path"]
            title = result["title"]
            pages = result["pages"]
            file_size = os.path.getsize(pdf_path)

            gdrive_link = ""

            # Upload to Google Drive if enabled
            if upload_gdrive:
                try:
                    gd_result = gdrive.upload_scribd_pdf(
                        pdf_path, title, doc_id, base_folder=gdrive_folder
                    )
                    if gd_result["success"]:
                        gdrive_link = gd_result.get("web_link", "")
                        logger.info(f"  → Uploaded to GDrive: {gdrive_link}")
                except Exception as e:
                    logger.warning(f"GDrive upload failed for {doc_id}: {e}")

            now = datetime.now(timezone.utc).isoformat()
            with db.get_db() as conn:
                conn.execute(
                    """UPDATE schedule_items SET
                       status = 'completed', title = ?, pages = ?,
                       file_size = ?, file_path = ?, gdrive_link = ?,
                       completed_at = ?, duration_seconds = ?
                       WHERE id = ?""",
                    (title, pages, file_size, pdf_path, gdrive_link,
                     now, round(duration, 1), item_id)
                )

            # Also log in main downloads table
            rid = db.add_download(doc_id, url, source="scheduler", account_id=account_id)
            db.mark_download_success(rid, title, pages, file_size, pdf_path, duration)

            return {"success": True, "title": title, "pages": pages}

        else:
            now = datetime.now(timezone.utc).isoformat()
            with db.get_db() as conn:
                conn.execute(
                    """UPDATE schedule_items SET
                       status = 'failed', error_message = ?,
                       completed_at = ?, duration_seconds = ?
                       WHERE id = ?""",
                    (result["error"], now, round(duration, 1), item_id)
                )
            return {"success": False, "error": result["error"]}

    except Exception as e:
        duration = time.time() - start
        now = datetime.now(timezone.utc).isoformat()
        with db.get_db() as conn:
            conn.execute(
                """UPDATE schedule_items SET
                   status = 'failed', error_message = ?,
                   completed_at = ?, duration_seconds = ?
                   WHERE id = ?""",
                (str(e), now, round(duration, 1), item_id)
            )
        return {"success": False, "error": str(e)}


async def run_schedule(schedule_id: int):
    """Execute a schedule: download all pending items with concurrency control."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        logger.error(f"Schedule {schedule_id} not found")
        return

    max_concurrent = schedule.get("max_concurrent", MAX_CONCURRENT)
    upload_gdrive = bool(schedule.get("upload_to_gdrive", 0))
    gdrive_folder = schedule.get("gdrive_folder", "Scribd Downloads")

    # Mark schedule as running
    now = datetime.now(timezone.utc).isoformat()
    with db.get_db() as conn:
        conn.execute(
            """UPDATE schedules SET status = 'running', started_at = ?,
               last_run_at = ?, run_count = run_count + 1
               WHERE id = ?""",
            (now, now, schedule_id)
        )

    pending_items = [i for i in schedule["items"] if i["status"] == "pending"]
    logger.info(f"▶ Schedule '{schedule['name']}': {len(pending_items)} items to download")

    # Semaphore for concurrency control
    sem = asyncio.Semaphore(max_concurrent)

    async def limited_download(item):
        # Check if schedule was paused
        s = get_schedule(schedule_id)
        if s and s["status"] == "paused":
            return {"success": False, "error": "Schedule paused"}

        async with sem:
            logger.info(f"  ⬇ Downloading {item['doc_id']} ({item['url'][:60]}...)")
            return await _download_item(item, upload_gdrive, gdrive_folder)

    # Run all downloads with concurrency limit
    tasks = [limited_download(item) for item in pending_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Summarize
    success = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
    failed = len(results) - success

    # Mark schedule complete
    now = datetime.now(timezone.utc).isoformat()
    final_status = "completed" if failed == 0 else "completed_with_errors"

    with db.get_db() as conn:
        conn.execute(
            """UPDATE schedules SET status = ?, completed_at = ?,
               error_message = ?
               WHERE id = ?""",
            (final_status, now,
             f"{success} thành công, {failed} thất bại" if failed else "",
             schedule_id)
        )

    logger.info(f"✅ Schedule '{schedule['name']}' done: {success}/{len(results)} success")
    return {"success": success, "failed": failed, "total": len(results)}


# ═══════════════════════════════════════════
# Scheduler Loop
# ═══════════════════════════════════════════

async def _scheduler_loop():
    """Background loop that checks for schedules to run."""
    global _running
    _running = True
    logger.info("📅 Scheduler loop started")

    while _running:
        try:
            now = datetime.now(timezone.utc)
            now_str = now.isoformat()

            with db.get_db() as conn:
                # Find schedules that should run now
                # 1. Type 'now' with status 'running' (immediate)
                immediate = conn.execute(
                    """SELECT id FROM schedules 
                       WHERE schedule_type = 'now' AND status = 'running'"""
                ).fetchall()

                # 2. Type 'once' with scheduled_at <= now and status 'pending'
                scheduled = conn.execute(
                    """SELECT id FROM schedules 
                       WHERE schedule_type = 'once' AND status = 'pending'
                       AND scheduled_at <= ?""",
                    (now_str,)
                ).fetchall()

                # 3. Type 'recurring' — check if it's time for next run
                recurring = conn.execute(
                    """SELECT id, last_run_at, repeat_interval_hours FROM schedules
                       WHERE schedule_type = 'recurring' AND status IN ('pending', 'completed', 'completed_with_errors')
                       AND repeat_interval_hours > 0"""
                ).fetchall()

            to_run = [r["id"] for r in immediate] + [r["id"] for r in scheduled]

            # Check recurring schedules
            for r in recurring:
                if not r["last_run_at"]:
                    to_run.append(r["id"])
                else:
                    last = datetime.fromisoformat(r["last_run_at"].replace("Z", "+00:00"))
                    if now - last >= timedelta(hours=r["repeat_interval_hours"]):
                        # Reset items to pending for re-run
                        with db.get_db() as conn:
                            conn.execute(
                                "UPDATE schedule_items SET status = 'pending' WHERE schedule_id = ?",
                                (r["id"],)
                            )
                        to_run.append(r["id"])

            # Execute schedules
            for sid in to_run:
                try:
                    await run_schedule(sid)
                except Exception as e:
                    logger.error(f"Schedule {sid} failed: {e}")
                    with db.get_db() as conn:
                        conn.execute(
                            "UPDATE schedules SET status = 'failed', error_message = ? WHERE id = ?",
                            (str(e), sid)
                        )

        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        # Check every 30 seconds
        await asyncio.sleep(30)


def start_scheduler():
    """Start the background scheduler in the current event loop."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return  # Already running

    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop())
    logger.info("📅 Scheduler started")


def stop_scheduler():
    """Stop the scheduler."""
    global _running, _scheduler_task
    _running = False
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
    logger.info("📅 Scheduler stopped")


async def run_schedule_now(schedule_id: int) -> dict:
    """Trigger a schedule to run immediately."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE schedules SET schedule_type = 'now', status = 'running' WHERE id = ?",
            (schedule_id,)
        )
    return await run_schedule(schedule_id)


def get_scheduler_status() -> dict:
    """Get scheduler status summary."""
    with db.get_db() as conn:
        counts = conn.execute(
            """SELECT status, COUNT(*) as cnt FROM schedules GROUP BY status"""
        ).fetchall()
        items_counts = conn.execute(
            """SELECT status, COUNT(*) as cnt FROM schedule_items GROUP BY status"""
        ).fetchall()

    return {
        "running": _running,
        "schedules": {r["status"]: r["cnt"] for r in counts},
        "items": {r["status"]: r["cnt"] for r in items_counts},
    }
