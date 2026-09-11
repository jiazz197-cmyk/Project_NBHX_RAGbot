#!/usr/bin/env bash
# Render nginx/nginx.conf from nginx/nginx.conf.template using DIFY_APP_API_KEY.
# Uses envsubst with an explicit variable list so nginx $variables are preserved.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="${ROOT}/nginx/nginx.conf.template"
OUT="${ROOT}/nginx/nginx.conf"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Missing template: $TEMPLATE" >&2
  exit 1
fi

if [[ -z "${DIFY_APP_API_KEY:-}" ]]; then
  CHAT_ENV="${ROOT}/frontend/apps/chat/.env"
  if [[ -f "$CHAT_ENV" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck disable=SC1091
    source "$CHAT_ENV"
    set +a
  fi
  DIFY_APP_API_KEY="${DIFY_APP_API_KEY:-${CHAT_PROXY_API_KEY:-${CHAT_API_KEY:-${VITE_CHAT_API_KEY:-}}}}"
fi

if [[ -z "${DIFY_APP_API_KEY:-}" ]]; then
  echo "Set DIFY_APP_API_KEY or CHAT_PROXY_API_KEY (e.g. in frontend/apps/chat/.env)." >&2
  exit 1
fi

export DIFY_APP_API_KEY
if ! command -v envsubst >/dev/null 2>&1; then
  echo "envsubst is required (gettext package: apt install gettext-base)." >&2
  exit 1
fi

envsubst '${DIFY_APP_API_KEY}' < "$TEMPLATE" > "$OUT"
echo "Wrote $OUT"
