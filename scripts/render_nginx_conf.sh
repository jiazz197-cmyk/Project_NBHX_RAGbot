#!/usr/bin/env bash
# Render nginx/nginx.conf from nginx/nginx.conf.template.
#
# The template no longer contains deployment-time envsubst placeholders (the
# legacy external-chat Authorization/key injection was removed), so rendering
# is a plain copy.  Keep this script as the stable entrypoint used by
# systemd/smoke tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="${ROOT}/nginx/nginx.conf.template"
OUT="${ROOT}/nginx/nginx.conf"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Missing template: $TEMPLATE" >&2
  exit 1
fi

cp "$TEMPLATE" "$OUT"
echo "Wrote $OUT"
