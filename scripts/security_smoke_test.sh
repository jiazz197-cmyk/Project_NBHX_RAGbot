#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_PREFIX="${API_PREFIX:-/api/v1}"
INTERNAL_API_KEY="${INTERNAL_API_KEY:-}"
RATE_LIMIT_TEST_COUNT="${RATE_LIMIT_TEST_COUNT:-30}"
RATE_LIMIT_EXPECT_429="${RATE_LIMIT_EXPECT_429:-0}"

TMP_BODY="$(mktemp)"
trap 'rm -f "$TMP_BODY"' EXIT

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; exit 1; }

expect_status() {
  local url="$1"
  local expected="$2"
  shift 2
  local code
  code="$(curl -sS -o "$TMP_BODY" -w "%{http_code}" "$@" "$url")"
  if [[ "$code" != "$expected" ]]; then
    echo "Expected HTTP $expected, got $code"
    echo "Response body:"
    cat "$TMP_BODY"
    fail "Status check failed for $url"
  fi
}

check_header_present() {
  local url="$1"
  local header_name="$2"
  if ! curl -sSI "$url" | tr -d '\r' | grep -qi "^${header_name}:"; then
    fail "Missing response header: ${header_name}"
  fi
}

echo "Running security smoke tests against ${BASE_URL}${API_PREFIX}"

# 1) Metrics endpoint protection
expect_status "${BASE_URL}${API_PREFIX}/metrics" "403"
pass "Metrics endpoint blocks anonymous access"

if [[ -n "$INTERNAL_API_KEY" ]]; then
  expect_status "${BASE_URL}${API_PREFIX}/metrics" "200" -H "X-API-KEY: ${INTERNAL_API_KEY}"
  pass "Metrics endpoint accepts valid API key"
else
  echo "[INFO] INTERNAL_API_KEY not set, skipped positive metrics auth check"
fi

# 2) Security headers
check_header_present "${BASE_URL}/" "X-Frame-Options"
check_header_present "${BASE_URL}/" "X-Content-Type-Options"
check_header_present "${BASE_URL}/" "Referrer-Policy"
pass "Baseline security headers are present"

# 3) Rate limit smoke (optional hard assertion)
hit_429=0
for ((i = 1; i <= RATE_LIMIT_TEST_COUNT; i++)); do
  code="$(curl -sS -o "$TMP_BODY" -w "%{http_code}" "${BASE_URL}${API_PREFIX}/health")"
  if [[ "$code" == "429" ]]; then
    hit_429=1
    break
  fi
done

if [[ "$RATE_LIMIT_EXPECT_429" == "1" && "$hit_429" == "0" ]]; then
  fail "Rate limit assertion failed: expected at least one 429"
fi

if [[ "$hit_429" == "1" ]]; then
  pass "Rate limiter produced HTTP 429 under burst traffic"
else
  echo "[INFO] No 429 observed in smoke loop (may be expected with current thresholds)"
fi

echo "All smoke checks completed."
