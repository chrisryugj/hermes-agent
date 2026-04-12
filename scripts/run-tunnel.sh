#!/bin/bash
# run-tunnel.sh — Hermes API용 Cloudflare Quick Tunnel 자동 재시작 + URL 감지/동기화
# 원본: openclaw bridge run-tunnel.sh → Hermes 포팅

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/.hermes/logs"
LOG_FILE="$LOG_DIR/cloudflared.log"
URL_FILE="$LOG_DIR/quick-tunnel-url.txt"
HERMES_PORT="${HERMES_PORT:-8642}"

mkdir -p "$LOG_DIR"

# 뮤텍스: 중복 실행 방지
LOCK_FILE="$LOG_DIR/.tunnel.lock"
if command -v flock >/dev/null 2>&1; then
  exec 201>"$LOCK_FILE"
  if ! flock -n 201; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] tunnel runner already active; exiting" >> "$LOG_FILE"
    exit 0
  fi
else
  if ! shlock -f "$LOCK_FILE" -p $$; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] tunnel runner already active; exiting" >> "$LOG_FILE"
    exit 0
  fi
  trap "rm -f '$LOCK_FILE'" EXIT
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

while true; do
  rm -f "$URL_FILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] preparing fresh cloudflared session log" > "$LOG_FILE"

  # 잔여 cloudflared 프로세스 정리
  pkill -f "cloudflared.*tunnel.*--url.*http://127.0.0.1:${HERMES_PORT}" 2>/dev/null

  log "starting cloudflared quick tunnel → http://127.0.0.1:${HERMES_PORT}"

  cloudflared tunnel --url "http://127.0.0.1:${HERMES_PORT}" --no-autoupdate >> "$LOG_FILE" 2>&1 &
  CF_PID=$!

  LAST_URL=""
  HEALTH_COUNTER=0
  FAIL_STREAK=0          # e2e 연속 실패 횟수
  MAX_FAIL_STREAK=2      # 2회 실패 → cloudflared 자체 재시작 (origin stale 대응)
  while kill -0 "$CF_PID" 2>/dev/null; do
    if [ -f "$LOG_FILE" ]; then
      URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | tail -1)
      if [ -n "$URL" ] && [ "$URL" != "$LAST_URL" ]; then
        LAST_URL="$URL"
        echo "$URL" > "$URL_FILE"
        log "discovered quick URL: $URL"

        # Cloudflare Worker 동기화
        SYNC_SCRIPT="$SCRIPT_DIR/sync-cloudflare-worker.sh"
        if [ -x "$SYNC_SCRIPT" ]; then
          "$SYNC_SCRIPT" "$URL" "$LOG_FILE" 2>/dev/null || log "sync script failed"
        fi
        HEALTH_COUNTER=0
        FAIL_STREAK=0
      fi
    fi

    # 주기적 e2e 헬스체크 (30초마다 = 15 * 2초 sleep)
    HEALTH_COUNTER=$((HEALTH_COUNTER + 1))
    if [ "$HEALTH_COUNTER" -ge 15 ] && [ -n "$LAST_URL" ]; then
      HEALTH_COUNTER=0
      HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
        "https://openclaw-bridge.ryuseungin.workers.dev/health" \
        -H "Authorization: Bearer lexdiff-hermes-local" 2>/dev/null || echo "000")
      if [ "$HTTP_CODE" != "200" ]; then
        FAIL_STREAK=$((FAIL_STREAK + 1))
        log "e2e health failed (HTTP $HTTP_CODE) streak=$FAIL_STREAK/$MAX_FAIL_STREAK"
        if [ "$FAIL_STREAK" -ge "$MAX_FAIL_STREAK" ]; then
          # 근본 대응: cloudflared 자체를 재시작 (새 URL 발급)
          log "RESTART cloudflared — origin stale suspected (streak=$FAIL_STREAK)"
          kill "$CF_PID" 2>/dev/null
          sleep 1
          kill -9 "$CF_PID" 2>/dev/null
          break  # inner while 탈출 → outer while가 새 cloudflared 시작
        else
          # 1회 실패는 우선 Worker resync만 시도 (일시적 propagation delay 대응)
          SYNC_SCRIPT="$SCRIPT_DIR/sync-cloudflare-worker.sh"
          if [ -x "$SYNC_SCRIPT" ]; then
            "$SYNC_SCRIPT" "$LAST_URL" "$LOG_FILE" 2>/dev/null || log "resync failed"
          fi
        fi
      else
        if [ "$FAIL_STREAK" -gt 0 ]; then
          log "e2e health recovered (was streak=$FAIL_STREAK)"
        fi
        FAIL_STREAK=0
      fi
    fi

    sleep 2
  done

  wait "$CF_PID" 2>/dev/null
  CODE=$?
  log "cloudflared exited (code=$CODE). restart in 5s"
  sleep 5
done
