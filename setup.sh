#!/bin/bash
# ═══════════════════════════════════════════
# Scribd Downloader Bot - Auto Setup Script
# For Ubuntu/Debian VPS
# ═══════════════════════════════════════════

set -e

APP_DIR="/opt/scribd-bot"
VENV_DIR="$APP_DIR/venv"

echo "═══════════════════════════════════════════"
echo "  📚 Scribd Downloader Bot - Setup"
echo "═══════════════════════════════════════════"

# 1. System packages
echo "[1/5] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl wget

# 2. Python virtual environment
echo "[2/5] Setting up Python environment..."
cd "$APP_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 3. Playwright + Chromium
echo "[3/5] Installing Playwright + Chromium (may take a few minutes)..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || apt-get install -y -qq \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 || true

# 4. Create download directory
echo "[4/5] Creating directories..."
mkdir -p /tmp/scribd_downloads

# 5. Create .env if not exists
if [ ! -f "$APP_DIR/.env" ]; then
    echo "[5/5] Creating .env template..."
    cat > "$APP_DIR/.env" << 'ENVEOF'
TELEGRAM_BOT_TOKEN=YOUR_TOKEN_HERE
DOWNLOAD_DIR=/tmp/scribd_downloads
DB_PATH=/opt/scribd-bot/scribd_bot.db
COOKIES_PATH=
RATE_LIMIT_SECONDS=30
MAX_QUEUE_SIZE=10
WEB_PORT=8000
MAX_CONCURRENT_DOWNLOADS=2
ENVEOF
    echo "    ⚠️  Edit .env to add your Telegram bot token!"
else
    echo "[5/5] .env already exists, skipping..."
fi

# Create systemd services
echo "Creating systemd services..."

# Telegram Bot Service
cat > /etc/systemd/system/scribd-bot.service << SVCEOF
[Unit]
Description=Scribd Downloader Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

# Web Server Service
cat > /etc/systemd/system/scribd-web.service << SVCEOF
[Unit]
Description=Scribd Downloader Web Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/python web_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload

echo ""
echo "═══════════════════════════════════════════"
echo "  ✅ Setup complete!"
echo "═══════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo "  1. Edit .env:  nano $APP_DIR/.env"
echo "  2. Start bot:  systemctl start scribd-bot && systemctl enable scribd-bot"
echo "  3. Start web:  systemctl start scribd-web && systemctl enable scribd-web"
echo ""
echo "  Web UI: http://YOUR_VPS_IP:8000"
echo "  Logs:   journalctl -u scribd-bot -f"
echo "          journalctl -u scribd-web -f"
echo ""
