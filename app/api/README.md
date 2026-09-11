# API package layout and URL conventions

## Directory rule (v1)

- All versioned route modules live under `app/api/v1/` in a **flat** structure (one module per area). Do not add a second `endpoints/` mirror; new routers are new files in `v1/`.
- Cross-cutting code (tag names, path prefixes, router registration) is centralized in:
  - `app/api/v1/tags.py` — OpenAPI `tags` strings and `openapi.json` tag metadata
  - `app/api/v1/prefixes.py` — first path segment under `API_V1_STR` (e.g. `/api/v1`)
  - `app/api/v1/registry.py` — `include_router` list (canonical and legacy alias mounts)

## Canonical path prefixes (under `API_V1_STR`, default `/api/v1`)

| Area | Prefix | Notes |
|------|--------|--------|
| Auth | `/auth` | |
| Example | `/example` | |
| File storage | `/files` | |
| Quotation | `/quotation` | |
| Document async tasks + task WebSocket | `/document-tasks` | Preferred; avoids clashing with Swagger UI at `/api/v1/docs` |
| OCR (image upload + PDF to image) | `/ocr` | Replaces split `/image2url` and `/pdf2image` for new clients |
| RAG retriever | `/retriever` | |
| Chat summary | `/chat-summary` | |
| Closing form | `/closing-form` | |
| Context compression | `/context-compression` | |
| SQL Server | `/sqlserver` | |

## Legacy aliases (deprecated)

Routers are also mounted for backward compatibility:

- `/docs` — same routes as `/document-tasks` (use `/document-tasks` for new code)
- `/image2url` — same as `/ocr` for image upload routes
- `/pdf2image` — same as `/ocr` for PDF routes

## Client migration

- Prefer `document-tasks` and `ocr` in frontends, scripts, and API clients. Legacy paths remain until a dedicated removal pass.
