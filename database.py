"""
Database layer for Scribd Downloader
Uses SQLite for download history, queue management, stats, and account management.
"""

import os
import sqlite3
import time
import json
import threading
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "scribd_bot.db")

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def get_db():
    """Context manager for database operations."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Initialize database tables."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT DEFAULT '',
                pages INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                file_path TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                source TEXT DEFAULT 'telegram',
                user_id TEXT DEFAULT '',
                user_name TEXT DEFAULT '',
                account_id INTEGER DEFAULT 0,
                error_message TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                completed_at TEXT,
                duration_seconds REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT DEFAULT 'web',
                user_id TEXT DEFAULT '',
                priority INTEGER DEFAULT 0,
                status TEXT DEFAULT 'waiting',
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT
            );

            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                total_downloads INTEGER DEFAULT 0,
                successful INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                UNIQUE(date)
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password TEXT DEFAULT '',
                cookies_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active',
                label TEXT DEFAULT '',
                download_count INTEGER DEFAULT 0,
                last_used_at TEXT,
                last_login_at TEXT,
                error_message TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_downloads_doc_id ON downloads(doc_id);
            CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
            CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id);
            CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
            CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
        """)


# ═══════════════════════════════════════════
# Account Management
# ═══════════════════════════════════════════

def add_account(email: str, password: str = "", cookies: list = None,
                label: str = "") -> int:
    """Add a new Scribd account. Returns account ID."""
    cookies_str = json.dumps(cookies or [])
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO accounts (email, password, cookies_json, label, status)
               VALUES (?, ?, ?, ?, 'active')
               ON CONFLICT(email) DO UPDATE SET
                   password = excluded.password,
                   cookies_json = excluded.cookies_json,
                   label = CASE WHEN excluded.label != '' THEN excluded.label ELSE accounts.label END,
                   status = 'active',
                   updated_at = datetime('now')""",
            (email, password, cookies_str, label)
        )
        return cursor.lastrowid


def get_account(account_id: int) -> Optional[dict]:
    """Get a single account by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_accounts(include_disabled: bool = False) -> list[dict]:
    """Get all accounts."""
    with get_db() as conn:
        if include_disabled:
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY created_at"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM accounts WHERE status = 'active' ORDER BY download_count ASC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_next_account() -> Optional[dict]:
    """Get the next account to use (round-robin by least used, active only)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM accounts
               WHERE status = 'active'
               ORDER BY
                   CASE WHEN last_used_at IS NULL THEN 0 ELSE 1 END,
                   last_used_at ASC,
                   download_count ASC
               LIMIT 1"""
        ).fetchone()
        if row:
            d = dict(row)
            # Mark as used
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE accounts SET
                       last_used_at = ?,
                       download_count = download_count + 1,
                       updated_at = ?
                   WHERE id = ?""",
                (now, now, d["id"])
            )
            return d
    return None


def update_account(account_id: int, **kwargs):
    """Update account fields."""
    allowed = {"email", "password", "cookies_json", "status", "label",
               "error_message", "last_login_at", "last_used_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [account_id]
    with get_db() as conn:
        conn.execute(f"UPDATE accounts SET {set_clause} WHERE id = ?", values)


def update_account_cookies(account_id: int, cookies: list):
    """Update cookies for an account."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """UPDATE accounts SET
                   cookies_json = ?,
                   last_login_at = ?,
                   status = 'active',
                   error_message = '',
                   updated_at = ?
               WHERE id = ?""",
            (json.dumps(cookies), now, now, account_id)
        )


def mark_account_error(account_id: int, error: str):
    """Mark an account as having an error."""
    update_account(account_id, status="error", error_message=error)


def disable_account(account_id: int):
    """Disable an account."""
    update_account(account_id, status="disabled")


def enable_account(account_id: int):
    """Re-enable an account."""
    update_account(account_id, status="active", error_message="")


def delete_account(account_id: int):
    """Delete an account permanently."""
    with get_db() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def get_account_by_email(email: str) -> Optional[dict]:
    """Find account by email."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_active_account_count() -> int:
    """Get number of active accounts."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM accounts WHERE status = 'active'"
        ).fetchone()
        return row["cnt"]


def get_account_cookies(account_id: int) -> list:
    """Get parsed cookies for an account."""
    acct = get_account(account_id)
    if acct and acct["cookies_json"]:
        try:
            return json.loads(acct["cookies_json"])
        except json.JSONDecodeError:
            return []
    return []


# ═══════════════════════════════════════════
# Download History
# ═══════════════════════════════════════════

def add_download(doc_id: str, url: str, source: str = "telegram",
                 user_id: str = "", user_name: str = "",
                 account_id: int = 0) -> int:
    """Add a new download record. Returns the record ID."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO downloads (doc_id, url, source, user_id, user_name, account_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'downloading')""",
            (doc_id, url, source, user_id, user_name, account_id)
        )
        return cursor.lastrowid


def update_download(record_id: int, **kwargs):
    """Update a download record with given fields."""
    allowed = {"title", "pages", "file_size", "file_path", "status",
               "error_message", "started_at", "completed_at", "duration_seconds",
               "account_id"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [record_id]
    with get_db() as conn:
        conn.execute(f"UPDATE downloads SET {set_clause} WHERE id = ?", values)


def mark_download_success(record_id: int, title: str, pages: int,
                          file_size: int, file_path: str, duration: float):
    """Mark download as successful."""
    now = datetime.now(timezone.utc).isoformat()
    update_download(
        record_id,
        title=title, pages=pages, file_size=file_size,
        file_path=file_path, status="completed",
        completed_at=now, duration_seconds=round(duration, 1)
    )
    _update_daily_stats(success=True, pages=pages, bytes_size=file_size)


def mark_download_failed(record_id: int, error: str, duration: float):
    """Mark download as failed."""
    now = datetime.now(timezone.utc).isoformat()
    update_download(
        record_id,
        status="failed", error_message=error,
        completed_at=now, duration_seconds=round(duration, 1)
    )
    _update_daily_stats(success=False)


def get_download_history(limit: int = 50, offset: int = 0,
                         status: str = None, source: str = None) -> list[dict]:
    """Get download history with optional filters."""
    with get_db() as conn:
        query = "SELECT * FROM downloads WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_download_by_doc_id(doc_id: str) -> Optional[dict]:
    """Get the most recent download for a doc_id."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM downloads WHERE doc_id = ? ORDER BY created_at DESC LIMIT 1",
            (doc_id,)
        ).fetchone()
        return dict(row) if row else None


def get_cached_download(doc_id: str) -> Optional[dict]:
    """Get a completed download if the file still exists (cache hit)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM downloads
               WHERE doc_id = ? AND status = 'completed' AND file_path != ''
               ORDER BY created_at DESC LIMIT 1""",
            (doc_id,)
        ).fetchone()
        if row:
            d = dict(row)
            if d["file_path"] and os.path.exists(d["file_path"]):
                return d
    return None


def get_total_downloads() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM downloads").fetchone()
        return row["cnt"]


def search_downloads(query: str, limit: int = 20) -> list[dict]:
    """Search downloads by title or URL."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM downloads
               WHERE title LIKE ? OR url LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", limit)
        ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════
# Queue Management
# ═══════════════════════════════════════════

def add_to_queue(doc_id: str, url: str, source: str = "web",
                 user_id: str = "", priority: int = 0) -> int:
    """Add a download to the queue. Returns queue ID."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO queue (doc_id, url, source, user_id, priority)
               VALUES (?, ?, ?, ?, ?)""",
            (doc_id, url, source, user_id, priority)
        )
        return cursor.lastrowid


def get_next_in_queue() -> Optional[dict]:
    """Get the next waiting item from the queue (highest priority first)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM queue
               WHERE status = 'waiting'
               ORDER BY priority DESC, created_at ASC
               LIMIT 1"""
        ).fetchone()
        if row:
            d = dict(row)
            conn.execute(
                "UPDATE queue SET status = 'processing', started_at = datetime('now') WHERE id = ?",
                (d["id"],)
            )
            return d
    return None


def complete_queue_item(queue_id: int, status: str = "completed"):
    """Mark a queue item as completed or failed."""
    with get_db() as conn:
        conn.execute(
            "UPDATE queue SET status = ? WHERE id = ?",
            (status, queue_id)
        )


def get_queue_status() -> dict:
    """Get current queue statistics."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM queue GROUP BY status"
        ).fetchall()
        result = {"waiting": 0, "processing": 0, "completed": 0, "failed": 0}
        for r in rows:
            result[r["status"]] = r["cnt"]
        return result


def get_queue_items(status: str = "waiting", limit: int = 20) -> list[dict]:
    """Get queue items by status."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status = ? ORDER BY priority DESC, created_at ASC LIMIT ?",
            (status, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def clear_old_queue(hours: int = 24):
    """Remove completed/failed queue items older than N hours."""
    with get_db() as conn:
        conn.execute(
            """DELETE FROM queue
               WHERE status IN ('completed', 'failed')
               AND created_at < datetime('now', ? || ' hours')""",
            (f"-{hours}",)
        )


# ═══════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════

def _update_daily_stats(success: bool, pages: int = 0, bytes_size: int = 0):
    """Update daily statistics."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO stats (date, total_downloads, successful, failed, total_pages, total_bytes)
               VALUES (?, 1, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   total_downloads = total_downloads + 1,
                   successful = successful + ?,
                   failed = failed + ?,
                   total_pages = total_pages + ?,
                   total_bytes = total_bytes + ?""",
            (today,
             1 if success else 0, 0 if success else 1, pages, bytes_size,
             1 if success else 0, 0 if success else 1, pages, bytes_size)
        )


def get_stats_summary() -> dict:
    """Get overall statistics summary."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status='downloading' THEN 1 ELSE 0 END) as active,
                SUM(pages) as total_pages,
                SUM(file_size) as total_bytes,
                AVG(CASE WHEN status='completed' THEN duration_seconds END) as avg_duration
               FROM downloads"""
        ).fetchone()
        return dict(row) if row else {}


def get_daily_stats(days: int = 30) -> list[dict]:
    """Get daily stats for the last N days."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM stats
               ORDER BY date DESC LIMIT ?""",
            (days,)
        ).fetchall()
        return [dict(r) for r in rows]


# Initialize on import
init_db()
