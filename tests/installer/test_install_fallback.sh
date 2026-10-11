#!/usr/bin/env bash
# Installer tests (INSTALL-01): anonymous GHCR fallback on auth errors.
#
# Uses a mocked `docker` on PATH. Never touches the network, the real Docker
# daemon, or the user's Docker credentials. Run: ./tests/installer/test_install_fallback.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d /tmp/uniyra-installer-tests.XXXXXX)"
trap 'rm -rf "${TEST_ROOT}"' EXIT
PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Mock docker: records invocations and simulates success/failure driven by
# MOCK_COMPOSE_PULL_FAIL (error text) and MOCK_ANON_PULL_FAIL=1.
# ---------------------------------------------------------------------------
MOCK_BIN="${TEST_ROOT}/bin"
mkdir -p "${MOCK_BIN}"
cat > "${MOCK_BIN}/docker" <<'EOF'
#!/usr/bin/env bash
LOG="${MOCK_LOG:?}"
echo "docker $*" >> "${LOG}"
if [[ "$1" == "pull" ]]; then
  echo "image=$2 DOCKER_CONFIG=${DOCKER_CONFIG:-unset}" >> "${MOCK_PULL_LOG:?}"
  if [[ -n "${DOCKER_CONFIG:-}" && -d "${DOCKER_CONFIG}" ]]; then
    echo "config_mode=$(stat -c '%a' "${DOCKER_CONFIG}")" >> "${MOCK_PULL_LOG}"
  fi
  if [[ "${MOCK_ANON_PULL_FAIL:-}" == "1" ]]; then
    echo "ERROR: denied: requested access to the resource is denied" >&2
    exit 1
  fi
  exit 0
fi
case "$1" in
  info) exit 0 ;;
  compose)
    if [[ "${MOCK_COMPOSE_PULL_FAIL:-}" != "" && "$*" == *" pull" ]]; then
      echo "ERROR: ${MOCK_COMPOSE_PULL_FAIL}" >&2
      exit 1
    fi
    exit 0 ;;
  image)
    if [[ "$2" == "inspect" ]]; then
      echo "v0.1.0"
      exit 0
    fi
    exit 0 ;;
esac
exit 0
EOF
chmod +x "${MOCK_BIN}/docker"

# ---------------------------------------------------------------------------
new_fixture() {  # $1 = name; sets FIXTURE, MOCK_LOG, MOCK_PULL_LOG
  FIXTURE="${TEST_ROOT}/$1"
  mkdir -p "${FIXTURE}"
  cp "${REPO_DIR}/install.sh" "${REPO_DIR}/update.sh" "${FIXTURE}/"
  : > "${FIXTURE}/docker-compose.prebuilt.yml"
  cat > "${FIXTURE}/.env.example" <<'EOF'
APP_PORT=8080
EOF
  MOCK_LOG="${FIXTURE}/docker.log"
  MOCK_PULL_LOG="${FIXTURE}/pull.log"
  : > "${MOCK_LOG}"
  : > "${MOCK_PULL_LOG}"
}

run_install() {  # full command, e.g. run_install env -u DOCKER_CONFIG "${FIXTURE}/install.sh" --version v1.0.0
  ( cd "${FIXTURE}" && PATH="${MOCK_BIN}:${PATH}" MOCK_LOG="${MOCK_LOG}" \
    MOCK_PULL_LOG="${MOCK_PULL_LOG}" "$@" ) > "${FIXTURE}/out.log" 2>&1
}

run_update() {  # extra args passed through; stdin from $1
  local stdin_file="$1"; shift
  ( cd "${FIXTURE}" && PATH="${MOCK_BIN}:${PATH}" MOCK_LOG="${MOCK_LOG}" \
    MOCK_PULL_LOG="${MOCK_PULL_LOG}" "${FIXTURE}/update.sh" "$@" ) \
    < "${stdin_file}" > "${FIXTURE}/out.log" 2>&1
}

check() {  # $1 description, $2 command (evaluated)
  if eval "$2"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: $1 (fixture: ${FIXTURE})"
    echo "--- out.log ---"; sed -n '1,60p' "${FIXTURE}/out.log" 2>/dev/null
  fi
}

assert_cleaned() {
  if compgen -G '/tmp/uniyra-ghcr-anon.*' > /dev/null || compgen -G '/tmp/uniyra-pulllog.*' > /dev/null; then
    FAIL=$((FAIL + 1)); echo "FAIL: temporary Docker config / pull log dirs left behind"
  else
    PASS=$((PASS + 1))
  fi
}

# ===========================================================================
echo "== Test 1: clean credentials, authenticated pull succeeds (no fallback)"
new_fixture t1
run_install env -u DOCKER_CONFIG "${FIXTURE}/install.sh"
check "install exits 0" "test \$? -eq 0"
check "no anonymous docker pull attempted" "! grep -q '^image=' '${MOCK_PULL_LOG}'"
check "version recorded" "[[ \"\$(cat '${FIXTURE}/.ailab_version')\" == 'v0.1.0' ]]"
check "AILAB_VERSION pinned in .env" "grep -q '^AILAB_VERSION=v0.1.0' '${FIXTURE}/.env'"
assert_cleaned

# ===========================================================================
echo "== Test 2: stale GHCR credentials -> anonymous fallback succeeds"
new_fixture t2
printf 'auths-stub\n' > "${FIXTURE}/docker-config-sentinel"
export DOCKER_CONFIG="${FIXTURE}/docker-config-sentinel"
MOCK_COMPOSE_PULL_FAIL="denied: unauthorized" run_install env DOCKER_CONFIG="${DOCKER_CONFIG}" "${FIXTURE}/install.sh"
unset DOCKER_CONFIG
check "install exits 0 despite auth failure" "test \$? -eq 0"
check "anonymous pull attempted for backend and frontend" \
  "[[ \$(grep -c '^image=ghcr.io/unreferred/uniyra-ailab-' '${MOCK_PULL_LOG}') -eq 2 ]]"
check "fallback used a temporary DOCKER_CONFIG (not the user's)" \
  "! grep -q \"DOCKER_CONFIG=${FIXTURE}/docker-config-sentinel\" '${MOCK_PULL_LOG}'"
check "temporary config dir had restrictive permissions" "grep -q 'config_mode=700' '${MOCK_PULL_LOG}'"
check "user's Docker config unchanged" "[[ \"\$(cat '${FIXTURE}/docker-config-sentinel')\" == 'auths-stub' ]]"
check "version recorded after fallback" "[[ \"\$(cat '${FIXTURE}/.ailab_version')\" == 'v0.1.0' ]]"
check "no 'docker logout' invoked" "! grep -q 'logout' '${MOCK_LOG}'"
assert_cleaned

# ===========================================================================
echo "== Test 3: stale credentials AND anonymous pull fails -> clear error"
new_fixture t3
export DOCKER_CONFIG="${TEST_ROOT}/t3-sentinel"
printf 'sentinel\n' > "${DOCKER_CONFIG}"
MOCK_COMPOSE_PULL_FAIL="unauthorized: authentication required" MOCK_ANON_PULL_FAIL=1 \
  run_install env DOCKER_CONFIG="${DOCKER_CONFIG}" "${FIXTURE}/install.sh" || RC=$?
RC=${RC:-0}
unset DOCKER_CONFIG
check "install exits non-zero" "test ${RC} -ne 0"
check "error mentions docker logout remediation" "grep -q 'docker logout ghcr.io' '${FIXTURE}/out.log'"
check "error points to releases page" "grep -q 'github.com/unreferred/uniyra-ailab/releases' '${FIXTURE}/out.log'"
check "user's Docker config unchanged" "[[ \"\$(cat '${TEST_ROOT}/t3-sentinel')\" == 'sentinel' ]]"
check "state file not written on failure" "! test -f '${FIXTURE}/.ailab_version'"
assert_cleaned

# ===========================================================================
echo "== Test 4: missing tag (non-auth error) -> NO anonymous fallback"
new_fixture t4
MOCK_COMPOSE_PULL_FAIL="manifest unknown" run_install env -u DOCKER_CONFIG "${FIXTURE}/install.sh" --version v9.9.9 || RC=$?
RC=${RC:-0}
check "install exits non-zero" "test ${RC} -ne 0"
check "no anonymous fallback for non-auth errors" "! grep -q '^image=' '${MOCK_PULL_LOG}'"
check "error reported unchanged" "grep -qi 'manifest unknown' '${FIXTURE}/out.log'"
assert_cleaned

# ===========================================================================
echo "== Test 5: explicit pinned version install with fallback"
new_fixture t5
MOCK_COMPOSE_PULL_FAIL="denied: forbidden" run_install env -u DOCKER_CONFIG "${FIXTURE}/install.sh" --version v0.1.0
check "pinned install exits 0 with fallback" "test \$? -eq 0"
check "fallback pulled pinned tag" "grep -q 'uniyra-ailab-backend:v0.1.0' '${MOCK_PULL_LOG}'"
check "state file is pinned version" "[[ \"\$(cat '${FIXTURE}/.ailab_version')\" == 'v0.1.0' ]]"
assert_cleaned

# ===========================================================================
echo "== Test 6: update with stale credentials -> fallback succeeds"
new_fixture t6
printf 'APP_PORT=8080\nAILAB_VERSION=v0.1.0\n' > "${FIXTURE}/.env"
printf 'v0.1.0\n' > "${FIXTURE}/.ailab_version"
mkdir -p "${TEST_ROOT}/t6"; printf 'y\ny\n' > "${TEST_ROOT}/t6/y"
MOCK_COMPOSE_PULL_FAIL="unauthorized" run_update "${TEST_ROOT}/t6/y"
check "update exits 0 with fallback" "test \$? -eq 0"
check "anonymous pull attempted" "grep -q 'uniyra-ailab-frontend:v0.1.0' '${MOCK_PULL_LOG}'"
check "previous version preserved for rollback" "[[ \"\$(cat '${FIXTURE}/.ailab_version')\" == 'v0.1.0' ]]"
assert_cleaned

# ===========================================================================
echo "== Test 7: rollback with stale credentials -> fallback succeeds"
new_fixture t7
printf 'APP_PORT=8080\nAILAB_VERSION=v0.2.0\n' > "${FIXTURE}/.env"
printf 'v0.2.0\n' > "${FIXTURE}/.ailab_version"
printf 'v0.1.0\n' > "${FIXTURE}/.ailab_version.previous"
printf 'y\ny\n' > "${FIXTURE}/stdin"
MOCK_COMPOSE_PULL_FAIL="403 Forbidden" run_update "${FIXTURE}/stdin" --rollback
check "rollback exits 0 with fallback" "test \$? -eq 0"
check "fallback pulled rollback version" "grep -q 'uniyra-ailab-backend:v0.1.0' '${MOCK_PULL_LOG}'"
check "state file now v0.1.0" "[[ \"\$(cat '${FIXTURE}/.ailab_version')\" == 'v0.1.0' ]]"
check "rollback target preserved" "[[ \"\$(cat '${FIXTURE}/.ailab_version.previous')\" == 'v0.2.0' ]]"
check ".env re-pinned to rolled-back version" "grep -q '^AILAB_VERSION=v0.1.0' '${FIXTURE}/.env'"
assert_cleaned

# ===========================================================================
echo "== Test 8: update fails (both pulls fail) -> .env and state untouched"
new_fixture t8
printf 'APP_PORT=8080\nAILAB_VERSION=v0.1.0\nJWT_SECRET=x\n' > "${FIXTURE}/.env"
printf 'v0.1.0\n' > "${FIXTURE}/.ailab_version"
printf 'y\ny\n' > "${FIXTURE}/stdin"
MOCK_COMPOSE_PULL_FAIL="unauthorized" MOCK_ANON_PULL_FAIL=1 run_update "${FIXTURE}/stdin" || RC=$?
RC=${RC:-0}
check "update exits non-zero" "test ${RC} -ne 0"
check "state file untouched" "[[ \"\$(cat '${FIXTURE}/.ailab_version')\" == 'v0.1.0' ]]"
check ".env untouched" "grep -q '^AILAB_VERSION=v0.1.0' '${FIXTURE}/.env'"
check "actionable error printed" "grep -q 'docker logout ghcr.io' '${FIXTURE}/out.log'"
assert_cleaned

# ===========================================================================
echo
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
