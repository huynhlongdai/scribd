"""
Scribd Telegram Bot (v2)
Send a Scribd link → get a PDF back.

Features:
- Download Scribd documents as PDF
- Download history with /history command
- Queue system for multiple requests
- Rate limiting per user
- Cache: re-download same doc returns cached file
- Admin stats with /stats
"""

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from downloader import download_scribd_document, extract_doc_id
import database as db
import account_manager as acct_mgr
import ai_helper
import scheduler

# Configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "10"))
RATE_LIMIT_SECONDS = int(os.environ.get("RATE_LIMIT_SECONDS", "30"))
COOKIES_PATH = os.environ.get("COOKIES_PATH", "")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# State
user_last_request: dict[int, float] = defaultdict(float)
active_downloads: dict[int, str] = {}  # user_id -> doc_url
bot_start_time = time.time()


def is_scribd_url(text: str) -> bool:
    return bool(re.search(r'scribd\.com/(doc(ument)?|presentation|embeds)/\d+', text))


def extract_url(text: str) -> str | None:
    match = re.search(r'https?://[^\s]*scribd\.com/[^\s]+', text)
    return match.group(0) if match else None


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


# ═══════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "📚 *Scribd Downloader Bot*\n\n"
        "Gửi link Scribd để tải tài liệu dưới dạng PDF\\.\n\n"
        "*Cách sử dụng:*\n"
        "1\\. Copy link tài liệu từ Scribd\n"
        "2\\. Gửi link vào chat\n"
        "3\\. Đợi bot tải và gửi PDF cho bạn\n\n"
        "*Lệnh:*\n"
        "/start \\- Hướng dẫn\n"
        "/history \\- Lịch sử tải\n"
        "/status \\- Trạng thái hàng đợi\n"
        "/stats \\- Thống kê\n"
        "/accounts \\- Danh sách tài khoản\n"
        "/addaccount \\- Thêm tài khoản Scribd\n"
        "/removeaccount \\- Xóa tài khoản\n"
        "/batch \\- Tải hàng loạt\n"
        "/schedules \\- Xem lịch tải\n"
        "/help \\- Trợ giúp"
    )
    await update.message.reply_text(welcome, parse_mode="MarkdownV2")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Hướng dẫn sử dụng*\n\n"
        "*Các định dạng link được hỗ trợ:*\n"
        "• `scribd.com/document/ID/title`\n"
        "• `scribd.com/doc/ID/title`\n"
        "• `scribd.com/presentation/ID/title`\n\n"
        "*Hỗ trợ:* Documents, PDF, PPT, Word, Excel, Sheet music\n"
        "*Không hỗ trợ:* Books (Everand), Magazines\n\n"
        "*Lưu ý:*\n"
        "• PDF dạng hình ảnh (image-based)\n"
        "• Tài liệu đã tải sẽ được cache\n"
        "• Giới hạn 1 yêu cầu mỗi 30 giây"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show download history for this user."""
    user_id = str(update.effective_user.id)

    # Get all history (filter by user_id in telegram source)
    all_history = db.get_download_history(limit=100, source="telegram")
    # Filter by user
    user_history = [h for h in all_history if h["user_id"] == user_id][:10]

    if not user_history:
        await update.message.reply_text("📭 Bạn chưa có lịch sử tải nào.")
        return

    lines = ["📋 *Lịch sử tải gần đây:*\n"]
    for i, h in enumerate(user_history, 1):
        icon = "✅" if h["status"] == "completed" else "❌" if h["status"] == "failed" else "⏳"
        title = h["title"] or h["doc_id"]
        if len(title) > 40:
            title = title[:37] + "..."
        pages_info = f" ({h['pages']}p)" if h["pages"] else ""
        size_info = f" {format_size(h['file_size'])}" if h["file_size"] else ""
        lines.append(f"{icon} {i}. {title}{pages_info}{size_info}")

    total = len([h for h in all_history if h["user_id"] == user_id])
    lines.append(f"\n📊 Tổng: {total} lượt tải")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - bot_start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)

    summary = db.get_stats_summary()
    queue = db.get_queue_status()

    status = (
        f"📊 *Trạng thái Bot*\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"📥 Tổng yêu cầu: {summary.get('total', 0)}\n"
        f"✅ Thành công: {summary.get('successful', 0)}\n"
        f"❌ Thất bại: {summary.get('failed', 0)}\n"
        f"🔄 Đang xử lý: {summary.get('active', 0)}\n"
        f"📋 Hàng đợi: {queue.get('waiting', 0)}"
    )
    await update.message.reply_text(status, parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed stats (for admins or all users)."""
    summary = db.get_stats_summary()
    total_mb = (summary.get("total_bytes") or 0) / 1024 / 1024
    avg_dur = summary.get("avg_duration") or 0
    acct_summary = acct_mgr.get_accounts_summary()

    text = (
        f"📊 *Thống kê chi tiết*\n\n"
        f"📥 Tổng tải: {summary.get('total', 0)}\n"
        f"✅ Thành công: {summary.get('successful', 0)}\n"
        f"❌ Thất bại: {summary.get('failed', 0)}\n"
        f"📃 Tổng trang: {summary.get('total_pages', 0)}\n"
        f"💾 Dung lượng: {total_mb:.1f}MB\n"
        f"⏱ TB thời gian: {avg_dur:.0f}s\n\n"
        f"👤 *Tài khoản Scribd:*\n"
        f"   Active: {acct_summary['active']} / {acct_summary['total']}\n"
        f"   Lỗi: {acct_summary['error']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show accounts list."""
    summary = acct_mgr.get_accounts_summary()
    if not summary["accounts"]:
        await update.message.reply_text("👤 Chưa có tài khoản Scribd nào.")
        return

    lines = [f"👤 *Tài khoản Scribd ({summary['active']}/{summary['total']} active):*\n"]
    for a in summary["accounts"]:
        icon = "✅" if a["status"] == "active" else "❌" if a["status"] == "error" else "⏸"
        cookies_icon = "🍪" if a["has_cookies"] else "🔒"
        label = f" ({a['label']})" if a["label"] else ""
        lines.append(f"{icon} `{a['email']}`{label} {cookies_icon} — {a['download_count']} lượt")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def addaccount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a Scribd account: /addaccount email password [label]"""
    args = context.args or []
    if len(args) < 1:
        await update.message.reply_text(
            "📝 *Cách dùng:*\n"
            "`/addaccount email@example.com password nhãn`\n\n"
            "Ví dụ: `/addaccount user@gmail.com MyPass123 TK-chính`",
            parse_mode="Markdown"
        )
        return

    email = args[0]
    password = args[1] if len(args) > 1 else ""
    label = " ".join(args[2:]) if len(args) > 2 else ""

    msg = await update.message.reply_text(f"⏳ Đang thêm tài khoản {email}...")

    # Save account (with or without login attempt)
    if password:
        result = await acct_mgr.add_account_with_login(email, password, label)
        await msg.edit_text(
            f"{'✅' if result['success'] else '⚠️'} {result['message']}"
        )
    else:
        acct_id = db.add_account(email=email, label=label)
        await msg.edit_text(
            f"✅ Đã thêm {email} (chưa có mật khẩu/cookies, cần cập nhật qua Web UI)"
        )


async def removeaccount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a Scribd account: /removeaccount email"""
    args = context.args or []
    if not args:
        await update.message.reply_text("📝 Cách dùng: `/removeaccount email@example.com`", parse_mode="Markdown")
        return

    email = args[0]
    account = db.get_account_by_email(email)
    if not account:
        await update.message.reply_text(f"❌ Không tìm thấy tài khoản {email}")
        return

    db.delete_account(account["id"])
    await update.message.reply_text(f"✅ Đã xóa tài khoản {email}")


async def batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create batch download: /batch name\nurl1\nurl2\n..."""
    text = update.message.text.replace("/batch", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "📅 *Tải hàng loạt:*\n\n"
            "```\n/batch Tên lịch tải\nhttps://scribd.com/document/123/...\nhttps://scribd.com/document/456/...\n```\n"
            "Gửi tên ở dòng đầu, các link Scribd ở các dòng sau\\.",
            parse_mode="MarkdownV2"
        )
        return

    lines = text.split("\n")
    name = lines[0].strip()
    urls = [l.strip() for l in lines[1:] if l.strip()]

    if not urls:
        await update.message.reply_text("❌ Chưa có link nào. Mỗi link 1 dòng sau tên.")
        return

    user_id = str(update.effective_user.id)
    result = scheduler.create_schedule(
        name=name, urls=urls, schedule_type="now",
        created_by=user_id
    )

    msg = await update.message.reply_text(
        f"📅 Đã tạo lịch tải *{name}*\n"
        f"📄 {result['items_count']} files\n"
        f"⏳ Đang tải...",
        parse_mode="Markdown"
    )

    # Run in background
    run_result = await scheduler.run_schedule(result["id"])

    if run_result:
        await msg.edit_text(
            f"📅 *{name}* — Hoàn tất!\n\n"
            f"✅ Thành công: {run_result['success']}\n"
            f"❌ Thất bại: {run_result['failed']}\n"
            f"📄 Tổng: {run_result['total']}",
            parse_mode="Markdown"
        )

        # Send successful files
        sched = scheduler.get_schedule(result["id"])
        if sched:
            for item in sched["items"]:
                if item["status"] == "completed" and item["file_path"] and os.path.exists(item["file_path"]):
                    file_size = os.path.getsize(item["file_path"])
                    if file_size <= 50 * 1024 * 1024:
                        try:
                            with open(item["file_path"], "rb") as f:
                                await update.message.reply_document(
                                    document=f,
                                    filename=os.path.basename(item["file_path"]),
                                    caption=f"📚 {item['title']}\n📃 {item.get('pages', '?')} trang",
                                )
                        except Exception as e:
                            logger.warning(f"Failed to send file: {e}")


async def schedules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all schedules: /schedules"""
    schedules = scheduler.get_all_schedules(limit=10)
    if not schedules:
        await update.message.reply_text("📅 Chưa có lịch tải nào.\n\nDùng /batch để tạo mới.")
        return

    lines = ["📅 *Danh sách lịch tải:*\n"]
    for s in schedules:
        icons = {"pending": "⏳", "running": "🔄", "completed": "✅",
                 "completed_with_errors": "⚠️", "failed": "❌", "paused": "⏸"}
        icon = icons.get(s["status"], "📅")
        lines.append(
            f"{icon} *{s['name']}* — {s['total_items']} files "
            f"(✅{s['completed_items']} ❌{s['failed_items']})"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════
# Message Handler
# ═══════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name or ""

    # 🤖 AI Smart Parse
    parsed = ai_helper.smart_parse_input(text)

    if parsed["type"] == "search_query" or not parsed["fixed_url"]:
        if "scribd" in text.lower() or parsed["confidence"] > 0.1:
            suggestions = "\n".join(parsed.get("suggestions", []))
            await update.message.reply_text(
                f"🤖 *AI:* Không nhận dạng được link Scribd\\.\n\n"
                f"{suggestions}\n\n"
                f"Ví dụ: `https://www\\.scribd\\.com/document/123456/Title`",
                parse_mode="MarkdownV2"
            )
        return

    url = parsed["fixed_url"]
    doc_id = parsed.get("doc_id") or extract_doc_id(url)

    # Show AI fix info if URL was modified
    if parsed.get("issues") and parsed["fixed_url"] != text.strip():
        fixes = ", ".join(parsed["issues"])
        await update.message.reply_text(f"🤖 AI đã sửa link: {fixes}")

    if not doc_id:
        await update.message.reply_text("❌ Link Scribd không hợp lệ.")
        return

    # Check cache
    cached = db.get_cached_download(doc_id)
    if cached and os.path.exists(cached["file_path"]):
        await update.message.reply_text(
            f"📦 Đã có trong cache! Đang gửi lại..."
        )
        try:
            with open(cached["file_path"], 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=os.path.basename(cached["file_path"]),
                    caption=f"📚 {cached['title']}\n📃 {cached['pages']} trang (cache)",
                )
            # Log as new download too
            rid = db.add_download(doc_id, url, source="telegram",
                                  user_id=str(user_id), user_name=user_name)
            db.mark_download_success(
                rid, cached["title"], cached["pages"],
                cached["file_size"], cached["file_path"], 0
            )
            return
        except Exception as e:
            logger.warning(f"Cache send failed: {e}")

    # Rate limiting
    now = time.time()
    time_since_last = now - user_last_request[user_id]
    if time_since_last < RATE_LIMIT_SECONDS:
        wait = int(RATE_LIMIT_SECONDS - time_since_last)
        await update.message.reply_text(f"⏳ Vui lòng đợi {wait} giây.")
        return

    if user_id in active_downloads:
        await update.message.reply_text("⏳ Bạn đã có 1 yêu cầu đang xử lý.")
        return

    user_last_request[user_id] = now

    status_msg = await update.message.reply_text(
        f"📥 Đang tải tài liệu...\n"
        f"📄 Doc ID: `{doc_id}`\n"
        f"⏳ Vui lòng đợi (30s - 2 phút)...",
        parse_mode="Markdown"
    )

    active_downloads[user_id] = url

    # Get account cookies (round-robin rotation)
    cookies, account_id = acct_mgr.get_cookies_for_download()

    record_id = db.add_download(doc_id, url, source="telegram",
                                user_id=str(user_id), user_name=user_name,
                                account_id=account_id)
    start_time = time.time()

    try:
        dl_kwargs = {"url": url, "output_dir": DOWNLOAD_DIR}
        if cookies:
            dl_kwargs["cookies_list"] = cookies
        elif COOKIES_PATH and os.path.exists(COOKIES_PATH):
            dl_kwargs["cookies_json"] = COOKIES_PATH

        result = await download_scribd_document(**dl_kwargs)
        duration = time.time() - start_time

        if result["success"]:
            pdf_path = result["pdf_path"]
            title = result["title"]
            pages = result["pages"]
            file_size = os.path.getsize(pdf_path)

            db.mark_download_success(record_id, title, pages, file_size, pdf_path, duration)

            await status_msg.edit_text(
                f"✅ Tải xong!\n📄 {title}\n📃 {pages} trang • {format_size(file_size)} • {duration:.0f}s\n📤 Đang gửi..."
            )

            if file_size > 50 * 1024 * 1024:
                await status_msg.edit_text(
                    f"⚠️ File quá lớn ({format_size(file_size)}).\nTelegram giới hạn 50MB."
                )
            else:
                with open(pdf_path, 'rb') as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(pdf_path),
                        caption=f"📚 {title}\n📃 {pages} trang",
                    )
                await status_msg.delete()

            logger.info(f"✅ {doc_id}: {title} ({pages}p, {duration:.1f}s)")
        else:
            # 🤖 AI Error Diagnosis
            diagnosis = ai_helper.diagnose_download_error(result["error"], url, doc_id)
            db.mark_download_failed(record_id, result["error"], duration)

            diag_text = (
                f"❌ *Lỗi tải tài liệu*\n\n"
                f"🤖 *AI Chẩn đoán:* {diagnosis['diagnosis']}\n\n"
            )
            for s in diagnosis["suggestions"]:
                diag_text += f"  {s}\n"
            if diagnosis["can_retry"]:
                diag_text += f"\n💡 Gửi lại link để thử lại."

            await status_msg.edit_text(diag_text, parse_mode="Markdown")
            logger.error(f"❌ {doc_id}: {result['error']} | AI: {diagnosis['error_type']}")

    except Exception as e:
        duration = time.time() - start_time
        diagnosis = ai_helper.diagnose_download_error(str(e), url, doc_id)
        db.mark_download_failed(record_id, str(e), duration)
        await status_msg.edit_text(
            f"❌ Lỗi: {str(e)[:150]}\n\n"
            f"🤖 AI: {diagnosis['diagnosis']}"
        )
        logger.error(f"❌ Error: {e}", exc_info=True)

    finally:
        active_downloads.pop(user_id, None)


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN chưa được cấu hình!")
        print("   export TELEGRAM_BOT_TOKEN='your-token-here'")
        return

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("accounts", accounts_command))
    app.add_handler(CommandHandler("addaccount", addaccount_command))
    app.add_handler(CommandHandler("removeaccount", removeaccount_command))
    app.add_handler(CommandHandler("batch", batch_command))
    app.add_handler(CommandHandler("schedules", schedules_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Bot started!")
    print("🤖 Scribd Downloader Bot v2 is running!")
    print(f"   Download dir: {DOWNLOAD_DIR}")
    print(f"   Rate limit: {RATE_LIMIT_SECONDS}s")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
