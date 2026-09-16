#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -f "${ROOT}/nginx/nginx.conf" ]] && [[ -z "${SKIP_NGINX_RENDER:-}" ]]; then
  "${ROOT}/scripts/render_nginx_conf.sh"
fi

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
# Optional: set SMOKE_AUTH_TOKEN to also assert the reserved-orchestrator 501 payload.
SMOKE_AUTH_TOKEN="${SMOKE_AUTH_TOKEN:-}"

CHAT_RESERVED_CODE="CHAT_ORCHESTRATOR_NOT_CONFIGURED"

MAX_TIME="${MAX_TIME:-3}"

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; exit 1; }

http_code() {
  curl -sS -o /dev/null -w "%{http_code}" --max-time "$MAX_TIME" "$1" || echo "000"
}


expect_any_of() {
  local url="$1"; shift
  local got
  got="$(http_code "$url")"
  for expected in "$@"; do
    if [[ "$got" == "$expected" ]]; then
      pass "GET $url -> $got"
      return 0
    fi
  done
  fail "GET $url -> $got (expected: $*)"
}


expect_any_of "${BASE_URL}/" 200 301 302 304
expect_any_of "${BASE_URL}/api/v1/docs" 200 301 302
expect_any_of "${BASE_URL}/api/v1/docs/" 200 301 302 307 308

# Chat paths must reach FastAPI and require login, not 404/502.
expect_any_of "${BASE_URL}/api/v1/conversations" 401
expect_any_of "${BASE_URL}/api/v1/messages?conversation_id=smoke-conversation" 401
chat_post_code="$(curl -sS -o /dev/null -w "%{http_code}" --max-time "$MAX_TIME" \
  -X POST -H 'Content-Type: application/json' \
  --data '{"query":"smoke"}' \
  "${BASE_URL}/api/v1/chat-messages" || echo "000")"
if [[ "$chat_post_code" != "401" ]]; then
  fail "POST ${BASE_URL}/api/v1/chat-messages (no JWT) -> ${chat_post_code} (expected 401)"
fi
pass "POST /api/v1/chat-messages requires JWT -> 401"

if [[ -n "${SMOKE_AUTH_TOKEN}" ]]; then
  code="$(curl -sS -o /tmp/nbhx-chat-smoke.json -w "%{http_code}" \
    --max-time "$MAX_TIME" \
    -H "Authorization: Bearer ${SMOKE_AUTH_TOKEN}" \
    "${BASE_URL}/api/v1/conversations" || echo "000")"
  if [[ "$code" != "501" ]]; then
    fail "GET ${BASE_URL}/api/v1/conversations (Bearer) -> ${code} (expected 501)"
  fi
  if ! grep -q "${CHAT_RESERVED_CODE}" /tmp/nbhx-chat-smoke.json; then
    fail "reserved chat error payload missing ${CHAT_RESERVED_CODE}"
  fi
  pass "reserved chat error: GET /api/v1/conversations -> 501 ${CHAT_RESERVED_CODE}"
else
  pass "SMOKE_AUTH_TOKEN 未设置，跳过 501 预留错误断言（仍已验证聊天接口需要登录）"
fi

pass "nginx smoke ok"

