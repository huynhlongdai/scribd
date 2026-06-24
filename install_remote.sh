#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Scribd Bot - Remote Install Script (chạy trực tiếp trên VPS)
# Copy & paste toàn bộ script này vào terminal VPS
# ═══════════════════════════════════════════════════════════

set -e
PROJECT_DIR="/opt/scribd-bot"

echo "📚 Installing Scribd Downloader Bot..."
echo "======================================="

# System deps
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv curl wget > /dev/null 2>&1
echo "  ✅ Done"

# Create project dir
echo "[2/6] Creating project..."
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

# Write all files inline
echo "[3/6] Writing application files..."

cat > requirements.txt << 'REQEOF'
python-telegram-bot==21.10
playwright==1.52.0
Pillow>=10.0.0
fastapi==0.115.0
uvicorn==0.32.0
REQEOF

cat > downloader.py << 'DLEOF'
"""Scribd Document Downloader - Core Engine"""
import asyncio, os, re, shutil, time, logging
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

def extract_doc_id(url):
    for p in [r'scribd\.com/doc(?:ument)?/(\d+)', r'scribd\.com/presentation/(\d+)', r'scribd\.com/embeds/(\d+)']:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def extract_doc_title(url):
    m = re.search(r'/(\d+)/([^/?#]+)', url)
    return m.group(2).replace('-', ' ') if m else "document"

async def download_scribd_document(url, output_dir="/tmp/scribd_downloads", cookies_json=None, quality=90, timeout=120):
    from playwright.async_api import async_playwright
    doc_id = extract_doc_id(url)
    if not doc_id: return {"success": False, "error": "Invalid Scribd URL"}
    title = extract_doc_title(url)
    embed_url = f"https://www.scribd.com/embeds/{doc_id}/content"
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(output_dir, f"temp_{doc_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            context = await browser.new_context(viewport={"width":1200,"height":900}, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            if cookies_json and os.path.exists(cookies_json):
                import json
                with open(cookies_json) as f: cookies = json.load(f)
                pw_cookies = []
                for c in cookies:
                    pc = {"name":c["name"],"value":c["value"],"domain":c.get("domain",".scribd.com"),"path":c.get("path","/")}
                    if c.get("secure"): pc["secure"] = True
                    if c.get("httpOnly"): pc["httpOnly"] = True
                    if c.get("sameSite") and c["sameSite"] in ("Strict","Lax","None"): pc["sameSite"] = c["sameSite"]
                    pw_cookies.append(pc)
                await context.add_cookies(pw_cookies)
            page = await context.new_page()
            logger.info(f"Loading: {embed_url}")
            try: await page.goto(embed_url, wait_until="networkidle", timeout=timeout*1000)
            except: await page.goto(embed_url, wait_until="domcontentloaded", timeout=timeout*1000)
            await asyncio.sleep(2)
            page_count = await page.evaluate('document.querySelectorAll(".outer_page").length')
            if page_count == 0:
                page_count = await page.evaluate('document.querySelectorAll("[class*=\\"page\\"]").length')
            if page_count == 0:
                await browser.close(); return {"success": False, "error": "No pages found. Document may be restricted."}
            logger.info(f"Found {page_count} pages")
            try:
                pt = await page.evaluate('document.querySelector("title")?.textContent?.trim()')
                if pt and pt != "Scribd": title = pt.replace(" | PDF","").strip()
            except: pass
            for i in range(1, page_count+1):
                await page.evaluate(f'document.getElementById("outer_page_{i}")?.scrollIntoView()')
                await asyncio.sleep(0.4)
            await asyncio.sleep(1)
            await page.evaluate('''() => { ['.toolbar_top','.toolbar_bottom','.osano-cm-window','.promo_div','.between_page_module','[class*="cookie"]','[class*="banner"]','[class*="overlay"]'].forEach(s => document.querySelectorAll(s).forEach(e => e.remove())); document.querySelectorAll('[style*="blur"]').forEach(e => { e.style.filter = 'none'; }); }''')
            images_paths = []
            for i in range(1, page_count+1):
                try:
                    el = page.locator(f"#outer_page_{i}")
                    if await el.count() > 0:
                        sp = os.path.join(temp_dir, f"page_{i:04d}.png")
                        await el.screenshot(path=sp)
                        images_paths.append(sp)
                        logger.info(f"Captured {i}/{page_count}")
                except Exception as e: logger.warning(f"Failed page {i}: {e}")
            await browser.close()
            if not images_paths: return {"success": False, "error": "Failed to capture pages."}
            safe_title = re.sub(r'[^\w\s\-]', '', title)[:100].strip() or "document"
            pdf_path = os.path.join(output_dir, f"{safe_title}_{doc_id}.pdf")
            images = []
            for ip in images_paths:
                img = Image.open(ip)
                if img.mode != 'RGB':
                    bg = Image.new('RGB', img.size, (255,255,255))
                    if img.mode == 'RGBA': bg.paste(img, mask=img.split()[-1])
                    else: bg.paste(img)
                    img = bg
                images.append(img)
            if images: images[0].save(pdf_path, 'PDF', save_all=True, append_images=images[1:], resolution=150)
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success":True,"pdf_path":pdf_path,"title":title,"pages":len(images),"doc_id":doc_id}
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2: print("Usage: python downloader.py <url>"); sys.exit(1)
    r = asyncio.run(download_scribd_document(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else "/tmp/scribd_downloads"))
    if r["success"]: print(f"✅ {r['pdf_path']} ({r['pages']} pages)")
    else: print(f"❌ {r['error']}"); sys.exit(1)
DLEOF

cat > bot.py << 'BOTEOF'
"""Scribd Telegram Bot"""
import asyncio, logging, os, re, time
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from downloader import download_scribd_document, extract_doc_id

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_SECONDS", "30"))
COOKIES_PATH = os.environ.get("COOKIES_PATH", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

user_last = defaultdict(float)
active = {}
stats = {"total":0,"ok":0,"fail":0,"start":time.time()}

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 *Scribd Downloader Bot*\n\nGửi link Scribd để tải PDF\\.\n\n*Lệnh:*\n/start \\- Hướng dẫn\n/status \\- Trạng thái\n/help \\- Trợ giúp\n\n*Ví dụ:*\n`https://www\\.scribd\\.com/document/123456/Title`", parse_mode="MarkdownV2")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 *Hướng dẫn*\n\n• Gửi link scribd\\.com/document/ID/title\n• Bot sẽ tải và gửi PDF\n• Giới hạn 1 yêu cầu mỗi 30s", parse_mode="MarkdownV2")

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    up = int(time.time()-stats["start"]); h,r = divmod(up,3600); m,s = divmod(r,60)
    await update.message.reply_text(f"📊 *Trạng thái*\n⏱ {h}h{m}m{s}s\n📥 Tổng: {stats['total']}\n✅ OK: {stats['ok']}\n❌ Fail: {stats['fail']}\n🔄 Active: {len(active)}", parse_mode="Markdown")

async def handle_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    uid = update.effective_user.id
    url_match = re.search(r'https?://[^\s]*scribd\.com/[^\s]+', text)
    if not url_match:
        if "scribd" in text.lower():
            await update.message.reply_text("⚠️ Link không hợp lệ. Gửi link đầy đủ:\n`https://www.scribd.com/document/123456/Title`", parse_mode="Markdown")
        return
    url = url_match.group(0)
    doc_id = extract_doc_id(url)
    if not doc_id: await update.message.reply_text("❌ Không tìm thấy document ID."); return
    now = time.time()
    if now - user_last[uid] < RATE_LIMIT:
        await update.message.reply_text(f"⏳ Đợi {int(RATE_LIMIT-(now-user_last[uid]))}s"); return
    if uid in active:
        await update.message.reply_text("⏳ Đang xử lý yêu cầu trước đó..."); return
    user_last[uid] = now; active[uid] = url; stats["total"] += 1
    msg = await update.message.reply_text(f"📥 Đang tải... (ID: `{doc_id}`)\n⏳ Vui lòng đợi...", parse_mode="Markdown")
    try:
        cp = COOKIES_PATH if COOKIES_PATH and os.path.exists(COOKIES_PATH) else None
        result = await download_scribd_document(url=url, output_dir=DOWNLOAD_DIR, cookies_json=cp)
        if result["success"]:
            pdf = result["pdf_path"]; title = result["title"]; pages = result["pages"]
            fs = os.path.getsize(pdf)
            if fs > 50*1024*1024:
                await msg.edit_text(f"⚠️ File quá lớn ({fs//(1024*1024)}MB)")
            else:
                await msg.edit_text(f"✅ {title} ({pages} trang)\n📤 Gửi file...")
                with open(pdf,'rb') as f:
                    await update.message.reply_document(document=f, filename=os.path.basename(pdf), caption=f"📚 {title}\n📃 {pages} trang")
                await msg.delete()
            try: os.remove(pdf)
            except: pass
            stats["ok"] += 1
            logger.info(f"OK: {doc_id} - {title} ({pages}p)")
        else:
            await msg.edit_text(f"❌ Lỗi: {result['error']}")
            stats["fail"] += 1
    except Exception as e:
        await msg.edit_text(f"❌ Lỗi: {str(e)[:200]}")
        stats["fail"] += 1
    finally:
        active.pop(uid, None)

def main():
    if not BOT_TOKEN: print("❌ Set TELEGRAM_BOT_TOKEN!"); return
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("🤖 Scribd Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__": main()
BOTEOF

cat > api_server.py << 'APIEOF'
"""Scribd Download API Server"""
import os, logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from downloader import download_scribd_document, extract_doc_id

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Scribd Downloader API", version="1.0.0")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/scribd_downloads")
COOKIES_PATH = os.environ.get("COOKIES_PATH", "")
active = {}

class DLReq(BaseModel):
    url: str; quality: int = 90

@app.get("/")
async def root(): return {"status":"ok","service":"Scribd Downloader API"}

@app.get("/health")
async def health(): return {"status":"healthy","active":len(active)}

@app.post("/download")
async def download(req: DLReq):
    doc_id = extract_doc_id(req.url)
    if not doc_id: raise HTTPException(400, "Invalid Scribd URL")
    if doc_id in active: return {"success":False,"message":"Already downloading"}
    active[doc_id] = True
    try:
        cp = COOKIES_PATH if COOKIES_PATH and os.path.exists(COOKIES_PATH) else None
        r = await download_scribd_document(url=req.url, output_dir=DOWNLOAD_DIR, cookies_json=cp, quality=req.quality)
        if r["success"]: return {"success":True,"doc_id":doc_id,"title":r["title"],"pages":r["pages"],"download_url":f"/file/{doc_id}"}
        else: return {"success":False,"error":r["error"]}
    finally: active.pop(doc_id, None)

@app.get("/file/{doc_id}")
async def get_file(doc_id: str):
    for f in os.listdir(DOWNLOAD_DIR):
        if f.endswith(f"_{doc_id}.pdf"): return FileResponse(os.path.join(DOWNLOAD_DIR,f), media_type="application/pdf", filename=f)
    raise HTTPException(404, "Not found")

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("API_PORT","8000")))
APIEOF

echo "  ✅ Files written"

# Python venv
echo "[4/6] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  ✅ Python deps OK"

# Playwright
echo "[5/6] Installing Playwright browser..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true
echo "  ✅ Chromium OK"

# Systemd services
echo "[6/6] Creating services..."

cat > /etc/systemd/system/scribd-bot.service << 'SVCEOF'
[Unit]
Description=Scribd Downloader Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/scribd-bot
EnvironmentFile=/opt/scribd-bot/.env
ExecStart=/opt/scribd-bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

cat > /etc/systemd/system/scribd-api.service << 'SVCEOF'
[Unit]
Description=Scribd Downloader API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/scribd-bot
EnvironmentFile=/opt/scribd-bot/.env
ExecStart=/opt/scribd-bot/venv/bin/python api_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload

# Create .env template
if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
TELEGRAM_BOT_TOKEN=THAY_TOKEN_O_DAY
DOWNLOAD_DIR=/tmp/scribd_downloads
COOKIES_PATH=
RATE_LIMIT_SECONDS=30
MAX_QUEUE_SIZE=10
API_PORT=8000
ENVEOF
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "✅ Cài đặt hoàn tất!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Bước tiếp theo:"
echo "  1. Thêm token: nano /opt/scribd-bot/.env"
echo "  2. Khởi động:  systemctl start scribd-bot && systemctl enable scribd-bot"
echo "  3. Xem logs:   journalctl -u scribd-bot -f"
echo ""
