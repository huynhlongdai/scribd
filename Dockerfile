FROM python:3.12-slim

# Install system deps
RUN apt-get update && apt-get install -y \
    wget curl gnupg2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright + Chromium
RUN playwright install chromium && playwright install-deps chromium

# Copy app code
COPY . .

# Create download directory
RUN mkdir -p /tmp/scribd_downloads

# Default: run Telegram bot
CMD ["python", "bot.py"]
