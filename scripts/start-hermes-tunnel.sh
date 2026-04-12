#!/bin/bash
# Hermes API Cloudflare Quick Tunnel
# - localhost:8642를 trycloudflare.com으로 노출
# - 터널 URL을 CF Worker(openclaw-bridge)의 ORIGIN_BASE로 자동 업데이트

set -euo pipefail

HERMES_PORT="${HERMES_PORT:-8642}"
CF_API_TOKEN="${CF_API_TOKEN:-Q49sL_Cqkbly6kvXuQRb8Mfo6pc9m6lqyLyTeMgC}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-d43dfeef1bd186fe4e7bbaf3563e1c59}"
CF_WORKER_NAME="${CF_WORKER_NAME:-openclaw-bridge}"
LOG_FILE="$HOME/.hermes/logs/tunnel.log"
URL_FILE="$HOME/.hermes/tunnel-url.txt"

mkdir -p "$(dirname "$LOG_FILE")"

echo "[$(date)] Starting Hermes tunnel on port $HERMES_PORT..." >> "$LOG_FILE"

# Start cloudflared in background, capture URL from stderr
cloudflared tunnel --url "http://127.0.0.1:${HERMES_PORT}" --no-autoupdate 2>&1 | while IFS= read -r line; do
  echo "$line" >> "$LOG_FILE"

  # Capture the trycloudflare.com URL
  if echo "$line" | grep -qo 'https://[a-z0-9-]*\.trycloudflare\.com'; then
    TUNNEL_URL=$(echo "$line" | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com')
    echo "$TUNNEL_URL" > "$URL_FILE"
    echo "[$(date)] Tunnel URL: $TUNNEL_URL" >> "$LOG_FILE"

    # Update CF Worker's ORIGIN_BASE secret
    echo "[$(date)] Updating CF Worker ORIGIN_BASE..." >> "$LOG_FILE"
    curl -sS -X PUT \
      "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/workers/scripts/${CF_WORKER_NAME}/secrets" \
      -H "Authorization: Bearer ${CF_API_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"ORIGIN_BASE\",\"text\":\"${TUNNEL_URL}\",\"type\":\"secret_text\"}" \
      >> "$LOG_FILE" 2>&1

    echo "[$(date)] CF Worker updated with new tunnel URL" >> "$LOG_FILE"
  fi
done
