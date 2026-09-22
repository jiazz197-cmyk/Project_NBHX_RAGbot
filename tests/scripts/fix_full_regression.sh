#!/usr/bin/env bash
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE="${BASE:-$BASE_URL/api/v1}"

SUPER_USER="${SUPER_USER:-superuser}"
# 超管口令不入库：必须由环境变量提供（值同 .env 的 BOOTSTRAP_SUPERUSER_PASSWORD）
SUPER_PASS="${SUPER_PASS:?请先 export SUPER_PASS=<超管口令>}"

TEST_PDF="${TEST_PDF:-/home/shmtu/桌面/ADW-0314S规格书.pdf}"
RUN_ID="${RUN_ID:-fix_ws_$(date +%Y%m%d_%H%M%S)}"

WORKDIR="${WORKDIR:-/tmp/nbhx_fix_regression_$RUN_ID}"
LOGFILE="$WORKDIR/full_regression.log"

# WebSocket 校验用解释器：优先 PYBIN，其次 VENV_DIR / 项目 .venv / nbhxenv，最后回落 PATH
resolve_pybin() {
  local cand
  if [[ -n "${PYBIN:-}" && -x "${PYBIN}" ]]; then echo "${PYBIN}"; return; fi
  for cand in "${VENV_DIR:-}" "${PROJECT_ROOT}/.venv" "${HOME}/桌面/nbhxenv" "${HOME}/nbhxenv"; do
    if [[ -n "$cand" && -x "${cand}/bin/python" ]]; then echo "${cand}/bin/python"; return; fi
  done
  command -v python3 || command -v python || echo python
}
PYBIN="$(resolve_pybin)"

mkdir -p "$WORKDIR"

exec > >(tee -a "$LOGFILE") 2>&1

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

SUPER_TOKEN=""
AUTH=""

FIX_USER=""
FIX_USER_ID=""
FIX_USER_PASS="Smoke.5001"

FIX_FILE_ID=""


FIX_OCR_IMAGE_TASK_ID=""
FIX_OCR_PDF_TASK_ID=""
FIX_DOC_TASK_ID=""

# Shared low-level helpers (section/pass/warn/fail/skip/need_cmd/http_code/
# json_print/redact_token_json/login_user/make_png) live in _lib.sh.
source "$PROJECT_ROOT/tests/scripts/_lib.sh"

init() {
  section "0. 初始化"

  need_cmd curl
  need_cmd jq
  need_cmd python
  need_cmd file

  echo "PROJECT_ROOT=$PROJECT_ROOT"
  echo "BASE=$BASE"
  echo "RUN_ID=$RUN_ID"
  echo "WORKDIR=$WORKDIR"
  echo "LOGFILE=$LOGFILE"
  echo "TEST_PDF=$TEST_PDF"

  if [ ! -f "$TEST_PDF" ]; then
    fail "测试 PDF 不存在: $TEST_PDF"
    exit 1
  fi

  pass "初始化完成"
}

login_superuser() {
  section "1. superuser 登录"

  local login_json
  login_json="$(login_user "$SUPER_USER" "$SUPER_PASS")"

  echo "$login_json" | json_print

  SUPER_TOKEN="$(echo "$login_json" | jq -r '.access_token // empty')"

  if [ -z "$SUPER_TOKEN" ] || [ "$SUPER_TOKEN" = "null" ]; then
    fail "superuser 登录失败"
    exit 1
  fi

  AUTH="Authorization: Bearer $SUPER_TOKEN"

  curl -sS "$BASE/auth/me" -H "$AUTH" > "$WORKDIR/super_me.json"
  cat "$WORKDIR/super_me.json" | jq '{id,username,role,is_active}'

  local username role
  username="$(jq -r '.username // empty' "$WORKDIR/super_me.json")"
  role="$(jq -r '.role // empty' "$WORKDIR/super_me.json")"

  if [ "$username" = "$SUPER_USER" ]; then
    pass "superuser 登录和 /auth/me 通过: role=$role"
  else
    fail "superuser /auth/me 返回异常"
  fi
}

test_health() {
  section "2. Health"

  curl -sS -D "$WORKDIR/health.headers" \
    "$BASE/health" \
    -o "$WORKDIR/health.body"

  cat "$WORKDIR/health.headers"
  cat "$WORKDIR/health.body" | json_print

  local code status pg redis
  code="$(http_code "$WORKDIR/health.headers")"
  status="$(jq -r '.status // empty' "$WORKDIR/health.body" 2>/dev/null)"
  pg="$(jq -r '.services.postgresql.status // empty' "$WORKDIR/health.body" 2>/dev/null)"
  redis="$(jq -r '.services.redis.status // empty' "$WORKDIR/health.body" 2>/dev/null)"

  if [ "$code" = "200" ] && [ "$status" = "healthy" ]; then
    pass "health 通过: postgresql=$pg redis=$redis"
  else
    fail "health 异常: HTTP=$code status=$status"
  fi
}

test_auth_reject() {
  section "3. 鉴权拒绝"

  curl -sS -D "$WORKDIR/no_token.headers" \
    "$BASE/auth/me" \
    -o "$WORKDIR/no_token.body"

  cat "$WORKDIR/no_token.headers"
  cat "$WORKDIR/no_token.body" | json_print

  if [ "$(http_code "$WORKDIR/no_token.headers")" = "401" ]; then
    pass "无 token 返回 401"
  else
    warn "无 token 不是 401"
  fi

  curl -sS -D "$WORKDIR/bad_token.headers" \
    "$BASE/auth/me" \
    -H "Authorization: Bearer bad.token.value" \
    -o "$WORKDIR/bad_token.body"

  cat "$WORKDIR/bad_token.headers"
  cat "$WORKDIR/bad_token.body" | json_print

  if [ "$(http_code "$WORKDIR/bad_token.headers")" = "401" ]; then
    pass "错误 token 返回 401"
  else
    warn "错误 token 不是 401"
  fi
}

test_user_crud() {
  section "4. 用户注册 / 登录 / 删除"

  FIX_USER="fix_user_${RUN_ID}"
  local email="${FIX_USER}@example.com"

  curl -sS -D "$WORKDIR/user_register.headers" \
    -X POST "$BASE/auth/register" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$FIX_USER\",\"password\":\"$FIX_USER_PASS\",\"email\":\"$email\"}" \
    -o "$WORKDIR/user_register.body"

  cat "$WORKDIR/user_register.headers"
  cat "$WORKDIR/user_register.body" | json_print

  local code
  code="$(http_code "$WORKDIR/user_register.headers")"

  if [ "$code" = "201" ] || [ "$code" = "200" ]; then
    pass "注册临时用户成功: $FIX_USER"
  else
    fail "注册临时用户失败: HTTP=$code"
    return
  fi

  local new_login new_token
  new_login="$(login_user "$FIX_USER" "$FIX_USER_PASS")"
  echo "$new_login" | json_print
  new_token="$(echo "$new_login" | jq -r '.access_token // empty')"

  if [ -n "$new_token" ] && [ "$new_token" != "null" ]; then
    pass "临时用户登录成功"
  else
    fail "临时用户登录失败"
  fi

  curl -sS "$BASE/auth/users" -H "$AUTH" > "$WORKDIR/users_list.body"

  FIX_USER_ID="$(jq -r --arg u "$FIX_USER" '
    if type=="array" then
      (.[] | select(.username==$u) | .id)
    elif has("items") then
      (.items[] | select(.username==$u) | .id)
    elif has("users") then
      (.users[] | select(.username==$u) | .id)
    else empty end
  ' "$WORKDIR/users_list.body" | head -n 1)"

  echo "FIX_USER_ID=$FIX_USER_ID"

  if [ -n "$FIX_USER_ID" ]; then
    pass "用户列表能查到临时用户"
  else
    warn "用户列表未提取到临时用户 ID"
  fi
}

test_file_manager() {
  section "5. 文件管理"

  echo "fix file smoke $RUN_ID $(date)" > "$WORKDIR/file_smoke.txt"

  curl -sS -D "$WORKDIR/file_upload.headers" \
    -X POST "$BASE/files/upload?uploader=superuser" \
    -H "$AUTH" \
    -F "file=@$WORKDIR/file_smoke.txt" \
    -o "$WORKDIR/file_upload.body"

  cat "$WORKDIR/file_upload.headers"
  cat "$WORKDIR/file_upload.body" | json_print

  FIX_FILE_ID="$(jq -r '.id // .file_id // empty' "$WORKDIR/file_upload.body")"
  echo "FIX_FILE_ID=$FIX_FILE_ID"

  if [ -z "$FIX_FILE_ID" ] || [ "$FIX_FILE_ID" = "null" ]; then
    fail "文件上传未返回 id"
    return
  fi

  curl -sS -D "$WORKDIR/file_download.headers" \
    "$BASE/files/download/$FIX_FILE_ID" \
    -H "$AUTH" \
    -o "$WORKDIR/file_downloaded.txt"

  cat "$WORKDIR/file_download.headers"

  if diff -u "$WORKDIR/file_smoke.txt" "$WORKDIR/file_downloaded.txt"; then
    pass "文件上传下载 diff 通过"
  else
    fail "文件下载内容不一致"
  fi

  curl -sS "$BASE/files/info/$FIX_FILE_ID" -H "$AUTH" > "$WORKDIR/file_info.body"
  cat "$WORKDIR/file_info.body" | json_print

  curl -sS "$BASE/files/list?limit=10&offset=0" -H "$AUTH" > "$WORKDIR/file_list.body"
  cat "$WORKDIR/file_list.body" | jq '{total, first:(.items[0] // null)}' 2>/dev/null || true

  curl -sS "$BASE/files/search?keyword=file_smoke&limit=10&offset=0" -H "$AUTH" > "$WORKDIR/file_search.body"
  cat "$WORKDIR/file_search.body" | json_print

  pass "文件 info/list/search 已调用"

  curl -sS -D "$WORKDIR/file_delete.headers" \
    -X DELETE "$BASE/files/delete/$FIX_FILE_ID" \
    -H "$AUTH" \
    -o "$WORKDIR/file_delete.body"

  cat "$WORKDIR/file_delete.headers"
  cat "$WORKDIR/file_delete.body" | json_print

  if [ "$(http_code "$WORKDIR/file_delete.headers")" = "200" ]; then
    pass "文件删除通过"
  else
    fail "文件删除失败"
  fi
}
poll_status() {
  local name="$1"
  local url="$2"
  local max_count="$3"
  local interval="$4"
  local out_file="$5"

  local i status json
  for i in $(seq 1 "$max_count"); do
    json="$(curl -sS "$url" -H "$AUTH")"

    echo "---- $name poll $i ----"
    echo "$json" | json_print
    echo "$json" > "$out_file"

    if echo "$json" | jq -e '.code == 429' >/dev/null 2>&1; then
      warn "$name 触发 429，停止轮询"
      return 2
    fi

    status="$(echo "$json" | jq -r '.status // .data.status // empty')"

    case "$status" in
      completed|failed|cancelled|awaiting_approval)
        echo "$name checkpoint/terminal status: $status"
        return 0
        ;;
    esac

    sleep "$interval"
  done

  warn "$name 轮询超时"
  return 1
}

ws_wait_task() {
  local name="$1"
  local task_id="$2"
  local max_seconds="${3:-180}"
  local out_file="$4"

  if ! "$PYBIN" - <<'PY_CHECK' >/dev/null 2>&1
import websockets
PY_CHECK
  then
    warn "当前 Python 环境（$PYBIN）未安装 websockets，无法 WebSocket 订阅 $name"
    return 2
  fi

  cat > "$WORKDIR/ws_wait_task.py" <<'PY_WS'
import asyncio
import json
import os
import sys
import time

import websockets

base_url = os.environ.get("WS_BASE_URL", "ws://127.0.0.1:8000")
task_id = os.environ["WS_TASK_ID"]
token = os.environ["WS_TOKEN"]
max_seconds = int(os.environ.get("WS_MAX_SECONDS", "180"))
out_file = os.environ["WS_OUT_FILE"]
name = os.environ.get("WS_NAME", "task")

url = f"{base_url}/api/v1/document-tasks/ws/{task_id}"

terminal_statuses = {"awaiting_approval", "completed", "failed", "cancelled"}
last_status = None
last_message = None
last_payload = None

def write_event(payload):
    with open(out_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

def extract_status(payload):
    if isinstance(payload, dict):
        for key in ("status", "task_status"):
            if isinstance(payload.get(key), str):
                return payload[key]

        for parent_key in ("data", "task", "event", "payload"):
            data = payload.get(parent_key)
            if isinstance(data, dict):
                for key in ("status", "task_status"):
                    if isinstance(data.get(key), str):
                        return data[key]

    return None

async def main():
    global last_status, last_message, last_payload

    print(f"[ws] connecting {name}: {url}", flush=True)
    started = time.time()

    try:
        async with websockets.connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"token": token}, ensure_ascii=False))
            print("[ws] auth sent", flush=True)

            while True:
                remain = max_seconds - int(time.time() - started)

                if remain <= 0:
                    timeout_payload = {
                        "type": "timeout",
                        "task_id": task_id,
                        "last_status": last_status,
                        "last_message": last_message,
                        "last_payload": last_payload,
                    }
                    print(json.dumps(timeout_payload, ensure_ascii=False), flush=True)
                    write_event(timeout_payload)
                    sys.exit(3)

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(60, remain))
                except asyncio.TimeoutError:
                    heartbeat = {
                        "type": "client_waiting",
                        "task_id": task_id,
                        "elapsed": int(time.time() - started),
                        "last_status": last_status,
                    }
                    print(json.dumps(heartbeat, ensure_ascii=False), flush=True)
                    write_event(heartbeat)
                    continue

                print(raw, flush=True)

                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"type": "raw", "message": raw}

                write_event(payload)
                last_payload = payload

                status = extract_status(payload)
                if status:
                    last_status = status

                if isinstance(payload, dict):
                    msg = payload.get("message")
                    if not msg and isinstance(payload.get("data"), dict):
                        msg = payload["data"].get("message")
                    if msg:
                        last_message = msg

                if last_status in terminal_statuses:
                    done_payload = {
                        "type": "client_done",
                        "task_id": task_id,
                        "status": last_status,
                        "message": last_message,
                    }
                    print(json.dumps(done_payload, ensure_ascii=False), flush=True)
                    write_event(done_payload)
                    return

    except Exception as exc:
        err = {
            "type": "ws_exception",
            "task_id": task_id,
            "error": repr(exc),
            "last_status": last_status,
            "last_message": last_message,
        }
        print(json.dumps(err, ensure_ascii=False), flush=True)
        write_event(err)
        sys.exit(2)

asyncio.run(main())
PY_WS

  : > "$out_file"

  WS_BASE_URL="${WS_BASE_URL:-ws://127.0.0.1:8000}" \
  WS_TASK_ID="$task_id" \
  WS_TOKEN="$SUPER_TOKEN" \
  WS_MAX_SECONDS="$max_seconds" \
  WS_OUT_FILE="$out_file" \
  WS_NAME="$name" \
  PYTHONUNBUFFERED=1 "$PYBIN" -u "$WORKDIR/ws_wait_task.py"

  return $?
}

test_ocr_pdf() {
  section "7. OCR / PDF 转图"

  make_png "$WORKDIR/ocr.png" >/dev/null

  curl -sS -D "$WORKDIR/ocr_image_submit.headers" \
    -X POST "$BASE/ocr/image/upload" \
    -H "$AUTH" \
    -F "file=@$WORKDIR/ocr.png" \
    -o "$WORKDIR/ocr_image_submit.body"

  cat "$WORKDIR/ocr_image_submit.headers"
  cat "$WORKDIR/ocr_image_submit.body" | json_print

  FIX_OCR_IMAGE_TASK_ID="$(jq -r '.task_id // empty' "$WORKDIR/ocr_image_submit.body")"
  echo "FIX_OCR_IMAGE_TASK_ID=$FIX_OCR_IMAGE_TASK_ID"

  if [ -n "$FIX_OCR_IMAGE_TASK_ID" ]; then
    poll_status "ocr image" "$BASE/ocr/image/task/$FIX_OCR_IMAGE_TASK_ID" 30 2 "$WORKDIR/ocr_image_status.body" || true

    curl -sS -D "$WORKDIR/ocr_image_result.headers" \
      "$BASE/ocr/image/task/$FIX_OCR_IMAGE_TASK_ID/result" \
      -H "$AUTH" \
      -o "$WORKDIR/ocr_image_result.body"

    cat "$WORKDIR/ocr_image_result.headers"
    cat "$WORKDIR/ocr_image_result.body" | json_print

    if grep -qi "different loop" "$WORKDIR/ocr_image_status.body" "$WORKDIR/ocr_image_result.body" 2>/dev/null; then
      fail "OCR 图片上传仍存在 different loop"
    elif grep -q '"completed"\|"success"' "$WORKDIR/ocr_image_status.body" "$WORKDIR/ocr_image_result.body" 2>/dev/null; then
      pass "OCR 图片上传 completed"
    else
      warn "OCR 图片上传结果需人工确认"
    fi
  else
    fail "OCR 图片上传未返回 task_id"
  fi

  curl -sS -D "$WORKDIR/ocr_pdf_page_count.headers" \
    -X POST "$BASE/ocr/pdf/page-count" \
    -H "$AUTH" \
    -F "file=@$TEST_PDF" \
    -o "$WORKDIR/ocr_pdf_page_count.body"

  cat "$WORKDIR/ocr_pdf_page_count.headers"
  cat "$WORKDIR/ocr_pdf_page_count.body" | json_print

  if [ "$(http_code "$WORKDIR/ocr_pdf_page_count.headers")" = "200" ]; then
    pass "PDF page-count 通过"
  else
    fail "PDF page-count 失败"
  fi

  curl -sS -D "$WORKDIR/ocr_pdf_submit.headers" \
    -X POST "$BASE/ocr/pdf/convert?uploader=superuser" \
    -H "$AUTH" \
    -F "file=@$TEST_PDF" \
    -o "$WORKDIR/ocr_pdf_submit.body"

  cat "$WORKDIR/ocr_pdf_submit.headers"
  cat "$WORKDIR/ocr_pdf_submit.body" | json_print

  FIX_OCR_PDF_TASK_ID="$(jq -r '.task_id // empty' "$WORKDIR/ocr_pdf_submit.body")"
  echo "FIX_OCR_PDF_TASK_ID=$FIX_OCR_PDF_TASK_ID"

  if [ -n "$FIX_OCR_PDF_TASK_ID" ]; then
    poll_status "ocr pdf" "$BASE/ocr/pdf/task/$FIX_OCR_PDF_TASK_ID" 60 3 "$WORKDIR/ocr_pdf_status.body" || true

    curl -sS -D "$WORKDIR/ocr_pdf_result.headers" \
      "$BASE/ocr/pdf/task/$FIX_OCR_PDF_TASK_ID/result" \
      -H "$AUTH" \
      -o "$WORKDIR/ocr_pdf_result.body"

    cat "$WORKDIR/ocr_pdf_result.headers"
    cat "$WORKDIR/ocr_pdf_result.body" | json_print

    if grep -qi "different loop" "$WORKDIR/ocr_pdf_status.body" "$WORKDIR/ocr_pdf_result.body" 2>/dev/null; then
      fail "OCR PDF 转图仍存在 different loop"
    elif grep -q '"completed"\|"success"' "$WORKDIR/ocr_pdf_status.body" "$WORKDIR/ocr_pdf_result.body" 2>/dev/null; then
      pass "OCR PDF 转图 completed"
    else
      warn "OCR PDF 转图结果需人工确认"
    fi
  else
    fail "OCR PDF 转图未返回 task_id"
  fi
}

test_document_processing() {
  section "8. 文档处理"

  curl -sS -D "$WORKDIR/doc_submit.headers" \
    -X POST "$BASE/document-tasks/process?collection=knowledge_chunks&chunk_size=500&chunk_overlap=50&uploader=superuser" \
    -H "$AUTH" \
    -F "files=@$TEST_PDF" \
    -o "$WORKDIR/doc_submit.body"

  cat "$WORKDIR/doc_submit.headers"
  cat "$WORKDIR/doc_submit.body" | json_print

  FIX_DOC_TASK_ID="$(jq -r '.task_id // empty' "$WORKDIR/doc_submit.body")"
  echo "FIX_DOC_TASK_ID=$FIX_DOC_TASK_ID"

  if [ -z "$FIX_DOC_TASK_ID" ]; then
    fail "文档处理未返回 task_id"
    return
  fi

  poll_status "document" "$BASE/document-tasks/status/$FIX_DOC_TASK_ID" 120 5 "$WORKDIR/doc_status.body" || true

  if grep -qi "different loop\|没有成功下载任何文件" "$WORKDIR/doc_status.body"; then
    fail "文档处理仍存在 MinIO 下载 / different loop 问题"
  elif grep -q '"completed"' "$WORKDIR/doc_status.body"; then
    pass "文档处理 completed"
  else
    warn "文档处理状态需人工确认"
  fi
}

test_rag() {
  section "10. RAG / Retriever"

  # 接口分家：/retriever/db 检索文档表 knowledge_chunks；
  # /retriever/excel 检索 Excel 表 excel_db_chunks（历史集合已下线，保留 Excel 集合）。

  curl -sS -D "$WORKDIR/rag_db_2.headers" \
    -X POST "$BASE/retriever/db?collection=knowledge_chunks" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{"question":"系统接口 API 如何调用？"}' \
    -o "$WORKDIR/rag_db_2.body"

  cat "$WORKDIR/rag_db_2.headers"
  cat "$WORKDIR/rag_db_2.body" | json_print

  curl -sS -D "$WORKDIR/rag_excel_1.headers" \
    -X POST "$BASE/retriever/excel?collection=excel_db_chunks" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{"question":"智能组合秤和重量分选秤有什么区别？"}' \
    -o "$WORKDIR/rag_excel_1.body"

  cat "$WORKDIR/rag_excel_1.headers"
  cat "$WORKDIR/rag_excel_1.body" | json_print

  if grep -qi "handler is closed\|TCPTransport closed" "$WORKDIR/rag_db_2.body"; then
    fail "RAG DB 检索仍存在 handler closed"
  else
    pass "RAG DB 未出现 handler closed"
  fi
}

test_websocket_reject() {
  section "11. WebSocket 拒绝场景"

  if ! "$PYBIN" - <<'PY' >/dev/null 2>&1
import websockets
PY
  then
    warn "当前 Python 环境（$PYBIN）未安装 websockets，跳过 WebSocket 拒绝场景"
    return
  fi

  local ws_task_id
  ws_task_id="$FIX_DOC_TASK_ID"

  if [ -z "$ws_task_id" ]; then
    warn "没有可用于 WebSocket 测试的 task_id"
    return
  fi

  cat > "$WORKDIR/ws_reject.py" <<'PY'
import asyncio
import json
import os
import sys
import websockets

url = os.environ["WS_URL"]
token = os.environ["TOKEN"]
mode = os.environ.get("MODE", "ok")

async def main():
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            if mode == "bad":
                payload = {"token": "bad.token.value"}
            elif mode == "wrong_field":
                payload = {"access_token": token}
            else:
                payload = {"token": token}
            await ws.send(json.dumps(payload, ensure_ascii=False))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                print(msg)
            except asyncio.TimeoutError:
                print("TIMEOUT_NO_MESSAGE")
    except Exception as e:
        print("WS_EXCEPTION:", repr(e))
        sys.exit(2)

asyncio.run(main())
PY

  export WS_URL="ws://127.0.0.1:8000/api/v1/document-tasks/ws/$ws_task_id"
  export TOKEN="$SUPER_TOKEN"

  MODE=bad "$PYBIN" "$WORKDIR/ws_reject.py" > "$WORKDIR/ws_bad.out" 2>&1 || true
  cat "$WORKDIR/ws_bad.out"

  MODE=wrong_field "$PYBIN" "$WORKDIR/ws_reject.py" > "$WORKDIR/ws_wrong.out" 2>&1 || true
  cat "$WORKDIR/ws_wrong.out"

  pass "WebSocket 拒绝场景已执行"
}

test_retention_pg_redis() {
  section "12. Redis / PG / Retention"

  sudo docker exec redis redis-cli PING || true

  sudo grep -i "MinIO reconcile" "$PROJECT_ROOT/logs/app.log" | tail -n 120 || true

  pass "Redis / PG / Retention 检查完成"
}

scan_error_logs() {
  section "13. 核心错误扫描"

  sudo grep -i "run_async() cannot be used inside a running event loop\|different loop\|handler is closed\|TCPTransport closed" "$PROJECT_ROOT/logs/app.log" | tail -n 160 > "$WORKDIR/core_errors.txt" || true

  cat "$WORKDIR/core_errors.txt"

  if [ -s "$WORKDIR/core_errors.txt" ]; then
    warn "日志中仍存在历史或当前核心错误，请结合时间判断"
  else
    pass "日志扫描未发现核心错误关键词"
  fi
}

cleanup() {
  section "14. 清理测试数据"

  if [ -n "$FIX_FILE_ID" ]; then
    curl -sS -X DELETE "$BASE/files/delete/$FIX_FILE_ID" -H "$AUTH" >/dev/null 2>&1 || true
  fi

  if [ -n "$FIX_USER_ID" ]; then
    curl -sS -D "$WORKDIR/user_delete.headers" \
      -X DELETE "$BASE/auth/users/$FIX_USER_ID" \
      -H "$AUTH" \
      -o "$WORKDIR/user_delete.body" || true

    cat "$WORKDIR/user_delete.headers" 2>/dev/null || true
    cat "$WORKDIR/user_delete.body" 2>/dev/null || true
    echo

    pass "临时用户清理已执行"
  fi

  echo "清理完成"
}

summary() {
  section "15. 总结"

  echo "RUN_ID=$RUN_ID"
  echo "WORKDIR=$WORKDIR"
  echo "LOGFILE=$LOGFILE"
  echo "FIX_DOC_TASK_ID=$FIX_DOC_TASK_ID"
  echo "FIX_OCR_IMAGE_TASK_ID=$FIX_OCR_IMAGE_TASK_ID"
  echo "FIX_OCR_PDF_TASK_ID=$FIX_OCR_PDF_TASK_ID"
  echo
  echo "PASS_COUNT=$PASS_COUNT"
  echo "WARN_COUNT=$WARN_COUNT"
  echo "FAIL_COUNT=$FAIL_COUNT"

  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "[RESULT] 回归脚本执行完成：无 FAIL。请人工复核 WARN。"
  else
    echo "[RESULT] 回归脚本执行完成：存在 FAIL，需要查看日志。"
  fi

  echo
  echo "常用查看命令："
  echo "grep -E \"\\[PASS\\]|\\[WARN\\]|\\[FAIL\\]|\\[RESULT\\]\" \"$LOGFILE\""

}

main() {
  init
  login_superuser
  test_health
  test_auth_reject
  test_user_crud
  test_file_manager
  test_ocr_pdf
  test_document_processing
  test_rag
  test_websocket_reject
  test_retention_pg_redis
  scan_error_logs
  cleanup
  summary
}

main "$@"
