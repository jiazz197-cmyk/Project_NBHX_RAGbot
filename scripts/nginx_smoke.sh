#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -f "${ROOT}/nginx/nginx.conf" ]] && [[ -z "${SKIP_NGINX_RENDER:-}" ]]; then
  "${ROOT}/scripts/render_nginx_conf.sh"
fi

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
DIFY_PROBE_PATH="${DIFY_PROBE_PATH:-/api/v1/smoke}"

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

expect_not_any_of() {
  local url="$1"; shift
  local got
  got="$(http_code "$url")"
  for forbidden in "$@"; do
    if [[ "$got" == "$forbidden" ]]; then
      fail "GET $url -> $got (forbidden)"
    fi
  done
  pass "GET $url -> $got"
}

expect_any_of "${BASE_URL}/" 200 301 302 304
expect_any_of "${BASE_URL}/api/v1/docs" 200 301 302
expect_any_of "${BASE_URL}/api/v1/docs/" 200 301 302 307 308
expect_not_any_of "${BASE_URL}${DIFY_PROBE_PATH}" 000 502 504

pass "nginx smoke ok"

