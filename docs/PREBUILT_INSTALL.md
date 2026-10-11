# Uniyra AI Lab — Prebuilt (GHCR) Installation

Install the full stack from **published container images** on GitHub Container
Registry — no clone, no local build, no host Node.js/Python/MongoDB required.

| Service    | GHCR image                                        | Exposure |
|------------|---------------------------------------------------|----------|
| `frontend` | `ghcr.io/unreferred/uniyra-ailab-frontend`        | **only public entrypoint**: `http://localhost:APP_PORT` |
| `backend`  | `ghcr.io/unreferred/uniyra-ailab-backend`         | internal network only (via nginx `/api/` proxy) |
| `mongo`    | `mongo:7.0` (Docker Hub)                          | internal network only, no published port |

Both GHCR images are Apache-2.0 licensed, published by **Unreferred**, link
back to this repository via OCI labels, and contain **no secrets or API keys**
(all configuration is injected at runtime from `.env`).

## Source build vs prebuilt

| | Source build (`docker-compose.yml`) | Prebuilt (`docker-compose.prebuilt.yml`) |
|---|---|---|
| Images | built locally from source | pulled from GHCR |
| Best for | development, patching, air-gapped builds | production installs, fast updates |
| Commands | `docker compose ...` | `./install.sh`, `./update.sh` or `docker compose -f docker-compose.prebuilt.yml ...` |
| Version | current checkout | explicit release tag |

Both share the same architecture (single nginx entrypoint, internal-only
backend/Mongo), the same named volumes (`mongo_data`, `attachments`) and the
same `.env` configuration. The existing source-build workflow is unchanged.

## Version tags

- `vX.Y.Z` (e.g. `v1.0.0`) — immutable images for a stable GitHub Release.
  Tags are never overwritten or rebuilt; a re-release requires a new version.
- `latest` — always the newest **approved stable** release, tracked with a
  strict semantic-version comparison (not string sorting), so `v1.10.0` >
  `v1.2.0`. Prereleases and drafts never publish images and never move
  `latest`; publishing an **older** stable release after a newer one
  publishes only its `vX.Y.Z` images and refuses to downgrade `latest`.
- Pushes to `main` and pull requests **never** publish images.

## Production publishing model

- **Authorization action:** publishing a GitHub Release (non-draft,
  non-prerelease, stable `vX.Y.Z` tag) **is** the production authorization
  step. There is no other approval mechanism configured, and none is claimed.
  Anyone with permission to publish a Release can publish images.
- **Concurrency:** the workflow runs under a `release-production`
  concurrency group with `cancel-in-progress: false`, so two releases can
  never publish simultaneously and an in-flight publish is never cancelled
  mid-push.
- **Optional human gate:** maintainers can add stricter control by creating a
  GitHub Environment with required reviewers and assigning it to the
  workflow's `publish`/`promote` jobs (Settings → Environments). This is not
  configured by default.

## Registry protections and their limits (important)

- The workflow refuses to push a versioned tag that already exists (pre-push
  `docker manifest inspect` check) and verifies the published manifests
  afterwards.
- **This pre-push check is best-effort only** (a check-then-push race).
  GHCR tags are **mutable by default**: anyone whose token has
  `packages:write` can overwrite any tag, including `vX.Y.Z` and `latest`.
  There is currently no GHCR setting in the repository UI that makes tags
  immutable, so tag immutability **cannot be fully enforced from repository
  code**. Required compensating settings:
  - Keep the default: the workflow uses only the workflow-scoped
    `GITHUB_TOKEN` with `packages: write` (`contents: read`). Do **not**
    create PATs with `write:packages` for automation.
  - Package settings (each GHCR package → Package settings → Manage access):
    remove any collaborator/role with write access that is not required, and
    never grant anyone `delete` — deletion plus re-push is an overwrite path.
  - Restrict who can publish Releases (branch protection on tags /
    CODEOWNERS) since Release publishing is the authorization action.
  - Optionally enable a GitHub Environment with required reviewers for the
    publishing jobs (see above).
- **Partial publication:** the two `latest` tag updates (frontend + backend)
  are **not atomic**. The workflow therefore promotes `latest` only after
  both versioned images are verified on GHCR, verifies both `latest`
  digests against the release afterwards, and reports a loud, explicit
  error with remediation steps if promotion is partial. Installers
  (`install.sh`/`update.sh`) additionally refuse to run when the resolved
  `latest` pair is inconsistent.

## First-time installation

```bash
mkdir uniyra-ailab && cd uniyra-ailab          # or clone the repo
curl -fsSLO https://raw.githubusercontent.com/unreferred/uniyra-ailab/main/install.sh   # or use the repo copy
./install.sh                    # default: latest stable release
./install.sh --version v1.0.0   # pin an explicit version (recommended)
```

`install.sh` checks Docker/Compose, creates `.env` from `.env.example` if
missing (preserving an existing one), generates `JWT_SECRET`/`ENCRYPTION_KEY`
only if they are empty (never overwriting values you set), pulls the requested
version, starts the stack and waits for health checks. It never deletes
volumes and never silently picks a different version.

**Version resolution:** even when installed via `latest`, `install.sh` reads
the `org.opencontainers.image.version` OCI label from both pulled images,
verifies the frontend/backend pair is the same release, and records that
concrete `vX.Y.Z` in `.ailab_version` — never the mutable `latest`. A
mismatched or unlabelled pair fails the install (fail closed). Updates and
rollback therefore always operate on pinned versions. The resolved version is
also pinned as `AILAB_VERSION` in `.env`, so manual recreations
(`docker compose -f docker-compose.prebuilt.yml up -d` in a fresh shell) can
never silently fall back to the mutable `latest`.

Then open **http://localhost:8080** and register the first account.

### Anonymous fallback for stale GHCR credentials

The public Uniyra images on GHCR can be pulled anonymously. If the
authenticated pull fails with an authorization error (typically stale or
expired credentials left in `~/.docker/config.json` by a previous
`docker login ghcr.io`), `install.sh` and `update.sh` automatically retry
**only the two public Uniyra images** using a temporary, credential-free
Docker configuration:

- Your real Docker credentials are **never** read, modified, or deleted, and
  `docker logout` is never invoked by the scripts.
- The temporary configuration lives in a `mktemp -d` directory with `0700`
  permissions and is removed when the script exits (success or failure).
- The fallback applies **only** to `ghcr.io/unreferred/uniyra-ailab-frontend`
  and `ghcr.io/unreferred/uniyra-ailab-backend` — never to third-party or
  private images.
- Unrelated failures (missing tags, network outages, invalid manifests, disk
  errors) are reported unchanged and never trigger the fallback.
- If both the authenticated and anonymous attempts fail, the scripts print an
  actionable error (check the release exists; if you previously ran
  `docker login ghcr.io` with an expired token, run `docker logout ghcr.io`
  and retry) and leave `.env`, volumes, and any existing containers untouched.

## Manual updates

```bash
./update.sh --version v1.1.0    # explicit upgrade (asks for confirmation)
./update.sh                     # re-apply the currently pinned version only
```

`update.sh` refuses to run without a backup confirmation, pulls only the
requested version, and verifies health before recording it. It will **not**
silently upgrade to a newer release — a version must be stated explicitly.

## Backup before updates

```bash
docker compose -f docker-compose.prebuilt.yml exec mongo \
  mongodump --archive --gzip > mongo-backup-$(date +%Y%m%d-%H%M%S).gz
docker run --rm -v uniyra-ailab_attachments:/data -v "$PWD":/backup \
  alpine tar czf attachments-backup.tar.gz -C /data .
cp .env .env.backup             # keep private: contains ENCRYPTION_KEY
```

`update.sh` prints these commands and requires confirmation before proceeding.

## Rollback

Each successful upgrade records the previous version in `.ailab_version.previous`:

```bash
./update.sh --rollback
```

If the new version fails its health checks, `update.sh` reports the failure
and prints the rollback command. Data volumes are never touched by rollback.

## Release publishing (maintainers)

Images are published only by the `Release images` GitHub Actions workflow
(`.github/workflows/release.yml`), triggered **exclusively** by publishing a
GitHub Release:

1. Tag a stable semver release: `git tag v1.0.0 && git push origin v1.0.0`.
2. Create the GitHub Release from that tag (not draft, not prerelease).
   **This is the production authorization action** (see above).
3. The workflow gate rejects drafts, prereleases, and non-`vX.Y.Z` tags.
4. Before building, it checks GHCR and **fails safely** if the version tag
   already exists (tags are immutable; never overwrite — see the limits
   documented above).
5. It builds both images from the existing Dockerfiles, attaches OCI labels
   (`vendor=Unreferred`, `licenses=Apache-2.0`, `source=<repo>`), and pushes
   the `vX.Y.Z` tags.
6. Only after both versioned images are verified on GHCR, and only if the
   release is the newest stable semver, does the workflow re-tag both images
   as `latest`, verify both digests, and report loudly on any partial
   publication (the two `latest` updates are not atomic).

Permissions are the minimum needed (`contents: read`, `packages: write`) and
authentication uses the workflow-scoped `GITHUB_TOKEN` — no personal access
tokens. Regular commits/pushes to `main` cannot trigger publishing.

## Known security limitations (inherited from the DOCKER-01 source build)

See `docs/DOCKER_INSTALL.md` for the full list; the prebuilt install inherits
all of them:

- **MongoDB has no authentication.** It is unreachable from the host/LAN (no
  published port), but any container on the Compose `internal` network has
  full read/write access to all data. Never add a `ports:` mapping to `mongo`
  or attach untrusted containers to the network.
- **Code interpreter is disabled by default.** Enabling it requires mounting
  the host Docker socket, which is root-equivalent on the host.
- **Plain HTTP entrypoint.** Terminate TLS in a reverse proxy and set
  `COOKIE_SECURE=true` and `CORS_ORIGINS` accordingly.
- Secrets are runtime-injected only and are **not** in the images or bundles.
- Provider API keys are configured per user in the app and encrypted in
  MongoDB with `ENCRYPTION_KEY`.
