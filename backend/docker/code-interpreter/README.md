# Code-interpreter sandbox policy

The code-interpreter executes untrusted Python **only** inside an ephemeral,
non-privileged Docker container. The FastAPI host never runs model code: it
stages input files, spawns `docker run`, enforces a wall-clock timeout, and
collects outputs.

## Image contents

- Base: `python:3.11-slim` (CPython 3.11).
- Packages (exact pins in `requirements.txt`):
  | Package    | Version |
  |------------|---------|
  | numpy      | 1.26.4  |
  | pandas     | 2.2.2   |
  | matplotlib | 3.8.4   |
- `MPLBACKEND=Agg` (headless plotting; no display server).
- Runs as unprivileged user `65532:65532`. No entrypoint.

## Build

From the repository root:

```sh
docker build -t omnilab-code-interpreter:latest backend/docker/code-interpreter
```

## Runtime flags reference

The backend builds the command in `build_docker_command`; the authoritative
flag set is:

```sh
docker run --rm -i --name <unique> \
  --network none \
  --read-only \
  --tmpfs /workspace:size=<TMPFS_MB>m,mode=1777 \
  --tmpfs /tmp:size=16m,mode=1777 \
  -v <staging-input>:/workspace/input:ro \
  -v <staging-output>:/workspace/output \
  --memory <MEM>m --memory-swap <MEM>m \
  --cpus <CPUS> --pids-limit <PIDS> \
  --user 65532:65532 \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONUNBUFFERED=1 --env MPLBACKEND=Agg \
  --workdir /workspace <image> python -
```

The untrusted program is streamed to `python -` over **STDIN** — never via
argv, never via environment variables.

## Sandbox policy

- **Network OFF** (`--network none`): no egress, no loopback services, no
  package installation at runtime.
- **Limits** (server-configurable, defaults):

  | Setting   | Env var                      | Default |
  |-----------|------------------------------|---------|
  | RAM       | `CODE_INTERPRETER_MEMORY_MB` | 512 MB  |
  | CPUs      | `CODE_INTERPRETER_CPUS`      | 1.0     |
  | PIDs      | `CODE_INTERPRETER_PIDS_LIMIT`| 128     |
  | Workspace | `CODE_INTERPRETER_TMPFS_MB`  | 64 MB   |
  | Timeout   | `CODE_INTERPRETER_TIMEOUT_SECONDS` | 30 s |
  | Code size | `CODE_INTERPRETER_MAX_CODE_CHARS`  | 100000 |
  | Stdout/stderr each | `CODE_INTERPRETER_MAX_OUTPUT_BYTES` | 204800 |
  | Output files | `CODE_INTERPRETER_MAX_FILES` / `CODE_INTERPRETER_MAX_FILE_BYTES` | 10 / 5 MB |

- **Environment whitelist**: only `PYTHONDONTWRITEBYTECODE`,
  `PYTHONUNBUFFERED`, `MPLBACKEND`. No secrets are ever passed; in particular
  never the Docker socket, provider keys, JWT secrets, or encryption keys.
- **Files**: `/workspace/input` is read-only; `/workspace/output` is the only
  writable mount. Staging directories live under `data/sandbox-staging/` and
  are validated with realpath containment (no `..`, no symlink escape). Only
  regular files under the output dir are collected (bounded count and size);
  `html`/`svg` outputs are served with `Content-Security-Policy: sandbox` and
  `X-Content-Type-Options: nosniff` plus an `active-content` warning.
- **Cleanup**: containers run with `--rm` plus a unique `--name` so a killed
  client run is removed best-effort (`docker rm -f`). Ephemeral staging dirs
  are deleted after every run; collected outputs persist under
  `data/sandbox-outputs/<execution_id>/` until the execution record is
  deleted.

## Deferred / explicitly out of scope

- No `pip install` inside the container (no network; fixed package set).
- No network profiles, egress allowlists, or GPU access.
- No persistent per-user workspaces; each execution is isolated.
