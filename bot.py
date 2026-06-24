"""
Scribd Telegram Bot
Send a Scribd link → get a PDF back.

Features:
- Download Scribd documents as PDF
- Queue system for multiple requests
- Rate limiting per user
- Admin commands for stats
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
download_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
user_last_request: dict[int, float] = defaultdict(float)
stats = {
    "total_downloads": 0,
    "successful": 0,
    "failed": 0,
    "start_time": time.time(),
}
active_downloads: dict[int, str] = {}  # user_id -> doc_url


def is_scribd_url(text: str) -> bool:
    """Check if text contains a Scribd URL."""
    return bool(re.search(r'scribd\.com/(doc(ument)?|presentation|embeds)/\d+', text))


def extract_url(text: str) -> str | None:
    """Extract Scribd URL from message text."""
    match = re.search(r'https?://[^\s]*scribd\.com/[^\s]+', text)
    return match.group(0) if match else None


# ═══════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome = (
        "📚 *Scribd Downloader Bot*\n\n"
        "Gửi link Scribd để tải tài liệu dưới dạng PDF\\.\n\n"
        "*Cách sử dụng:*\n"
        "1\\. Copy link tài liệu từ Scribd\n"
        "2\\. Gửi link vào chat\n"
        "3\\. Đợi bot tải và gửi PDF cho bạn\n\n"
        "*Ví dụ:*\n"
        "`https://www\\.scribd\\.com/document/123456/Ten\\-Tai\\-Lieu`\n\n"
        "*Lệnh:*\n"
        "/start \\- Hiển thị hướng dẫn\n"
        "/status \\- Xem trạng thái hàng đợi\n"
        "/help \\- Trợ giúp"
    )
    await update.message.reply_text(welcome, parse_mode="MarkdownV2")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📖 *Hướng dẫn sử dụng*\n\n"
        "*Các định dạng link được hỗ trợ:*\n"
        "• `scribd\\.com/document/ID/title`\n"
        "• `scribd\\.com/doc/ID/title`\n"
        "• `scribd\\.com/presentation/ID/title`\n\n"
        "*Lưu ý:*\n"
        "• Bot tải dưới dạng PDF hình ảnh \\(image\\-based PDF\\)\n"
        "• Thời gian tải phụ thuộc vào số trang\n"
        "• Giới hạn 1 yêu cầu mỗi 30 giây"
    )
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    uptime = int(time.time() - stats["start_time"])
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    status = (
        f"📊 *Trạng thái Bot*\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"📥 Tổng yêu cầu: {stats['total_downloads']}\n"
        f"✅ Thành công: {stats['successful']}\n"
        f"❌ Thất bại: {stats['failed']}\n"
        f"🔄 Đang xử lý: {len(active_downloads)}\n"
        f"📋 Hàng đợi: {download_queue.qsize()}/{MAX_QUEUE_SIZE}"
    )
    await update.message.reply_text(status, parse_mode="Markdown")


# ═══════════════════════════════════════════
# Message Handlers
# ═══════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages with Scribd URLs."""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Extract URL
    url = extract_url(text)
    if not url:
        if "scribd" in text.lower():
            await update.message.reply_text(
                "⚠️ Không tìm thấy link Scribd hợp lệ.\n"
                "Vui lòng gửi link đầy đủ, ví dụ:\n"
                "`https://www.scribd.com/document/123456/Title`",
                parse_mode="Markdown"
            )
        return
    
    # Validate URL
    doc_id = extract_doc_id(url)
    if not doc_id:
        await update.message.reply_text("❌ Link Scribd không hợp lệ. Không tìm thấy document ID.")
        return
    
    # Rate limiting
    now = time.time()
    time_since_last = now - user_last_request[user_id]
    if time_since_last < RATE_LIMIT_SECONDS:
        wait = int(RATE_LIMIT_SECONDS - time_since_last)
        await update.message.reply_text(f"⏳ Vui lòng đợi {wait} giây trước khi gửi yêu cầu mới.")
        return
    
    # Check if user already has active download
    if user_id in active_downloads:
        await update.message.reply_text("⏳ Bạn đã có 1 yêu cầu đang xử lý. Vui lòng đợi hoàn tất.")
        return
    
    user_last_request[user_id] = now
    
    # Process download
    status_msg = await update.message.reply_text(
        f"📥 Đang tải tài liệu...\n"
        f"📄 Document ID: `{doc_id}`\n"
        f"⏳ Vui lòng đợi...",
        parse_mode="Markdown"
    )
    
    active_downloads[user_id] = url
    stats["total_downloads"] += 1
    
    try:
        # Download
        cookies_path = COOKIES_PATH if COOKIES_PATH and os.path.exists(COOKIES_PATH) else None
        result = await download_scribd_document(
            url=url,
            output_dir=DOWNLOAD_DIR,
            cookies_json=cookies_path,
        )
        
        if result["success"]:
            pdf_path = result["pdf_path"]
            title = result["title"]
            pages = result["pages"]
            
            # Update status
            await status_msg.edit_text(
                f"✅ Tải thành công!\n"
                f"📄 {title}\n"
                f"📃 {pages} trang\n"
                f"📤 Đang gửi file..."
            )
            
            # Send PDF file
            file_size = os.path.getsize(pdf_path)
            if file_size > 50 * 1024 * 1024:  # 50MB Telegram limit
                await status_msg.edit_text(
                    f"⚠️ File quá lớn ({file_size // (1024*1024)}MB).\n"
                    f"Telegram giới hạn 50MB."
                )
            else:
                with open(pdf_path, 'rb') as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(pdf_path),
                        caption=f"📚 {title}\n📃 {pages} trang",
                    )
                await status_msg.delete()
            
            # Cleanup PDF
            try:
                os.remove(pdf_path)
            except Exception:
                pass
            
            stats["successful"] += 1
            logger.info(f"Download successful: {doc_id} - {title} ({pages} pages)")
        
        else:
            await status_msg.edit_text(f"❌ Lỗi: {result['error']}")
            stats["failed"] += 1
            logger.error(f"Download failed: {doc_id} - {result['error']}")
    
    except Exception as e:
        await status_msg.edit_text(f"❌ Lỗi không xác định: {str(e)[:200]}")
        stats["failed"] += 1
        logger.error(f"Unexpected error: {e}", exc_info=True)
    
    finally:
        active_downloads.pop(user_id, None)


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN environment variable is required!")
        print("   1. Talk to @BotFather on Telegram")
        print("   2. Create a new bot with /newbot")
        print("   3. Copy the token")
        print("   4. Set: export TELEGRAM_BOT_TOKEN='your-token-here'")
        return
    
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    # Build application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🤖 Bot started! Waiting for messages...")
    print("🤖 Scribd Downloader Bot is running!")
    print(f"   Download dir: {DOWNLOAD_DIR}")
    print(f"   Rate limit: {RATE_LIMIT_SECONDS}s")
    print(f"   Max queue: {MAX_QUEUE_SIZE}")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
