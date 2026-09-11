# Task state source-of-truth

This document describes where each async task type stores authoritative state.

## Task ID prefixes

| Prefix | Task type | Example |
|--------|-----------|---------|
| `quotation_generation_` | Quotation pipeline (Phase1 + optional Phase2) | `quotation_generation_20260526_131046_609_89c8fedd` |
| `doc_process_` | Document processing | `doc_process_20260407_182347_*` |
| `pdf_convert_` | OCR PDF conversion (short-lived) | `pdf_convert_*` |
| `image_upload_` | OCR image upload (short-lived) | `image_upload_*` |

## Field ownership

### `quotation_generation_*`

| Field | Source of truth | Notes |
|-------|-----------------|-------|
| `status`, `progress`, `message`, `error`, `result_payload` | Postgres `quotation_tasks` | API + FileManager list read PG |
| `owner_id` | Postgres `quotation_tasks.owner_id` | WS auth via `TaskOwnerRegistry` |
| WS progress cache | Redis `task:{task_id}` (24h TTL) | Synced on create/status/progress updates |
| `awaiting_approval_at` | Postgres only | Used for 24h retention expiry |

**Status vocabulary (Postgres):** `queued`, `running`, `awaiting_approval`, `completed`, `failed`, `cancelled`

**Redis/TaskManager mirrors:** same strings after sync; created as `pending` then immediately set to `queued`.

### `doc_process_*`

| Field | Source of truth |
|-------|-----------------|
| All task fields | Redis `task:{task_id}` (24h TTL) |
| `owner_id` | Redis metadata |

List/detail APIs read TaskManager only. Expired Redis keys mean the task no longer exists.

### `pdf_convert_*` / `image_upload_*`

| Field | Source of truth |
|-------|-----------------|
| `status` | In-memory `ExecutorManager` Future (derived at query time) |
| `owner_id` | `TaskOwnerRegistry` memory cache only |

Short-lived; lost on process restart or when Future history is trimmed (>60 completed).

## Status sync rules (quotation)

1. **Create:** PG `queued` + Redis `queued` (via `TaskManagerStateAdapter.create_task`).
2. **Phase1 → awaiting_approval:** PG status + `awaiting_approval_at`; Redis `update_status('awaiting_approval')`.
3. **Cancel:** PG `cancelled`; Redis `update_status('cancelled')` (not `failed`).
4. **Approve → Phase2:** PG `running`, clear `awaiting_approval_at`; Redis `update_status('running')`.
5. **Startup recovery:** PG stale `running` → `queued`; Redis synced via background task.

## Retention (quotation_tasks)

- **Global count > 100:** delete oldest terminal rows (`completed` / `failed` / `cancelled`) until total ≤ 50.
- **`awaiting_approval` > 24h:** hard purge (files + DB + Redis + caches).
- Non-terminal tasks (`queued`, `running`, `awaiting_approval` within TTL) are never removed by count-based retention.
