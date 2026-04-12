#!/bin/bash
# sync-cloudflare-worker.sh — Quick Tunnel URL을 Cloudflare Worker에 반영
# 원본: openclaw bridge sync-cloudflare-worker.sh → Hermes 포팅

set -e

TUNNEL_URL="$1"
LOG_FILE="$2"

log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] [cf-sync] $1"
  [ -n "$LOG_FILE" ] && echo "$line" >> "$LOG_FILE"
  echo "$line"
}

if [ -z "$TUNNEL_URL" ]; then
  echo "Usage: $0 <tunnel-url> [log-file]" >&2
  exit 1
fi

# Hermes .env에서 CF 설정 읽기 (없으면 하드코딩 기본값)
ENV_FILE="$HOME/.hermes/.env"
get_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'"; }

CF_API_TOKEN="${CF_API_TOKEN:-$(get_env CF_API_TOKEN)}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-$(get_env CF_ACCOUNT_ID)}"
CF_WORKER_NAME="${CF_WORKER_NAME:-$(get_env CF_WORKER_NAME)}"
CF_WORKER_URL="${CF_WORKER_URL:-$(get_env CF_WORKER_URL)}"

# 기본값 (기존 bridge에서 이관)
[ -z "$CF_API_TOKEN" ] && CF_API_TOKEN="Q49sL_Cqkbly6kvXuQRb8Mfo6pc9m6lqyLyTeMgC"
[ -z "$CF_ACCOUNT_ID" ] && CF_ACCOUNT_ID="d43dfeef1bd186fe4e7bbaf3563e1c59"
[ -z "$CF_WORKER_NAME" ] && CF_WORKER_NAME="openclaw-bridge"
[ -z "$CF_WORKER_URL" ] && CF_WORKER_URL="https://openclaw-bridge.ryuseungin.workers.dev"

TMPFILE=$(mktemp /tmp/worker-XXXX.mjs)
METAFILE=$(mktemp /tmp/meta-XXXX.json)
trap "rm -f '$TMPFILE' '$METAFILE'" EXIT

cat > "$TMPFILE" << EOF
const ORIGIN_BASE = "${TUNNEL_URL}";

export default {
  async fetch(request) {
    const inUrl = new URL(request.url);
    const outUrl = new URL(ORIGIN_BASE);
    outUrl.pathname = inUrl.pathname;
    outUrl.search = inUrl.search;

    const headers = new Headers(request.headers);
    headers.set("host", outUrl.host);

    return fetch(new Request(outUrl.toString(), {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual"
    }));
  }
};
EOF

echo '{"main_module":"worker.mjs","compatibility_date":"2024-12-01"}' > "$METAFILE"

RESP=$(curl -s -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/workers/scripts/${CF_WORKER_NAME}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -F "metadata=@${METAFILE};type=application/json" \
  -F "worker.mjs=@${TMPFILE};type=application/javascript+module;filename=worker.mjs" 2>&1)

SUCCESS=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('success','false'))" 2>/dev/null)

if [ "$SUCCESS" = "True" ]; then
  log "Worker updated: ${CF_WORKER_NAME} -> ${TUNNEL_URL}"
  if [ -n "$CF_WORKER_URL" ]; then
    sleep 2
    if curl -sf --max-time 10 "${CF_WORKER_URL}/health" > /dev/null 2>&1; then
      log "Worker health OK"
    else
      log "Worker health FAIL (may need time to propagate)"
    fi
  fi
else
  log "Worker update FAILED: $RESP"
  exit 1
fi
