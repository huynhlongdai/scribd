# 📚 Scribd Downloader

Hệ thống tải tài liệu Scribd đầy đủ tính năng: **Telegram Bot** + **Admin API** + **Public SEO Website**.

## ✨ Tính năng

### 🤖 Telegram Bot (`bot.py`)
- Gửi link Scribd → nhận file PDF
- Lệnh: `/start`, `/help`, `/history`, `/stats`, `/accounts`
- Quản lý tài khoản: `/addaccount`, `/removeaccount`
- Cache thông minh — không tải lại file đã có

### 🔧 Admin Web API (`web_server.py`) — Port 8000
- Dashboard quản trị: lịch sử tải, hàng đợi, thống kê
- Quản lý multi-account Scribd (thêm/xóa/bật/tắt/refresh)
- API endpoints: `/api/download`, `/api/history`, `/api/accounts`, ...
- Dark theme UI

### 🌐 Public SEO Website (`public_site.py`) — Port 80
- Giao diện public siêu nhẹ, tối ưu SEO
- Trang chủ với download box + trust badges + stats
- Blog/bài viết tích hợp (hướng dẫn, so sánh, kiến thức)
- Robots.txt, sitemap.xml, Open Graph, Schema.org
- FAQ section, responsive mobile
- Vị trí quảng cáo sẵn sàng gắn Google AdSense
- Kết nối API backend để tải tài liệu

### 🔄 Core Features
- **Multi-account rotation** — xoay vòng tài khoản Scribd
- **Download queue** — hàng đợi khi quá tải
- **Smart cache** — cache file đã tải, tránh tải lặp
- **SQLite database** — lưu lịch sử, thống kê, tài khoản

## 📁 Cấu trúc

```
├── bot.py              # Telegram Bot
├── web_server.py       # Admin API + Dashboard (port 8000)
├── public_site.py      # Public SEO Website (port 80)
├── downloader.py       # Core download engine
├── database.py         # SQLite database layer
├── account_manager.py  # Multi-account management
├── articles/           # Blog articles (JSON)
├── setup.sh            # Auto-setup for VPS
├── docker-compose.yml  # Docker deployment
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 🚀 Cài đặt nhanh

### VPS (Ubuntu/Debian)
```bash
git clone https://github.com/huynhlongdai/scribd.git /opt/scribd-bot
cd /opt/scribd-bot
chmod +x setup.sh && bash setup.sh

# Cấu hình
nano .env

# Khởi động
systemctl start scribd-bot && systemctl enable scribd-bot    # Telegram Bot
systemctl start scribd-web && systemctl enable scribd-web    # Admin API
systemctl start scribd-site && systemctl enable scribd-site  # Public Website
```

### Docker
```bash
git clone https://github.com/huynhlongdai/scribd.git
cd scribd
cp .env.example .env && nano .env
docker-compose up -d
```

## ⚙️ Cấu hình (.env)

```env
TELEGRAM_BOT_TOKEN=your_token_here
DOWNLOAD_DIR=/tmp/scribd_downloads
DB_PATH=/opt/scribd-bot/scribd_bot.db
WEB_PORT=8000
PUBLIC_PORT=80
SITE_NAME=ScribdGet
SITE_DOMAIN=scribdget.com
API_BACKEND=http://localhost:8000
MAX_CONCURRENT_DOWNLOADS=2
RATE_LIMIT_SECONDS=30
```

## 🌐 Truy cập

| Service | URL | Mô tả |
|---------|-----|--------|
| Public Website | `http://YOUR_IP` | Trang public cho người dùng |
| Admin Dashboard | `http://YOUR_IP:8000` | Quản trị (history, accounts, stats) |
| Telegram Bot | `@YourBot` | Tải qua Telegram |

## 📝 Blog / SEO

Website public tích hợp sẵn:
- **5 bài viết mẫu** (hướng dẫn, so sánh, kiến thức)
- **Robots.txt** + **Sitemap.xml** tự động
- **Schema.org** structured data
- **Open Graph** + **Twitter Cards**
- **Vị trí quảng cáo** sẵn sàng cho Google AdSense

Thêm bài viết: sửa file `articles/articles.json` hoặc thêm trực tiếp trong `public_site.py`.

## 📄 Loại tài liệu hỗ trợ

- ✅ Documents (PDF, Word, Text)
- ✅ Presentations (PowerPoint)
- ✅ Spreadsheets (Excel)
- ✅ Sheet Music
- ❌ Books/Ebooks (Everand)
- ❌ Magazines
