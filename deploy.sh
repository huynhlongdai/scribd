#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Scribd Bot - One-command VPS Deploy Script
# Chạy trên máy local để deploy lên VPS
#
# Cách dùng: bash deploy.sh
# ═══════════════════════════════════════════════════════════

set -e

# ─── VPS Config ───
VPS_IP="104.207.75.43"
VPS_USER="root"
VPS_PASS="8rS4RXf5It4Bofb6U6"
VPS_PORT=22
PROJECT_DIR="/opt/scribd-bot"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}📚 Deploying Scribd Bot to VPS ${VPS_IP}...${NC}"

# Check sshpass
if ! command -v sshpass &>/dev/null; then
    echo -e "${YELLOW}Installing sshpass...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y sshpass
    elif command -v brew &>/dev/null; then
        brew install hudochenkov/sshpass/sshpass
    else
        echo -e "${RED}Please install sshpass first${NC}"
        exit 1
    fi
fi

SSH_CMD="sshpass -p '${VPS_PASS}' ssh -o StrictHostKeyChecking=no -p ${VPS_PORT} ${VPS_USER}@${VPS_IP}"
SCP_CMD="sshpass -p '${VPS_PASS}' scp -o StrictHostKeyChecking=no -P ${VPS_PORT}"

# ─── Step 1: Upload files ───
echo -e "\n${GREEN}[1/4] Uploading files to VPS...${NC}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

eval "${SSH_CMD} 'mkdir -p ${PROJECT_DIR}'"

for f in downloader.py bot.py api_server.py requirements.txt setup.sh .env.example Dockerfile docker-compose.yml README.md; do
    if [ -f "${SCRIPT_DIR}/${f}" ]; then
        eval "${SCP_CMD} '${SCRIPT_DIR}/${f}' ${VPS_USER}@${VPS_IP}:${PROJECT_DIR}/${f}"
        echo "  ✅ ${f}"
    fi
done

# Upload cookies if exists
if [ -f "${SCRIPT_DIR}/cookies.json" ]; then
    eval "${SCP_CMD} '${SCRIPT_DIR}/cookies.json' ${VPS_USER}@${VPS_IP}:${PROJECT_DIR}/cookies.json"
    echo "  ✅ cookies.json"
fi

# ─── Step 2: Run setup ───
echo -e "\n${GREEN}[2/4] Running setup on VPS...${NC}"
eval "${SSH_CMD} 'cd ${PROJECT_DIR} && chmod +x setup.sh && bash setup.sh'"

# ─── Step 3: Configure .env ───
echo -e "\n${GREEN}[3/4] Configuring environment...${NC}"
read -p "Paste your Telegram Bot Token (from @BotFather): " BOT_TOKEN

if [ -n "$BOT_TOKEN" ]; then
    eval "${SSH_CMD} \"cat > ${PROJECT_DIR}/.env << ENVEOF
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
DOWNLOAD_DIR=/tmp/scribd_downloads
COOKIES_PATH=${PROJECT_DIR}/cookies.json
RATE_LIMIT_SECONDS=30
MAX_QUEUE_SIZE=10
API_PORT=8000
ENVEOF\""
    echo "  ✅ .env configured"
else
    echo -e "  ${YELLOW}⚠️ No token provided. Edit .env manually on VPS${NC}"
fi

# ─── Step 4: Start services ───
echo -e "\n${GREEN}[4/4] Starting services...${NC}"
eval "${SSH_CMD} 'systemctl daemon-reload && systemctl restart scribd-bot && systemctl enable scribd-bot'"
echo "  ✅ Telegram Bot started"

eval "${SSH_CMD} 'systemctl restart scribd-api && systemctl enable scribd-api'" 2>/dev/null || true
echo "  ✅ API Server started"

# ─── Verify ───
echo -e "\n${GREEN}Checking service status...${NC}"
eval "${SSH_CMD} 'systemctl status scribd-bot --no-pager | head -10'"

echo ""
echo "═══════════════════════════════════════════════════"
echo -e "${GREEN}✅ Deploy hoàn tất!${NC}"
echo "═══════════════════════════════════════════════════"
echo ""
echo "📝 Kiểm tra:"
echo "  - Bot logs: ssh root@${VPS_IP} 'journalctl -u scribd-bot -f'"
echo "  - API: http://${VPS_IP}:8000"
echo "  - Gửi link Scribd cho bot trên Telegram để test"
echo ""
