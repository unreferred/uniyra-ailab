#!/usr/bin/env bash
# Uniyra AI Lab — prebuilt installation from GitHub Container Registry.
#
# Usage:
#   ./install.sh                 # install the default documented release
#   AILAB_VERSION=v1.0.0 ./install.sh
#   ./install.sh --version v1.0.0
#
# Never destroys volumes or overwrites existing .env values.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

COMPOSE_FILE="docker-compose.prebuilt.yml"
DEFAULT_VERSION="latest"   # latest == newest approved stable GHCR release (see docs/PREBUILT_INSTALL.md)
STATE_FILE=".ailab_version"

fail() { echo "ERROR: $*" >&2; exit 1; }

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//'; }

VERSION="${AILAB_VERSION:-${DEFAULT_VERSION}}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version|-v) [[ -n "${2:-}" ]] || fail "--version requires a value"; VERSION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1 (see --help)" ;;
  esac
done

echo "==> Prerequisite checks"
command -v docker >/dev/null 2>&1 || fail "docker not found. Install Docker Engine 24+ and retry."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin missing (docker compose version failed)."
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable. Start Docker and retry (or add yourself to the docker group)."

echo "==> Environment initialization"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example."
else
  echo "Existing .env preserved."
fi

# Fill required secrets ONLY if absent, never overwrite existing values.
if ! grep -q '^JWT_SECRET=..*' .env; then
  printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
  echo "Generated JWT_SECRET."
fi
if ! grep -q '^ENCRYPTION_KEY=..*' .env; then
  printf 'ENCRYPTION_KEY=%s\n' "$(openssl rand -base64 32 | tr '+/' '-_')" >> .env
  echo "Generated ENCRYPTION_KEY."
fi

# Persist chosen version for update.sh/rollback is done AFTER the images are
# pulled and the version pair is resolved below, so the recorded state is
# always a concrete, verified version — never the mutable 'latest'.

echo "==> Pulling images (version: ${VERSION})"
export AILAB_VERSION="${VERSION}"

# Temporary credential-free Docker config, used ONLY if the authenticated
# GHCR pull fails with an authorization error (e.g. stale ~/.docker/config.json
# credentials from a previous 'docker login ghcr.io'). This never touches the
# user's real Docker credentials and never runs 'docker logout'.
ANON_DOCKER_CONFIG=""
PULL_LOG="$(mktemp "${TMPDIR:-/tmp}/uniyra-pulllog.XXXXXX")"
cleanup_pull() {
  rm -f "${PULL_LOG}"
  if [[ -n "${ANON_DOCKER_CONFIG}" && -d "${ANON_DOCKER_CONFIG}" ]]; then
    rm -rf "${ANON_DOCKER_CONFIG}"
  fi
}
trap cleanup_pull EXIT

pull_images() {
  if docker compose -f "${COMPOSE_FILE}" pull >"${PULL_LOG}" 2>&1; then
    return 0
  fi
  # Retry anonymously ONLY on authorization errors and ONLY for the two
  # public Uniyra GHCR images. Unrelated failures (missing tags, network
  # outages, invalid manifests, disk errors) are reported unchanged.
  if ! grep -Eqi 'unauthorized|authentication required|401|403|denied' "${PULL_LOG}"; then
    cat "${PULL_LOG}" >&2
    return 1
  fi
  echo "Authenticated pull failed with an authorization error; retrying the public Uniyra images anonymously."
  ANON_DOCKER_CONFIG="$(mktemp -d "${TMPDIR:-/tmp}/uniyra-ghcr-anon.XXXXXX")"
  chmod 700 "${ANON_DOCKER_CONFIG}"
  local img ok=0
  for img in backend frontend; do
    if DOCKER_CONFIG="${ANON_DOCKER_CONFIG}" docker pull "ghcr.io/unreferred/uniyra-ailab-${img}:${AILAB_VERSION}"; then
      echo "Pulled ghcr.io/unreferred/uniyra-ailab-${img}:${AILAB_VERSION} anonymously."
    else
      ok=1
    fi
  done
  if [[ "${ok}" -ne 0 ]]; then
    echo "ERROR: Could not pull version '${VERSION}' from GHCR (authenticated and anonymous attempts both failed)." >&2
    echo "       Check that the release exists: https://github.com/unreferred/uniyra-ailab/releases" >&2
    echo "       If you previously ran 'docker login ghcr.io' with an expired token, run 'docker logout ghcr.io' and retry." >&2
    cat "${PULL_LOG}" >&2
    return 1
  fi
  return 0
}

if ! pull_images; then
  if [[ "${VERSION}" != "latest" ]]; then
    fail "Version '${VERSION}' could not be pulled. Check that the release exists: https://github.com/unreferred/uniyra-ailab/releases"
  else
    fail "Could not pull 'latest'. Is the GHCR package public, or run with an explicit version: ./install.sh --version vX.Y.Z"
  fi
fi

# Resolve the pulled images to one concrete, coherent version pair.
# The version is read from the org.opencontainers.image.version OCI label
# baked in by the release workflow. Fail closed: never start a mixed
# frontend/backend pair and never record a mutable tag.
resolve_version() {
  local service="$1" img resolved
  img="ghcr.io/unreferred/uniyra-ailab-${service}:${AILAB_VERSION}"
  resolved="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "${img}" 2>/dev/null || true)"
  # metadata-action may record the version with or without the leading 'v'.
  if [[ "${resolved}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    resolved="v${resolved}"
  fi
  [[ "${resolved}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || resolved=""
  printf '%s\n' "${resolved}"
}

RESOLVED_FRONTEND="$(resolve_version frontend)"
RESOLVED_BACKEND="$(resolve_version backend)"
if [[ -z "${RESOLVED_BACKEND}" || -z "${RESOLVED_FRONTEND}" ]]; then
  fail "Could not resolve the installed version from the pulled images (missing org.opencontainers.image.version label). Run with an explicit version instead: ./install.sh --version vX.Y.Z"
fi
if [[ "${RESOLVED_FRONTEND}" != "${RESOLVED_BACKEND}" ]]; then
  fail "Inconsistent image pair for '${AILAB_VERSION}': frontend=${RESOLVED_FRONTEND}, backend=${RESOLVED_BACKEND}. Refusing to install a mixed set. Pin an explicit version: ./install.sh --version vX.Y.Z"
fi
if [[ "${RESOLVED_BACKEND}" != "${AILAB_VERSION}" ]]; then
  echo "Resolved '${AILAB_VERSION}' to pinned release ${RESOLVED_BACKEND}."
fi
export AILAB_VERSION="${RESOLVED_BACKEND}"

# Record the resolved version (previous version kept for rollback).
if [[ -f "${STATE_FILE}" && "$(cat "${STATE_FILE}")" != "${AILAB_VERSION}" ]]; then
  PREV="$(cat "${STATE_FILE}")"
  cp "${STATE_FILE}" "${STATE_FILE}.previous"
  echo "Previous version ${PREV} recorded in ${STATE_FILE}.previous."
fi
printf '%s\n' "${AILAB_VERSION}" > "${STATE_FILE}"

# Pin the resolved version in .env so later manual recreations (fresh shells,
# `docker compose -f docker-compose.prebuilt.yml up -d` / `pull`) never default
# back to the mutable 'latest'. Script exports override .env, so explicit
# install/update/rollback targets still win.
if grep -q '^AILAB_VERSION=' .env; then
  sed -i "s/^AILAB_VERSION=.*/AILAB_VERSION=${AILAB_VERSION}/" .env
else
  printf '\n# Pinned image version (managed by install.sh/update.sh; do not set to latest).\nAILAB_VERSION=%s\n' "${AILAB_VERSION}" >> .env
fi
echo "Pinned AILAB_VERSION=${AILAB_VERSION} in .env."

echo "==> Starting stack"
docker compose -f "${COMPOSE_FILE}" up -d

echo "==> Health checks (this can take ~1 minute on first start)"
if ! docker compose -f "${COMPOSE_FILE}" up --wait --wait-timeout 180; then
  echo "--- Recent backend logs ---"
  docker compose -f "${COMPOSE_FILE}" logs --tail 50 backend || true
  fail "Stack did not become healthy. Inspect logs above: docker compose -f ${COMPOSE_FILE} logs"
fi

echo
echo "Installation complete (version: ${AILAB_VERSION})."
echo "Open http://localhost:$(grep -E '^APP_PORT=' .env | cut -d= -f2 || echo 8080) and register the first account."
echo "Rollback after a future update: ./update.sh --rollback"
