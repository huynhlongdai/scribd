# 📚 Scribd Downloader Bot

Telegram bot + API server để tải tài liệu từ Scribd dưới dạng PDF.

## Tính năng

- 🤖 **Telegram Bot** — Gửi link Scribd → nhận file PDF
- 🌐 **API Server** — REST API để tải tài liệu
- 📄 **PDF Output** — Chuyển đổi tài liệu thành PDF chất lượng cao
- ⏱ **Rate Limiting** — Giới hạn tốc độ yêu cầu
- 🔄 **Queue System** — Xử lý nhiều yêu cầu cùng lúc

## Cách hoạt động

1. Bot nhận link Scribd từ người dùng
2. Chuyển URL sang dạng embed (`/embeds/{doc_id}/content`)
3. Dùng Playwright headless browser để render tất cả trang
4. Chụp screenshot từng trang → ghép thành file PDF
5. Gửi file PDF về cho người dùng qua Telegram

## Cài đặt nhanh (VPS Ubuntu/Debian)

```bash
# Clone/upload code lên VPS
# Chạy script setup tự động:
sudo bash setup.sh

# Chỉnh sửa token:
nano /opt/scribd-bot/.env

# Khởi động:
systemctl start scribd-bot
systemctl enable scribd-bot
```

## Cài đặt với Docker

```bash
# Tạo file .env
cp .env.example .env
nano .env  # Thêm TELEGRAM_BOT_TOKEN

# Chạy
docker-compose up -d
```

## Cấu hình

| Biến | Mô tả | Mặc định |
|------|--------|----------|
| `TELEGRAM_BOT_TOKEN` | Token từ @BotFather | *Bắt buộc* |
| `ADMIN_IDS` | Telegram user IDs admin | Trống |
| `DOWNLOAD_DIR` | Thư mục lưu PDF | `/tmp/scribd_downloads` |
| `COOKIES_PATH` | File cookies.json cho auth | Trống |
| `RATE_LIMIT_SECONDS` | Giới hạn giữa các yêu cầu | `30` |
| `API_PORT` | Port API server | `8000` |

## Sử dụng Telegram Bot

1. Tạo bot mới với [@BotFather](https://t.me/BotFather): `/newbot`
2. Copy token → thêm vào `.env`
3. Gửi link Scribd cho bot → nhận PDF

### Lệnh bot
- `/start` — Hướng dẫn sử dụng
- `/help` — Trợ giúp
- `/status` — Trạng thái hàng đợi

## API Server

```bash
# Khởi động API
python api_server.py

# Hoặc với systemd
systemctl start scribd-api
```

### Endpoints

```
GET  /           — Health check
GET  /health     — Trạng thái
POST /download   — Tải tài liệu
GET  /file/{id}  — Lấy file PDF đã tải
```

### Ví dụ

```bash
curl -X POST http://localhost:8000/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.scribd.com/document/123456/Title"}'
```

## CLI Usage

```bash
# Tải trực tiếp từ command line
python downloader.py "https://www.scribd.com/document/123456/Title"
```

## Cấu trúc project

```
scribd-bot/
├── bot.py              # Telegram bot
├── api_server.py       # FastAPI server
├── downloader.py       # Core download engine
├── cookies.json        # Scribd cookies (optional)
├── requirements.txt    # Python dependencies
├── setup.sh            # Auto-setup script
├── Dockerfile          # Docker image
├── docker-compose.yml  # Docker compose
├── .env.example        # Template cấu hình
└── README.md           # Tài liệu
```
