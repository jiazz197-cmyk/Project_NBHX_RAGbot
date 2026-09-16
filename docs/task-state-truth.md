# Task state source-of-truth

This document describes where each async task type stores authoritative state in the
current codebase (document processing / OCR only).

## Task ID prefixes

| Prefix | Task type | Example |
|--------|-----------|---------|
| `doc_process_` | Document processing | `doc_process_20260407_182347_*` |
| `pdf_convert_` | OCR PDF conversion (short-lived) | `pdf_convert_*` |
| `image_upload_` | OCR image upload (short-lived) | `image_upload_*` |

## Field ownership

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

## Status sync rules (document tasks)

1. **Create:** Redis `pending` then `queued` via `TaskManagerStateAdapter.create_task`.
2. **Progress:** `TaskManager.update_task_progress` mirrors progress for WebSocket clients.
3. **Terminal:** `completed` / `failed` / `cancelled` is written by the task worker or cancel path.
4. **Retention:** task records follow the shared TaskManager retention policy; expired keys no longer exist.

## MinIO orphan reconcile

The reconciliation scheduler only scans production prefixes still used by document
processing / OCR (`temp/`, `images/`). Historical business prefixes are no longer
scanned and are never deleted by reconcile.
