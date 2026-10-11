#!/usr/bin/env bash
# Shared strict semver helpers for release publishing and tests.
# Sourced by .github/workflows/release.yml (promote job) and by
# focused release-ordering simulations. Pure bash, no external deps.

# semver_lt A B — returns 0 (true) iff A < B as strict stable semver (vX.Y.Z).
# Compares numeric components, never lexicographic strings
# (so v1.10.0 > v1.2.0 and v1.2.0 < v1.2.10).
semver_lt() {
  local a="${1#v}" b="${2#v}"
  if ! [[ "${a}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "${b}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "semver_lt: non-strict-semver input: '${1}' / '${2}'" >&2
    return 2
  fi
  local a1 a2 a3 b1 b2 b3
  IFS=. read -r a1 a2 a3 <<< "${a}"
  IFS=. read -r b1 b2 b3 <<< "${b}"
  if (( a1 != b1 )); then (( a1 < b1 )); return; fi
  if (( a2 != b2 )); then (( a2 < b2 )); return; fi
  (( a3 < b3 ))
}

# newest_stable TAG others... — prints the highest strict semver among TAG and
# the given tags; tags not matching v#.#.# are ignored.
newest_stable() {
  local best="$1"; shift
  local t
  for t in "$@"; do
    [[ "${t}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
    semver_lt "${best}" "${t}" && best="${t}"
  done
  printf '%s\n' "${best}"
}
