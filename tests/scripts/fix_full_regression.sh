#!/usr/bin/env bash
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE="${BASE:-$BASE_URL/api/v1}"

SUPER_USER="${SUPER_USER:-superuser}"
SUPER_PASS="${SUPER_PASS:-superuser.5001}"

TEST_PDF="${TEST_PDF:-/home/shmtu/桌面/ADW-0314S规格书.pdf}"
RUN_ID="${RUN_ID:-fix_ws_$(date +%Y%m%d_%H%M%S)}"

WORKDIR="${WORKDIR:-/tmp/yamato_fix_regression_$RUN_ID}"
LOGFILE="$WORKDIR/full_regression.log"

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

FIX_CLOSING_IMAGE_OBJECT=""
FIX_CLOSING_FORM_ID=""
FIX_CLOSING_CUSTOMER=""

FIX_OCR_IMAGE_TASK_ID=""
FIX_OCR_PDF_TASK_ID=""
FIX_DOC_TASK_ID=""
FIX_Q_TASK_ID=""

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

test_closing_form() {
  section "6. Closing Form"

  make_png "$WORKDIR/closing.png" >/dev/null

  curl -sS -D "$WORKDIR/closing_image_upload.headers" \
    -X POST "$BASE/closing-form/image/upload" \
    -H "$AUTH" \
    -F "image=@$WORKDIR/closing.png" \
    -o "$WORKDIR/closing_image_upload.body"

  cat "$WORKDIR/closing_image_upload.headers"
  cat "$WORKDIR/closing_image_upload.body" | json_print

  FIX_CLOSING_IMAGE_OBJECT="$(jq -r '.object_name // empty' "$WORKDIR/closing_image_upload.body")"
  echo "FIX_CLOSING_IMAGE_OBJECT=$FIX_CLOSING_IMAGE_OBJECT"

  if [ -z "$FIX_CLOSING_IMAGE_OBJECT" ] || [ "$FIX_CLOSING_IMAGE_OBJECT" = "null" ]; then
    fail "Closing Form 图片上传未返回 object_name"
    return
  fi

  curl -sS -D "$WORKDIR/closing_image_get.headers" \
    "$BASE/closing-form/image/$FIX_CLOSING_IMAGE_OBJECT" \
    -H "$AUTH" \
    -o "$WORKDIR/closing_get.png"

  cat "$WORKDIR/closing_image_get.headers"
  file "$WORKDIR/closing_get.png" || true

  FIX_CLOSING_CUSTOMER="FIX_REGRESS_CUSTOMER_$RUN_ID"

  local submit_json
  submit_json="$(cat <<EOF
{
  "date": "2026-06-02",
  "closing_date": "2026-06-02",
  "customer_name": "$FIX_CLOSING_CUSTOMER",
  "product_type": "智能组合秤",
  "model_spec": "FIX-MODEL-001",
  "quantity": 1,
  "price_excluding_tax": 1000,
  "production_number": "FIX-PROD-$RUN_ID",
  "material_name": "测试物料",
  "weighing_spec": "10-100g",
  "speed": 60,
  "precision": "±0.1g",
  "top_cone_type": "标准",
  "linear_vibration_type": "标准",
  "material_layer_ring": "无",
  "feed_hopper": "标准",
  "metering_hopper": "标准",
  "memory_hopper": "标准",
  "chute_angle": "标准",
  "collection_hopper_type": "标准",
  "scale_type": "10头",
  "image_url_1": "$FIX_CLOSING_IMAGE_OBJECT",
  "image_url_2": null
}
EOF
)"

  curl -sS -D "$WORKDIR/closing_submit.headers" \
    -X POST "$BASE/closing-form/submit" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d "$submit_json" \
    -o "$WORKDIR/closing_submit.body"

  cat "$WORKDIR/closing_submit.headers"
  cat "$WORKDIR/closing_submit.body" | json_print

  curl -sS "$BASE/closing-form/list" -H "$AUTH" > "$WORKDIR/closing_list.body"

  FIX_CLOSING_FORM_ID="$(jq -r --arg c "$FIX_CLOSING_CUSTOMER" '
    .records[]? | select(.text | contains($c)) | .id
  ' "$WORKDIR/closing_list.body" | head -n 1)"

  echo "FIX_CLOSING_FORM_ID=$FIX_CLOSING_FORM_ID"

  if [ -n "$FIX_CLOSING_FORM_ID" ]; then
    pass "Closing Form 提交后列表可查到记录"
  else
    fail "Closing Form 列表未找到提交记录"
    return
  fi

  curl -sS -D "$WORKDIR/closing_reject.headers" \
    -X PATCH "$BASE/closing-form/reject/$FIX_CLOSING_FORM_ID" \
    -H "$AUTH" \
    -o "$WORKDIR/closing_reject.body"

  cat "$WORKDIR/closing_reject.headers"
  cat "$WORKDIR/closing_reject.body" | json_print

  curl -sS -D "$WORKDIR/closing_delete_rejected.headers" \
    -X DELETE "$BASE/closing-form/rejected/$FIX_CLOSING_FORM_ID" \
    -H "$AUTH" \
    -o "$WORKDIR/closing_delete_rejected.body"

  cat "$WORKDIR/closing_delete_rejected.headers"
  cat "$WORKDIR/closing_delete_rejected.body" | json_print

  pass "Closing Form reject + delete rejected 已执行"

  curl -sS -D "$WORKDIR/closing_image_delete.headers" \
    -X DELETE "$BASE/closing-form/image?object_name=$FIX_CLOSING_IMAGE_OBJECT" \
    -H "$AUTH" \
    -o "$WORKDIR/closing_image_delete.body"

  cat "$WORKDIR/closing_image_delete.headers"
  cat "$WORKDIR/closing_image_delete.body" | json_print

  pass "Closing Form 图片删除已执行"
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

  if ! /home/shmtu/桌面/yamatoenv/bin/python - <<'PY_CHECK' >/dev/null 2>&1
import websockets
PY_CHECK
  then
    warn "yamatoenv 未安装 websockets，无法 WebSocket 订阅 $name"
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
  PYTHONUNBUFFERED=1 /home/shmtu/桌面/yamatoenv/bin/python -u "$WORKDIR/ws_wait_task.py"

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
    -X POST "$BASE/document-tasks/process?instance_id=1&chunk_size=500&chunk_overlap=50&uploader=superuser" \
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

test_quotation() {
  section "9. 报价 Worker - WebSocket 订阅版"

  curl -sS -D "$WORKDIR/quotation_submit.headers" \
    -X POST "$BASE/quotation/tasks" \
    -H "$AUTH" \
    -F "file=@$TEST_PDF" \
    -F "task_name=fix-regress-$RUN_ID" \
    -o "$WORKDIR/quotation_submit.body"

  cat "$WORKDIR/quotation_submit.headers"
  cat "$WORKDIR/quotation_submit.body" | json_print

  FIX_Q_TASK_ID="$(jq -r '.task_id // empty' "$WORKDIR/quotation_submit.body")"
  echo "FIX_Q_TASK_ID=$FIX_Q_TASK_ID"

  if [ -z "$FIX_Q_TASK_ID" ]; then
    fail "报价任务未返回 task_id"
    return
  fi

  echo "[quotation] 使用 WebSocket 订阅任务状态，不再 HTTP 轮询"
  echo "[quotation] ws path: /api/v1/document-tasks/ws/$FIX_Q_TASK_ID"
  echo "[quotation] 最多等待 180 秒；超时后自动抓 PG / Redis / 日志快照"

  ws_wait_task "quotation" "$FIX_Q_TASK_ID" 180 "$WORKDIR/quotation_ws_events.jsonl"
  local ws_rc=$?

  echo "[quotation] WebSocket events tail:"
  tail -n 80 "$WORKDIR/quotation_ws_events.jsonl" 2>/dev/null || true

  local final_status
  final_status="$(tail -n 300 "$WORKDIR/quotation_ws_events.jsonl" 2>/dev/null \
    | jq -r 'select(.status? != null) | .status' 2>/dev/null \
    | tail -n 1)"

  if [ -z "$final_status" ]; then
    final_status="$(tail -n 300 "$WORKDIR/quotation_ws_events.jsonl" 2>/dev/null \
      | jq -r 'select(.data?.status? != null) | .data.status' 2>/dev/null \
      | tail -n 1)"
  fi

  echo "FIX_Q_FINAL_STATUS=$final_status"

  case "$final_status" in
    awaiting_approval|completed)
      pass "报价任务通过 WebSocket 推进到 $final_status"
      ;;
    failed)
      warn "报价任务通过 WebSocket 进入 failed，需要查看业务错误"
      ;;
    cancelled)
      warn "报价任务通过 WebSocket 进入 cancelled"
      ;;
    running)
      warn "报价任务 WebSocket 最后状态仍为 running，可能卡在执行阶段"
      ;;
    "")
      if [ "$ws_rc" -eq 3 ]; then
        warn "报价任务 WebSocket 等待超时，未收到终态/awaiting_approval"
      else
        warn "报价任务未解析到最终状态，ws_rc=$ws_rc"
      fi
      ;;
    *)
      warn "报价任务 WebSocket 最后状态: $final_status"
      ;;
  esac

  echo "[quotation] PG snapshot:"
  sudo docker exec pgvector_new psql -U pguser -d pgdb -c "
SELECT id, task_id, status, progress, message, owner_username, created_at, started_at, updated_at, completed_at, awaiting_approval_at, error
FROM quotation_tasks
WHERE task_id = '$FIX_Q_TASK_ID';
" || true

  echo "[quotation] Redis snapshot:"
  local suffix
  suffix="$(echo "$FIX_Q_TASK_ID" | awk -F_ '{print $NF}')"
  sudo docker exec redis redis-cli --scan --pattern "*$suffix*" | tee "$WORKDIR/quotation_redis_keys.txt" || true

  local redis_key
  redis_key="$(head -n 1 "$WORKDIR/quotation_redis_keys.txt" 2>/dev/null || true)"
  if [ -n "$redis_key" ]; then
    sudo docker exec redis redis-cli TYPE "$redis_key" || true
    sudo docker exec redis redis-cli TTL "$redis_key" || true
    sudo docker exec redis redis-cli GET "$redis_key" | jq . 2>/dev/null || true
  fi

  echo "[quotation] app/diag log snapshot:"
  sudo grep -n "$FIX_Q_TASK_ID" "$PROJECT_ROOT/logs/app.log" "$PROJECT_ROOT/logs/diag.log" | tail -n 160 || true

  echo "[quotation] quotation error keyword snapshot:"
  sudo grep -i "$FIX_Q_TASK_ID\|报价任务 Phase1 执行失败\|get_task_payload\|cleanup_task_files_by_id\|different loop\|task_failed\|traceback\|exception" \
    "$PROJECT_ROOT/logs/app.log" "$PROJECT_ROOT/logs/diag.log" \
    | tail -n 220 || true

  if sudo grep -i "$FIX_Q_TASK_ID" "$PROJECT_ROOT/logs/app.log" | grep -qi "different loop"; then
    fail "报价任务仍存在 different loop"
  fi
}

test_rag() {
  section "10. RAG / Retriever"

  curl -sS -D "$WORKDIR/rag_db_1.headers" \
    -X POST "$BASE/retriever/db?instance_id=1" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{"question":"智能组合秤是什么？"}' \
    -o "$WORKDIR/rag_db_1.body"

  cat "$WORKDIR/rag_db_1.headers"
  cat "$WORKDIR/rag_db_1.body" | json_print

  curl -sS -D "$WORKDIR/rag_db_2.headers" \
    -X POST "$BASE/retriever/db?instance_id=2" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{"question":"U8 API 如何调用？"}' \
    -o "$WORKDIR/rag_db_2.body"

  cat "$WORKDIR/rag_db_2.headers"
  cat "$WORKDIR/rag_db_2.body" | json_print

  curl -sS -D "$WORKDIR/rag_excel_1.headers" \
    -X POST "$BASE/retriever/excel?instance_id=1" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{"question":"智能组合秤和重量分选秤有什么区别？"}' \
    -o "$WORKDIR/rag_excel_1.body"

  cat "$WORKDIR/rag_excel_1.headers"
  cat "$WORKDIR/rag_excel_1.body" | json_print

  if grep -qi "handler is closed\|TCPTransport closed" "$WORKDIR/rag_db_1.body" "$WORKDIR/rag_db_2.body"; then
    fail "RAG DB 检索仍存在 handler closed"
  else
    pass "RAG DB 未出现 handler closed"
  fi
}

test_websocket_reject() {
  section "11. WebSocket 拒绝场景"

  if ! /home/shmtu/桌面/yamatoenv/bin/python - <<'PY' >/dev/null 2>&1
import websockets
PY
  then
    warn "yamatoenv 未安装 websockets，跳过 WebSocket 拒绝场景"
    return
  fi

  local ws_task_id
  ws_task_id="$FIX_DOC_TASK_ID"
  if [ -z "$ws_task_id" ]; then
    ws_task_id="$FIX_Q_TASK_ID"
  fi

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

  MODE=bad /home/shmtu/桌面/yamatoenv/bin/python "$WORKDIR/ws_reject.py" > "$WORKDIR/ws_bad.out" 2>&1 || true
  cat "$WORKDIR/ws_bad.out"

  MODE=wrong_field /home/shmtu/桌面/yamatoenv/bin/python "$WORKDIR/ws_reject.py" > "$WORKDIR/ws_wrong.out" 2>&1 || true
  cat "$WORKDIR/ws_wrong.out"

  pass "WebSocket 拒绝场景已执行"
}

test_retention_pg_redis() {
  section "12. Redis / PG / Retention"

  sudo docker exec redis redis-cli PING || true

  sudo docker exec pgvector_new psql -U pguser -d pgdb -c "
SELECT status, count(*)
FROM quotation_tasks
GROUP BY status
ORDER BY count(*) DESC;
" || true

  sudo docker exec pgvector_new psql -U pguser -d pgdb -c "
SELECT id, task_id, status, progress, uploaded_file_name, updated_at, awaiting_approval_at
FROM quotation_tasks
WHERE status='awaiting_approval'
ORDER BY updated_at ASC;
" || true

  sudo grep -i "retention scheduler started\|retention scheduler stopped\|Global terminal retention\|Awaiting approval expiry" "$PROJECT_ROOT/logs/app.log" | tail -n 120 || true

  pass "Redis / PG / Retention 检查完成"
}

scan_error_logs() {
  section "13. 核心错误扫描"

  sudo grep -i "run_async() cannot be used inside a running event loop\|different loop\|handler is closed\|TCPTransport closed\|调度报价任务失败" "$PROJECT_ROOT/logs/app.log" | tail -n 160 > "$WORKDIR/core_errors.txt" || true

  cat "$WORKDIR/core_errors.txt"

  if [ -s "$WORKDIR/core_errors.txt" ]; then
    warn "日志中仍存在历史或当前核心错误，请结合时间判断"
  else
    pass "日志扫描未发现核心错误关键词"
  fi
}

cleanup() {
  section "14. 清理测试数据"

  if [ -n "$FIX_CLOSING_FORM_ID" ]; then
    curl -sS -X DELETE "$BASE/closing-form/rejected/$FIX_CLOSING_FORM_ID" -H "$AUTH" >/dev/null 2>&1 || true
  fi

  if [ -n "$FIX_CLOSING_IMAGE_OBJECT" ]; then
    curl -sS -X DELETE "$BASE/closing-form/image?object_name=$FIX_CLOSING_IMAGE_OBJECT" -H "$AUTH" >/dev/null 2>&1 || true
  fi

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
  echo "FIX_Q_TASK_ID=$FIX_Q_TASK_ID"
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
  echo "tail -n 120 \"$WORKDIR/quotation_ws_events.jsonl\" 2>/dev/null | jq ."
}

main() {
  init
  login_superuser
  test_health
  test_auth_reject
  test_user_crud
  test_file_manager
  test_closing_form
  test_ocr_pdf
  test_document_processing
  test_quotation
  test_rag
  test_websocket_reject
  test_retention_pg_redis
  scan_error_logs
  cleanup
  summary
}

main "$@"
