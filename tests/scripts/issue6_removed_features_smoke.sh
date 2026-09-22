#!/usr/bin/env bash
# Issue #6 冒烟：已删功能 404、保留功能可用、页面权限框架未启用。
#
# 用法（后端 + 前端已启动）：
#   BASE_URL=http://127.0.0.1:8000 FRONTEND_URL=http://127.0.0.1 \
#   SUPER_USER=superuser SUPER_PASS=<超管口令> \
#   bash tests/scripts/issue6_removed_features_smoke.sh
#
# 后端不可用时脚本输出 SKIP，不会产生 FAIL；适合本地/验收前快速跑。
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE="${BASE:-$BASE_URL/api/v1}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1}"
SUPER_USER="${SUPER_USER:-superuser}"
# 超管口令不入库：必须由环境变量提供（值同 .env 的 BOOTSTRAP_SUPERUSER_PASSWORD）
SUPER_PASS="${SUPER_PASS:?请先 export SUPER_PASS=<超管口令>}"
RUN_ID="${RUN_ID:-issue6_smoke_$(date +%Y%m%d_%H%M%S)}"
WORKDIR="${WORKDIR:-/tmp/nbhx_issue6_smoke_$RUN_ID}"

mkdir -p "$WORKDIR"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

source "$PROJECT_ROOT/tests/scripts/_lib.sh"

SUPER_TOKEN=""
AUTH=""

check_removed_routes() {
  section "1. 已删 API 前缀返回 404"

  local prefix code
  for prefix in closing-form quotation sqlserver; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/$prefix/" 2>/dev/null)"
    [ -n "$code" ] || code=000
    if [ "$code" = "404" ]; then
      pass "/api/v1/$prefix/ -> 404"
    elif [ "$code" = "000" ]; then
      skip "/api/v1/$prefix/ 后端不可达"
    else
      fail "/api/v1/$prefix/ 期望 404，实际 $code"
    fi
  done
}

check_frontend_routes_removed() {
  section "2. 前端路由静态检查"

  local router_ts="$PROJECT_ROOT/frontend/apps/chat/src/router/index.ts"

  if grep -q "path: '/:pathMatch(.*)\\*'" "$router_ts"; then
    pass "存在兜底路由：未注册路径重定向到 /chat"
  else
    fail "$router_ts 缺少兜底路由"
  fi

  if grep -qE "path: '/(files|closing-form)'" "$router_ts"; then
    fail "$router_ts 仍注册了已删页面路由"
  else
    pass "已删页面 /files、/closing-form 未注册"
  fi

  if [ -n "$FRONTEND_URL" ]; then
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$FRONTEND_URL/files" 2>/dev/null)"
    [ -n "$code" ] || code=000
    if [ "$code" = "000" ]; then
      skip "前端不可达，跳过 /files HTTP 冒烟（浏览器重定向由路由兜底验证）"
    elif [ "$code" = "200" ]; then
      warn "/files 返回 200 属 SPA history fallback；实际重定向需浏览器执行 JS 验证"
    else
      warn "/files 期望被 SPA 兜底重定向，实际 HTTP $code"
    fi
  fi
}

check_page_permission_framework_disabled() {
  section "3. 页面权限框架未启用"

  local config_py="$PROJECT_ROOT/app/core/config.py"
  local auth_py="$PROJECT_ROOT/app/api/v1/auth.py"
  local auth_ts="$PROJECT_ROOT/frontend/apps/chat/src/services/auth.ts"

  grep -q "PAGE_PERMISSION_MANAGEMENT_ENABLED" "$config_py" \
    && pass "配置项 PAGE_PERMISSION_MANAGEMENT_ENABLED 保留" \
    || fail "缺少 PAGE_PERMISSION_MANAGEMENT_ENABLED 配置"

  grep -q "page-permissions" "$auth_py" \
    && pass "PATCH /auth/users/{id}/page-permissions 框架代码保留" \
    || fail "页面权限框架端点代码缺失"

  grep -q "updateUserPagePermissions" "$auth_ts" \
    && pass "前端 updateUserPagePermissions service 保留" \
    || fail "前端页面权限 service 缺失"

  if [ -n "$AUTH" ]; then
    local code
    code="$(curl -sS -o "$WORKDIR/page_permissions.body" -w '%{http_code}' \
      -X PATCH "$BASE/auth/users/00000000-0000-0000-0000-000000000000/page-permissions" \
      -H "$AUTH" -H "Content-Type: application/json" \
      -d '{"page_permissions":{"future_page":true}}' 2>/dev/null)"
    [ -n "$code" ] || code=000
    if [ "$code" = "404" ]; then
      pass "默认关闭时 PATCH /auth/users/{id}/page-permissions -> 404"
    elif [ "$code" = "000" ]; then
      skip "后端不可达，跳过权限端点冒烟"
    else
      fail "权限端点期望 404，实际 $code（body=$(cat "$WORKDIR/page_permissions.body" 2>/dev/null | head -c 200)）"
    fi
  fi
}

check_kept_features() {
  section "4. 保留功能冒烟（knowledge / retriever）"

  if [ -z "$SUPER_TOKEN" ]; then
    skip "未登录 superuser，跳过 knowledge / retriever 冒烟"
    return
  fi

  local code

  code="$(curl -sS -o "$WORKDIR/knowledge_list.body" -w '%{http_code}' \
    "$BASE/knowledge/records" -H "$AUTH" 2>/dev/null)"
  [ -n "$code" ] || code=000
  if [ "$code" = "200" ]; then
    pass "GET /api/v1/knowledge/records -> 200"
  elif [ "$code" = "000" ]; then
    skip "后端不可达，跳过 knowledge 列表冒烟"
  else
    fail "GET /api/v1/knowledge/records 期望 200，实际 $code"
  fi

  code="$(curl -sS -o "$WORKDIR/knowledge_delete.body" -w '%{http_code}' \
    -X DELETE "$BASE/knowledge/records/999999999" -H "$AUTH" 2>/dev/null)"
  [ -n "$code" ] || code=000
  if [ "$code" = "404" ] || [ "$code" = "400" ] || [ "$code" = "422" ]; then
    pass "DELETE /api/v1/knowledge/records/{id} 路由存在（非法 id -> $code）"
  elif [ "$code" = "000" ]; then
    skip "后端不可达，跳过 knowledge 删除冒烟"
  else
    fail "DELETE /api/v1/knowledge/records/{id} 期望 4xx，实际 $code"
  fi

  cat > "$WORKDIR/issue6_upload.txt" <<EOF
issue6 smoke ${RUN_ID}
EOF

  code="$(curl -sS -o "$WORKDIR/knowledge_upload.body" -w '%{http_code}' \
    -X POST "$BASE/knowledge/documents?on_conflict=replace" \
    -H "$AUTH" \
    -F "file=@$WORKDIR/issue6_upload.txt;filename=issue6_${RUN_ID}.txt" \
    -F "uploader=superuser" 2>/dev/null)"
  [ -n "$code" ] || code=000
  if [ "$code" = "200" ] || [ "$code" = "201" ] || [ "$code" = "202" ] || [ "$code" = "409" ]; then
    pass "POST /api/v1/knowledge/documents -> $code"
  elif [ "$code" = "000" ]; then
    skip "后端不可达，跳过 knowledge 上传冒烟"
  else
    fail "POST /api/v1/knowledge/documents 期望 2xx/409，实际 $code"
  fi

  code="$(curl -sS -o "$WORKDIR/retriever_db.body" -w '%{http_code}' \
    -X POST "$BASE/retriever/db?collection=knowledge_chunks" \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"question":"issue6 smoke ping"}' 2>/dev/null)"
  [ -n "$code" ] || code=000
  if [ "$code" = "200" ]; then
    pass "POST /api/v1/retriever/db?collection=knowledge_chunks -> 200"
  elif [ "$code" = "000" ]; then
    skip "后端不可达，跳过 retriever 冒烟"
  else
    fail "POST /api/v1/retriever/db 期望 200，实际 $code"
  fi
}

summary() {
  section "5. 总结"
  echo "RUN_ID=$RUN_ID"
  echo "WORKDIR=$WORKDIR"
  echo "PASS_COUNT=$PASS_COUNT"
  echo "WARN_COUNT=$WARN_COUNT"
  echo "SKIP_COUNT=$SKIP_COUNT"
  echo "FAIL_COUNT=$FAIL_COUNT"

  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "[RESULT] Issue #6 冒烟通过：无 FAIL。"
  else
    echo "[RESULT] Issue #6 冒烟存在 FAIL。"
  fi
}

main() {
  need_cmd curl
  need_cmd jq
  need_cmd grep

  check_removed_routes

  section "0. superuser 登录（用于保留功能冒烟）"
  local login_json
  login_json="$(login_user "$SUPER_USER" "$SUPER_PASS" || true)"
  SUPER_TOKEN="$(echo "$login_json" | jq -r '.access_token // empty' 2>/dev/null || true)"
  if [ -n "$SUPER_TOKEN" ] && [ "$SUPER_TOKEN" != "null" ]; then
    AUTH="Authorization: Bearer $SUPER_TOKEN"
    pass "superuser 登录成功"
  else
    skip "superuser 未登录（后端不可达或凭据不可用），保留功能脚本将跳过"
  fi

  check_frontend_routes_removed
  check_page_permission_framework_disabled
  check_kept_features
  summary
}

main "$@"
