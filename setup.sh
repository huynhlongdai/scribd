#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Scribd Downloader Bot - Setup Script
# Cài đặt tự động trên Ubuntu/Debian VPS
# ═══════════════════════════════════════════════════════════

set -e

echo "📚 Scribd Downloader Bot - Setup"
echo "================================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Vui lòng chạy với quyền root: sudo bash setup.sh${NC}"
    exit 1
fi

# ─── System Dependencies ───
echo -e "\n${GREEN}[1/5] Cài đặt system dependencies...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl wget > /dev/null 2>&1
echo "  ✅ System dependencies OK"

# ─── Project Directory ───
echo -e "\n${GREEN}[2/5] Tạo project directory...${NC}"
PROJECT_DIR="/opt/scribd-bot"
mkdir -p "$PROJECT_DIR"
cp -r "$(dirname "$0")"/* "$PROJECT_DIR/" 2>/dev/null || true
cd "$PROJECT_DIR"
echo "  ✅ Project dir: $PROJECT_DIR"

# ─── Python Virtual Environment ───
echo -e "\n${GREEN}[3/5] Tạo Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  ✅ Python dependencies OK"

# ─── Playwright Browser ───
echo -e "\n${GREEN}[4/5] Cài đặt Playwright browser...${NC}"
playwright install chromium
playwright install-deps chromium 2>/dev/null || true
echo "  ✅ Playwright Chromium OK"

# ─── Environment Configuration ───
echo -e "\n${GREEN}[5/5] Cấu hình environment...${NC}"
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo -e "  ${YELLOW}⚠️  Vui lòng chỉnh sửa file .env:${NC}"
    echo "     nano $PROJECT_DIR/.env"
    echo "     → Thêm TELEGRAM_BOT_TOKEN"
else
    echo "  ✅ .env file already exists"
fi

# ─── Create systemd service ───
cat > /etc/systemd/system/scribd-bot.service << 'EOF'
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
EOF

# API server service (optional)
cat > /etc/systemd/system/scribd-api.service << 'EOF'
[Unit]
Description=Scribd Downloader API Server
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
EOF

systemctl daemon-reload

echo ""
echo "═══════════════════════════════════════════════════"
echo -e "${GREEN}✅ Cài đặt hoàn tất!${NC}"
echo "═══════════════════════════════════════════════════"
echo ""
echo "📝 Các bước tiếp theo:"
echo ""
echo "  1. Chỉnh sửa token trong .env:"
echo "     nano /opt/scribd-bot/.env"
echo ""
echo "  2. Khởi động Telegram Bot:"
echo "     systemctl start scribd-bot"
echo "     systemctl enable scribd-bot"
echo ""
echo "  3. (Tuỳ chọn) Khởi động API Server:"
echo "     systemctl start scribd-api"
echo "     systemctl enable scribd-api"
echo ""
echo "  4. Xem logs:"
echo "     journalctl -u scribd-bot -f"
echo "     journalctl -u scribd-api -f"
echo ""
echo "  5. Test bot: Gửi link Scribd cho bot trên Telegram"
echo ""
echo "═══════════════════════════════════════════════════"
