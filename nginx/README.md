# Nginx reverse proxy (chat stack)

The tracked configuration is [`nginx.conf.template`](nginx.conf.template). **`nginx/nginx.conf` is generated** and must not be committed (see root `.gitignore`). It contains the Dify app API key at render time.

## Render `nginx.conf`

```bash
./scripts/render_nginx_conf.sh
```

Requires **`DIFY_APP_API_KEY`** in the environment, or the same value as the Vite dev proxy in **`frontend/apps/chat/.env`** via `CHAT_PROXY_API_KEY` (or `CHAT_API_KEY` / `VITE_CHAT_API_KEY`).

Depends on **`envsubst`** (Debian/Ubuntu: `gettext-base`).

Docker example (after rendering on the host, or run `envsubst` in an entrypoint):

```bash
./scripts/render_nginx_conf.sh
docker run --rm -p 8080:8080 --add-host=host.docker.internal:host-gateway \
  -v "$(pwd)/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:alpine
```

Or pass the key only inside the container:

```bash
export DIFY_APP_API_KEY='your_dify_app_key'
envsubst '${DIFY_APP_API_KEY}' < nginx/nginx.conf.template > /tmp/nginx.conf
docker run --rm -p 8080:8080 --add-host=host.docker.internal:host-gateway \
  -v /tmp/nginx.conf:/etc/nginx/nginx.conf:ro nginx:alpine
```

## Key rotation (security)

Any key that was previously committed in git history should be **rotated in the Dify console** and replaced in your deployment secrets / `frontend/apps/chat/.env`. Never commit the new key into `nginx.conf.template` or any tracked file.

## Load balancing vs path routing

The default template defines **three upstream blocks**, each with **one** `server`. Nginx **does not** distribute load across multiple replicas until you add **multiple** `server` lines inside the same `upstream`.

Example: two FastAPI instances behind one upstream (round-robin by default):

```nginx
upstream yamato_backend {
    server 10.0.0.1:8000 max_fails=3 fail_timeout=30s;
    server 10.0.0.2:8000 max_fails=3 fail_timeout=30s;
    keepalive 64;
}
```

Notes for multi-instance setups:

- **WebSockets** (`/api/v1/document-tasks/ws/`; legacy `/api/v1/docs/ws/`): sticky routing may be required if the app assumes a fixed backend; consider `ip_hash` or `hash $cookie_... consistent;` on the upstream, or ensure the app is stateless with respect to which worker handles the socket.
- **Health**: open-source Nginx uses `max_fails` / `fail_timeout` on `server`; active health checks need a module or an external load balancer.
- **Dify / Vite**: scale those services the same way by adding more `server` entries to `yamato_dify` or `yamato_vite` (or terminate TLS on a cloud LB and point Nginx at internal addresses).

## Edge hardening (optional)

The template includes:

- **`limit_req`** on the Dify catch-all `location ^~ /api/v1/` (tune `rate` / `burst` in the template).
- **Commented `set_real_ip_from` / `real_ip_header`**: uncomment and set CIDRs when Nginx runs **behind** another trusted proxy so `X-Forwarded-For` and `$remote_addr` reflect the real client correctly.

TLS termination is not defined here; use your cloud load balancer, `certbot`, or another layer in front of this Nginx. If TLS terminates upstream, pass `X-Forwarded-Proto: https` and enable the real-IP block as appropriate.

## Smoke test

With Nginx listening on `8080` and backends reachable:

```bash
./scripts/nginx_smoke.sh
```

If `nginx/nginx.conf` is missing, the script runs `./scripts/render_nginx_conf.sh` unless `SKIP_NGINX_RENDER=1` is set.
