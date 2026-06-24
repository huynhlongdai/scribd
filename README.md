# 📚 Scribd Downloader Bot

Hệ thống tải tài liệu Scribd dưới dạng PDF — hỗ trợ Telegram Bot + Web UI.

## ✨ Tính năng

### Telegram Bot
- 📥 Gửi link Scribd → nhận PDF
- 📋 `/history` — Lịch sử tải
- 📊 `/stats` — Thống kê
- 🔄 `/status` — Trạng thái hàng đợi
- ⚡ Cache thông minh — tải lại tài liệu cũ rất nhanh
- ⏳ Rate limiting — chống spam

### Web UI
- 🌐 Giao diện web đẹp, dark theme
- 📥 Dán link → tải PDF trực tiếp
- 📋 Lịch sử tải + tìm kiếm
- 🔄 Hàng đợi real-time
- 📊 Thống kê chi tiết

### Core Engine
- 🔧 Dùng embed URL + Playwright headless render
- 📃 Chụp từng trang → ghép PDF
- 💾 SQLite database cho lịch sử + queue
- 🔄 Hỗ trợ tải đồng thời

## 📋 Định dạng hỗ trợ

| Loại | Hỗ trợ |
|------|--------|
| Documents/PDF | ✅ |
| Presentations (PPT) | ✅ |
| Word docs | ✅ |
| Spreadsheets | ✅ |
| Sheet music | ✅ |
| Books (Everand) | ❌ |
| Magazines | ❌ |

## 🚀 Cài đặt

### Trên VPS (Ubuntu/Debian):
```bash
git clone https://github.com/huynhlongdai/scribd.git /opt/scribd-bot
cd /opt/scribd-bot
chmod +x setup.sh && bash setup.sh
```

### Cấu hình:
```bash
nano /opt/scribd-bot/.env
# Thêm TELEGRAM_BOT_TOKEN=your_token
```

### Khởi động:
```bash
# Telegram Bot
systemctl start scribd-bot && systemctl enable scribd-bot

# Web UI (port 8000)
systemctl start scribd-web && systemctl enable scribd-web
```

### Truy cập:
- **Web UI:** `http://YOUR_VPS_IP:8000`
- **Telegram:** Tìm bot và gửi link Scribd

## 📁 Cấu trúc

```
├── downloader.py     # Core download engine
├── bot.py            # Telegram bot (v2 + history)
├── web_server.py     # FastAPI web server + UI
├── database.py       # SQLite database layer
├── setup.sh          # Auto-setup script
├── requirements.txt  # Python dependencies
├── .env.example      # Config template
├── Dockerfile        # Docker build
└── docker-compose.yml
```

## 🐳 Docker

```bash
cp .env.example .env
# Edit .env
docker-compose up -d
```

## 📊 API Endpoints

| Method | URL | Mô tả |
|--------|-----|--------|
| GET | `/` | Web UI |
| POST | `/api/download` | Bắt đầu tải |
| GET | `/api/status/{doc_id}` | Kiểm tra trạng thái |
| GET | `/api/file/{doc_id}` | Tải PDF |
| GET | `/api/history` | Lịch sử tải |
| GET | `/api/search?q=...` | Tìm kiếm |
| GET | `/api/queue` | Hàng đợi |
| GET | `/api/stats` | Thống kê |
