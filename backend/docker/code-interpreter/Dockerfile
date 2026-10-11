# OmniLab code-interpreter sandbox image.
#
# Build (from the repository root):
#   docker build -t omnilab-code-interpreter:latest backend/docker/code-interpreter
#
# Run (reference — the backend builds the full flag set programmatically
# via build_docker_command; never relax these flags):
#   docker run --rm -i --network none --read-only \
#     --tmpfs /workspace:size=64m,mode=1777 --tmpfs /tmp:size=16m,mode=1777 \
#     -v <staging-input>:/workspace/input:ro -v <staging-output>:/workspace/output \
#     --memory 512m --memory-swap 512m --cpus 1.0 --pids-limit 128 \
#     --user 65532:65532 --cap-drop ALL --security-opt no-new-privileges=true \
#     --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONUNBUFFERED=1 --env MPLBACKEND=Agg \
#     --workdir /workspace omnilab-code-interpreter:latest python -
FROM python:3.11-slim

# Minimal, non-interactive install. requirements.txt carries exact pins.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Workspace mount points. Backed by tmpfs at runtime; nothing persists.
RUN mkdir -p /workspace/input /workspace/output

WORKDIR /workspace

# Run as the unprivileged nobody user (uid/gid 65532 in the slim image).
USER 65532:65532

# No ENTRYPOINT/CMD: the backend always invokes `python -` with the
# untrusted program streamed over STDIN.
