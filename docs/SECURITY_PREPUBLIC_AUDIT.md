# Pre-Public Security Audit — Uniyra AI Lab

**Repo:** `unreferred/uniyra-ailab` (target: PUBLIC) · **Branch:** `main` · **Commit audited:** `f80bf49` (initial & only commit)
**Date:** 2026-10-11 · **Method:** manual pattern audit (Gitleaks/Trufflehog not installed locally) over all 502 tracked files at `f80bf49` plus the working tree.

## 1. Executive Summary

One push blocker found: a Slack-shaped bot token literal in
`backend/tests/test_v5_redaction.py:19` **inside the initial commit blob**. The file is a
pure unit test of the production `app/services/redaction.py` module; every value in it is a
synthetic fixture. The working tree already contains an uncommitted sanitization (string
concatenation) that passes the same tests, but because the token literal is baked into
commit `f80bf49`, a working-tree edit alone will **not** unblock the push — the history
must be rewritten (amend/reword the initial commit) before the first push.

No real or active credentials were found anywhere else: no LLM provider keys, no GitHub
PATs, no AWS credentials, no JWT secrets, no database connection strings with credentials,
no encryption keys, no private keys, no webhook URLs, no Emergent platform credentials.
`.env` files are not tracked; only `.env.example` files with empty/placeholder values exist.

## 2. Findings

| # | Path | Line | Type | Classification | Production relation |
|---|------|------|------|----------------|---------------------|
| 1 | `backend/tests/test_v5_redaction.py` | 19 (committed `f80bf49`) | Slack bot token literal (`xoxb-…`, 2 segments, sequential digits/alphabet — synthetic format, 2-segment form is not even a valid live token) | **SANITIZE + ROTATE NOT REQUIRED** — but **push-blocking in committed history** | Test-only. Imports exclusively from `app.services.redaction` (production module — the module under test is production code; the test file itself is not in any runtime import graph). Pytest-discovered via `backend/pytest.ini`. |
| 2 | `backend/tests/test_v5_redaction.py` | 14–31 (also 20–24, 25–29) | OpenAI key, GitHub token, AWS key pair, HS256 JWT, RSA private key stub, bearer token | **KEEP** — synthetic fixtures (`ABCDEF…`, `IOSFODNN7EXAMPLE` = AWS docs example, 1-line fake PEM) | Test-only, same file. AWS values are the canonical AWS documentation examples; JWT has empty/absent claims; PEM is one truncated line. None match live-credential entropy. |
| 3 | `backend/tests/test_v5_credential_exclusion.py` | 15–16 | `sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX`, `ghp_abcdef…` | **KEEP** — synthetic | Test-only. |
| 4 | `backend/tests/test_v5_failure_records.py` | 28–29 | `sk-proj-ABC…`, `sk-proj-ZZZ…` | **KEEP** — synthetic | Test-only. |
| 5 | `backend/tests/test_v5_red_team.py` | 390 | `sk-proj-ABC…` | **KEEP** — synthetic | Test-only. |
| 6 | `backend/tests/test_v6_context_lab.py` | 413 | `sk-proj-abcdefghijklmnop1234` | **KEEP** — synthetic | Test-only. |
| 7 | `backend/tests/test_v6_permissions.py` | 39 | `sk-proj-SECRETSECRET…` | **KEEP** — synthetic | Test-only. |
| 8 | `backend/tests/test_v6_prompt_compiler.py` | 308 | `sk-proj-SECRETSECRET…` | **KEEP** — synthetic | Test-only. |
| 9 | `docs/V5_LIVE_ACCEPTANCE.md` 690, 692 · `docs/V6_LIVE_ACCEPTANCE.md` 613–618 | — | `sk-proj-` / `BEGIN PRIVATE KEY` as **grep patterns** for redaction verification | **KEEP** — patterns, not credentials | Docs only. Does not trigger Push Protection (no full token shape). |
| 10 | `backend/app/services/redaction.py` 39 · `backend/app/services/finding_promotion.py` 339 · `backend/app/services/red_team_fixtures.py` 178, 193 | — | `AKIA[0-9A-Z]{16}` regex / `sk-proj-` sentinel strings | **KEEP** — redaction detection logic | Production code, contains no literal secrets. |
| 11 | `backend/.env.example` 5 (`JWT_SECRET=change-me-dev-secret-min-32-chars`), 8 (`ENCRYPTION_KEY=replace-with-…`) | — | Placeholder values, explicitly labelled "Never commit real secrets" | **KEEP** | Not loaded in production; real values come from untracked `.env` (`.gitignore`d). |
| 12 | Working tree `backend/tests/test_v5_redaction.py` 19 | — | Uncommitted sanitization: token built by string concatenation so the literal never appears in source | **KEEP** (pending commit) | Runtime token value identical to commit version; tests pass unchanged. |

**False-positive scan results:** `yarn.lock` integrity hashes and any long base64 strings in
non-test code matched no credential formats after review (dedup of the initial broad scan).

**Not found anywhere (tracked files + `f80bf49`):** `xoxp/xoxa/xoxr` tokens, `ghp_/gho_/github_pat_`
real-shaped tokens, live AWS keys, database URLs with embedded credentials, `hooks.slack.com`
webhooks, `BEGIN … PRIVATE KEY` blocks in non-test code, hardcoded admin passwords, Emergent
platform keys (adapter and dependency were removed; only historical mentions in
`memory/PRD.md`/README remain, with no credentials).

## 3. Files safe to consider for deletion

None are deletion candidates. Every file carrying secret-shaped content is an active,
passing security test or documentation. `*.orig` files
(`backend/tests/test_phase3_context.py.orig`, `backend/tests/test_v4_knowledge.py.orig`)
contain no secrets; removal is optional housekeeping, not security-driven.

## 4. Files that must be kept and sanitized

- `backend/tests/test_v5_redaction.py` — **do not delete.** It is the security regression
  suite for `app/services/redaction.py`, which is imported by production modules
  (`agent_runtime.py`, `agent_evaluation.py`). Deleting it would remove redaction regression
  coverage. Sanitization: the uncommitted concatenation form at line 19 (already in the
  working tree, tests pass) keeps the identical fixture value at runtime while removing the
  literal from source. Note the fixture itself (`xoxb-` + digits + alphabet) is synthetic:
  team ID `123456789012` is sequential, and the 2-segment shape is not a live Slack token
  format. No rotation is indicated — but if this string ever existed in a real Slack
  workspace, rotation is a zero-cost precaution.

## 5. Active-credential risk & rotation

- **Risk of an active credential in the repo: none identified.** The only push-triggering
  value is a synthetic, malformed-format Slack token fixture.
- **Rotation required: NO** for the repository contents. If the Slack workspace that the
  fixture string coincidentally resembles is real and reachable, rotate that bot token as a
  precaution (UNVERIFIED, low probability).
- GitHub Push Protection must not be bypassed; the fix is to remove the literal from
  history.

## 6. Validation

Environment: project venv `backend/.venv` (Python 3.12, pytest + pytest-xdist per
`backend/pytest.ini` `addopts -n 2 --dist loadscope`). No system Python modified.

- `tests/test_v5_redaction.py tests/test_v5_credential_exclusion.py tests/test_v5_failure_records.py`
  → **40 passed** (includes the working-tree sanitized redaction file).
- `tests/test_v5_red_team.py tests/test_v6_context_lab.py tests/test_v6_permissions.py
  tests/test_v6_prompt_compiler.py tests/test_v5_credential_exclusion.py`
  → **46 passed**.
- No production backend code was modified. Working tree still contains exactly one
  modified file (`test_v5_redaction.py`, uncommitted).

## 7. Final decision: **BLOCKED** (pending history rewrite approval)

Reasons:
1. The Slack token literal is in the **committed blob of `f80bf49`**, which is the commit
   GitHub rejects (GH013). Sanitizing the working tree does not change the pushed object.
2. Required remediation (only after explicit approval): commit the working-tree
   sanitization and squash/fix-up it into a rewritten initial commit (`git commit --amend`
   on a fresh `main`, or interactive rebase), producing a new initial-commit hash, then
   push. Verify with `git show <new-commit>:backend/tests/test_v5_redaction.py | grep -c xoxb-`
   → must be 0 before pushing. Do not use `--force` against an existing remote history; do
   not use `#[gitleaks:allow]`-style suppression or any Push Protection bypass.
3. All other findings are synthetic fixtures or detection patterns and are safe to publish
   as-is.

After the rewrite, the repository state is **READY FOR PUBLIC PUSH**.
