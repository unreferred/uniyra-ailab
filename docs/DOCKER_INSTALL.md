# Uniyra AI Lab — Self-Hosted Docker Installation

Run the full stack (frontend + backend + MongoDB) with Docker only. No host
Node.js, Python, or MongoDB required. Images are built locally; nothing is
published or pushed anywhere.

> **Prebuilt alternative:** to install from published GHCR images instead of
> building from source, see `docs/PREBUILT_INSTALL.md` (`./install.sh`).

## Architecture

| Service    | Image                        | Exposure                                      |
|------------|------------------------------|-----------------------------------------------|
| `frontend` | built from `frontend/` (nginx) | **only public entrypoint**: `http://localhost:APP_PORT` |
| `backend`  | built from `backend/` (uvicorn) | internal network only, reached via nginx `/api/` proxy |
| `mongo`    | `mongo:7.0`                  | internal network only, no published port      |

The browser calls the same origin (`/api/...`); nginx proxies to the backend,
preserving cookies, CSRF, CORS, and long-running streaming responses.
MongoDB data lives in the `mongo_data` volume; attachments in `attachments`.

## Prerequisites

- Docker Engine 24+ with the Compose v2 plugin (`docker compose version`)
- ~4 GB free disk for images and data
- Bash-compatible shell (for the secret generation commands)

## 1. Clone and configure

```bash
git clone <repository-url> uniyra-ailab && cd uniyra-ailab
cp .env.example .env
```

Edit `.env` and fill in the two required secrets:

```bash
# JWT signing secret (>= 32 random characters; length is NOT enforced by the app)
openssl rand -hex 32

# Fernet key for stored credentials: 32 random bytes, url-safe base64 (44 chars).
# The backend refuses to start with any other format.
openssl rand -base64 32 | tr '+/' '-_'
```

Paste each output after `JWT_SECRET=` / `ENCRYPTION_KEY=` in `.env`. Compose
refuses to start if either is empty. Do not reuse secrets across installations.

All other defaults work out of the box on `http://localhost:8080`. To reach
the app from another machine, a hostname, or behind an https proxy, you **must**
add that exact origin to `CORS_ORIGINS` in `.env`, e.g.
`CORS_ORIGINS=http://192.168.1.50:8080`, then `docker compose up -d`. Without
it, login works but every write request fails with `403 CSRF validation failed`.
The frontend always calls `/api` on the page's own origin, so any host name,
port or scheme works without rebuilding.

There are **no default credentials**. Admin seeding stays off
(`ENABLE_ADMIN_SEED=false`) unless you opt in on a fresh database.

## 2. First installation

```bash
docker compose build          # frontend + backend images
docker compose up -d          # starts mongo -> backend -> frontend
docker compose ps             # all three should show "healthy"/"running"
```

Then open **http://localhost:8080** and register the first account in the UI.

Optional: pre-build the code-interpreter sandbox image
(`docker build -t omnilab-code-interpreter:latest backend/docker/code-interpreter`) —
note the sandbox itself is disabled by default in Docker (see Limitations).

## 3. Startup / shutdown / logs

```bash
docker compose up -d          # start
docker compose stop           # stop (data preserved)
docker compose start          # resume
docker compose logs -f backend    # follow one service
docker compose logs -f            # follow everything
docker compose down           # remove containers; volumes preserved
```

Never run `docker compose down -v` unless you intend to delete all data.

## 4. Updating from source

```bash
git pull
docker compose build
docker compose up -d
```

MongoDB schema changes are handled by the backend at startup (idempotent
index creation in `app/database.py`); no manual migration step is needed.

## 5. Backup and restore

MongoDB data volume: `uniyra-ailab_mongo_data` (name prefix from the Compose
project name in `docker-compose.yml`). Backup while running:

```bash
docker compose exec mongo mongodump --archive --gzip > mongo-backup.gz
```

Restore (**destructive**: `--drop` replaces existing collections with the
backup contents; stop the backend first with `docker compose stop backend`
and start it again afterwards). To rehearse safely, restore into a scratch
database instead: add `--nsFrom 'omnilab.*' --nsTo 'restoretest.*'` and drop
`restoretest` afterwards.

```bash
docker compose exec -T mongo mongorestore --archive --gzip --drop < mongo-backup.gz
```

Also back up `.env` (contains the encryption key; keep the backup private — without it, stored tool
credentials cannot be decrypted) and the `attachments` volume:

```bash
docker run --rm -v uniyra-ailab_attachments:/data -v "$PWD":/backup \
  alpine tar czf /backup/attachments-backup.tar.gz -C /data .
```

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `backend` exits with `JWT_SECRET`/`ENCRYPTION_KEY` error | `.env` missing or secrets empty; re-run the generation commands in step 1 |
| `frontend` unhealthy, logs show `host not found in upstream` | backend not up yet; `docker compose up -d` waits for backend health automatically — check `docker compose logs backend` |
| Login page loads but requests fail (CORS/CSRF) | You are accessing from a non-localhost origin; add `http://<host>:<APP_PORT>` to `CORS_ORIGINS` in `.env` and `docker compose up -d` again |
| Port 8080 already in use | Change `APP_PORT` in `.env`, then `docker compose up -d` (also update `CORS_ORIGINS` if you use it) |
| Mongo data gone after restart | You ran `docker compose down -v`; data lives in the named volume and survives `down`/`stop`/recreate otherwise |
| Code interpreter tool reports unavailable | Expected: disabled by default in Docker (see below) |

## Limitations & notes

- **MongoDB has no authentication** (the backend's `MONGO_URL` carries no
  credentials and this release does not change that). Residual risk: any
  process that can reach `mongo:27017` on the Compose `internal` network, i.e.
  any other container attached to that network or code running inside the
  backend/mongo containers, has full read/write access to all data, including
  encrypted credentials, and could rewrite users or sessions. It is not
  reachable from the host or LAN (no published port). Do **not** add a `ports:`
  mapping to `mongo`, and do not attach untrusted containers to this network.
- **Code interpreter disabled by default**: the Docker socket is **not**
  mounted. The sandbox spawns sibling containers on the *host* daemon, so the
  stock backend image (no `docker` CLI, non-root user, no socket) cannot run it
  and the tool reports "unavailable". Enabling it is an unsupported advanced
  setup: you must build a backend image containing the docker CLI, mount
  `/var/run/docker.sock` and a shared host staging directory, and build
  `omnilab-code-interpreter:latest`. **Security implication:** access to the
  Docker socket is root-equivalent on the host. Anyone who can make the backend
  execute attacker-influenced code (a compromised backend, a prompt-injected
  agent, a malicious user on a multi-user install) can start privileged
  containers, mount host paths and take over the machine. Enable only on a
  single-user, trusted host.
- **HTTP entrypoint**: cookies are sent without the `Secure` flag so plain
  http works locally. Behind an https reverse proxy, set `COOKIE_SECURE=true`.
- **Reverse proxy / TLS**: the container serves plain HTTP only. Terminate TLS
  in front of it, forward `/` to `APP_PORT`, keep response buffering off for
  `/api/` (streamed runs) and set `COOKIE_SECURE=true` and `CORS_ORIGINS` to the
  public https origin. No WebSockets are used (streaming is HTTP/SSE).
  **Forwarding headers**: the proxy must pass the browser's `Host`, `Origin`
  and `Referer` request headers through to the backend unchanged — the CSRF
  check compares `Origin`/`Referer` against `CORS_ORIGINS`, so a proxy that
  strips or rewrites them (e.g. setting `proxy_set_header Origin ""`) breaks
  every state-changing request with `403 CSRF validation failed`. `Host` may be
  rewritten to the upstream address (the backend does not depend on it), but
  do not strip `Origin`/`Referer`.
- **Secrets**: `JWT_SECRET`/`ENCRYPTION_KEY` are injected at runtime only; they
  are not in the images, build args or frontend bundle. `.env` is git-ignored.
- Provider API keys (OpenAI, Gemini, TinyFish, ...) are **not** environment
  variables here — they are configured per user inside the app (Registry /
  Tool credentials) and encrypted with `ENCRYPTION_KEY` in MongoDB.
