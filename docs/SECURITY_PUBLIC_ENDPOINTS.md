# API surface: authentication and sensitivity

This document inventories HTTP routes by authentication requirement. Paths are relative to the app prefix (typically `/api/v1` plus router prefix). WebSocket and `main` routes are included.

**Sensitivity legend**

- **Low**: No or minimal business data; safe for health checks and demos.
- **Medium**: Unauthenticated or weakly protected; information disclosure or abuse if exposed.
- **High**: Business or PII; must require strong auth and authorization.

## Unauthenticated (no `Bearer` JWT on route dependency)

| Path | Method | Notes | Sensitivity |
|------|--------|--------|-------------|
| `/` | GET | App metadata | Low |
| `/api/v1/health` | GET | Service health | Low |
| `/api/v1/auth/login` | POST | Issues JWT | Medium |
| `/api/v1/auth/register` | POST | Creates user | Medium |
| `/api/v1/example/hello` | GET | Demo; **404 when `ENVIRONMENT` is `production` / `prod`** | Low |
| `/api/v1/example/hello/{name}` | GET | Same as above | Low |
| `/api/v1/example/db-example` | GET | Same as above (DB demo) | Medium |

`OPENAPI`/`docs` are disabled in production via `create_app()`.

## Authenticated with optional alternative

| Path | Method | Auth mechanism | Sensitivity |
|------|--------|----------------|-------------|
| `/api/v1/metrics` | GET | `X-API-KEY` when `METRICS_REQUIRE_API_KEY` | High |
| `/api/v1/document-tasks/ws/{task_id}` | WebSocket | `token` query (JWT) + task ownership | High |
| `/api/v1/docs/ws/{task_id}` | WebSocket | Legacy alias; same as `document-tasks` | High |

## JWT required (`Depends(get_current_user)` or stricter `require_roles`)

All routes under: `/files`, `/quotation`, `/document-tasks` (HTTP; legacy `/docs`), `/ocr` (legacy `/image2url` and `/pdf2image` aliases), `/retriever`, `/chat-summary`, `/closing-form` (as per each handler), `/context-compression`, `/sqlserver` (U8/PDM queries).

Role-restricted examples:

- `GET /api/v1/document-tasks/ws/stats` (and legacy `GET /api/v1/docs/ws/stats`) — superuser only
- `GET /api/v1/auth/users` — superuser; user delete/role — superuser
- Parts of `closing-form` — admin or superuser per handler

Cross-user (same as WebSocket task policy: `admin` or `superuser` may act on other users; normal users are self-only):

- `POST /api/v1/chat-summary/create` and `GET /api/v1/chat-summary/query/{user_id}` — `admin` / `superuser` may use another user’s `user_id` (UUID or username) per handler logic; others restricted to their own aliases.
- `POST /api/v1/context-compression/compress` — `admin` / `superuser` may pass any business `user_id` for Dify; others restricted to their own aliases.

## Review checklist

- Re-audit when adding new routers in `app/api/v1/registry.py`.
- In production, disable or protect `/example` if not needed.
- Keep `TRUST_PROXY_HEADERS` off unless the reverse proxy is in `TRUSTED_PROXIES`.

Last updated: automated pass after SQL Server routes were secured with `get_current_user`.
