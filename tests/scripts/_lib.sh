#!/usr/bin/env bash
# Shared helper library for the acceptance/regression bash scripts.
#
# Sourced by full_acceptance_regression.sh and fix_full_regression.sh after
# they have set PROJECT_ROOT and initialized the PASS_COUNT/WARN_COUNT/
# FAIL_COUNT counters. Only genuinely-identical low-level helpers live here;
# init/login_superuser/test_*/cleanup/summary differ per script and stay there.

# Pretty section header.
section() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "[PASS] $1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  echo "[WARN] $1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "[FAIL] $1"
}

skip() {
  SKIP_COUNT=${SKIP_COUNT:-0}
  SKIP_COUNT=$((SKIP_COUNT + 1))
  echo "[SKIP] $1"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    fail "缺少命令: $1"
    exit 1
  }
}

http_code() {
  local headers="$1"
  grep -i '^HTTP/' "$headers" | tail -n 1 | awk '{print $2}'
}

json_print() {
  jq . 2>/dev/null || cat
}

redact_token_json() {
  jq 'if .access_token then .access_token="<redacted>" else . end' 2>/dev/null || cat
}

login_user() {
  local username="$1"
  local password="$2"

  curl -sS -X POST "$BASE/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$username\",\"password\":\"$password\"}"
}

make_png() {
  local out="$1"
  python - "$out" <<'PY'
from pathlib import Path
import base64
import sys

out = Path(sys.argv[1])
png_b64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
out.write_bytes(base64.b64decode(png_b64))
print(out)
PY
}
