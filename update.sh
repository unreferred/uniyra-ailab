#!/usr/bin/env bash
# Uniyra AI Lab — update or rollback the prebuilt (GHCR) installation.
#
# Usage:
#   ./update.sh                        # pull/restart the version currently pinned in .ailab_version (no silent upgrades)
#   ./update.sh --version v1.0.1       # explicitly upgrade to a specific release
#   ./update.sh --rollback             # return to the previous known-good version
#
# Never performs destructive volume operations. Existing .env values preserved.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

COMPOSE_FILE="docker-compose.prebuilt.yml"
STATE_FILE=".ailab_version"
PREV_FILE="${STATE_FILE}.previous"
DEFAULT_VERSION="latest"

fail() { echo "ERROR: $*" >&2; exit 1; }
usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//'; }

MODE="current"
TARGET=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version|-v) [[ -n "${2:-}" ]] || fail "--version requires a value"; MODE="explicit"; TARGET="$2"; shift 2 ;;
    --rollback) MODE="rollback"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1 (see --help)" ;;
  esac
done

echo "==> Prerequisite checks"
command -v docker >/dev/null 2>&1 || fail "docker not found."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin missing."
[[ -f .env ]] || fail "No .env found. Run ./install.sh first."
[[ -f "${STATE_FILE}" ]] || fail "No ${STATE_FILE} found. Run ./install.sh first."

CURRENT="$(cat "${STATE_FILE}")"

case "${MODE}" in
  current)
    TARGET="${CURRENT}"
    echo "==> Updating current pinned version: ${TARGET}"
    ;;
  explicit)
    if [[ "${TARGET}" == "${CURRENT}" ]]; then
      echo "Already running ${TARGET}; nothing to do."
      exit 0
    fi
    echo "==> Explicit upgrade requested: ${CURRENT} -> ${TARGET}"
    read -r -p "Proceed with upgrade to ${TARGET}? [y/N] " answer
    [[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Aborted."; exit 0; }
    ;;
  rollback)
    [[ -f "${PREV_FILE}" ]] || fail "No previous version recorded (${PREV_FILE} missing); cannot roll back."
    TARGET="$(cat "${PREV_FILE}")"
    if [[ "${TARGET}" == "latest" ]]; then
      fail "Previous version was recorded as the mutable 'latest' (legacy state). Rollback must be pinned: find the last known-good release at https://github.com/unreferred/uniyra-ailab/releases and run ./update.sh --version vX.Y.Z"
    fi
    echo "==> Rollback requested: ${CURRENT} -> ${TARGET}"
    ;;
esac

if [[ "${TARGET}" == "latest" ]]; then
  echo "WARNING: 'latest' tracks the newest stable release; prefer pinning (./update.sh --version vX.Y.Z)."
fi

echo "==> Backup reminder"
echo "    MongoDB:   docker compose -f ${COMPOSE_FILE} exec mongo mongodump --archive --gzip > mongo-backup-$(date +%Y%m%d-%H%M%S).gz"
echo "    .env:      keep a private copy (contains ENCRYPTION_KEY)."
read -r -p "Have you backed up MongoDB data before updating? [y/N] " answer
[[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Aborted. Back up first (commands above)."; exit 0; }

echo "==> Pulling version ${TARGET}"
export AILAB_VERSION="${TARGET}"
docker compose -f "${COMPOSE_FILE}" pull || fail "Could not pull '${TARGET}'. Check https://github.com/unreferred/uniyra-ailab/releases for published versions."

# Resolve a mutable tag to the concrete release it points to, so updates and
# rollbacks never depend on 'latest' after resolution, and never mix a
# frontend/backend pair from different releases.
if [[ "${TARGET}" == "latest" ]]; then
  resolve_label() {
    local resolved
    resolved="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "ghcr.io/unreferred/uniyra-ailab-$1:${TARGET}" 2>/dev/null || true)"
    if [[ "${resolved}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then resolved="v${resolved}"; fi
    [[ "${resolved}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || resolved=""
    printf '%s\n' "${resolved}"
  }
  RESOLVED_FRONTEND="$(resolve_label frontend)"
  RESOLVED_BACKEND="$(resolve_label backend)"
  if [[ -z "${RESOLVED_BACKEND}" || -z "${RESOLVED_FRONTEND}" ]]; then
    fail "Could not resolve 'latest' to a concrete version (missing org.opencontainers.image.version label). Pin explicitly: ./update.sh --version vX.Y.Z"
  fi
  if [[ "${RESOLVED_FRONTEND}" != "${RESOLVED_BACKEND}" ]]; then
    fail "Inconsistent 'latest' pair: frontend=${RESOLVED_FRONTEND}, backend=${RESOLVED_BACKEND}. This is a partial publication upstream; DO NOT update. Pin explicitly: ./update.sh --version vX.Y.Z"
  fi
  echo "Resolved 'latest' -> ${RESOLVED_BACKEND}"
  TARGET="${RESOLVED_BACKEND}"
  export AILAB_VERSION="${TARGET}"
fi

echo "==> Recreating containers"
docker compose -f "${COMPOSE_FILE}" up -d

echo "==> Health checks"
if ! docker compose -f "${COMPOSE_FILE}" up --wait --wait-timeout 180; then
  echo "--- Recent backend logs ---"
  docker compose -f "${COMPOSE_FILE}" logs --tail 50 backend || true
  echo "Roll back with: ./update.sh --rollback"
  fail "New version ${TARGET} is not healthy."
fi

# Success: shift version history (old pinned version becomes the rollback target).
if [[ "${TARGET}" != "${CURRENT}" ]]; then
  echo "${CURRENT}" > "${PREV_FILE}"
fi
echo "${TARGET}" > "${STATE_FILE}"

# Keep .env pinned to the running version so manual recreations never default
# back to the mutable 'latest' (script exports override .env).
if grep -q '^AILAB_VERSION=' .env; then
  sed -i "s/^AILAB_VERSION=.*/AILAB_VERSION=${TARGET}/" .env
else
  printf '\n# Pinned image version (managed by install.sh/update.sh; do not set to latest).\nAILAB_VERSION=%s\n' "${TARGET}" >> .env
fi

echo
echo "Update complete. Running version: ${TARGET}."
echo "Rollback available with: ./update.sh --rollback  (returns to ${CURRENT})"
