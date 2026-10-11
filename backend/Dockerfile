# Uniyra AI Lab — backend production image (local build, not published).
#
# Build (from the repository root):
#   docker build -t uniyra-ailab-backend backend
#
# Configuration is supplied at runtime via environment variables (Compose),
# never baked into the image. backend/.env is deliberately excluded via
# .dockerignore.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Reproducible install: requirements.txt is fully pinned.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root runtime user; writable data dir for attachments / sandbox staging.
RUN useradd --system --uid 10001 --no-create-home uniyra \
    && mkdir -p /app/data/attachments \
    && chown -R uniyra:uniyra /app/data

USER uniyra

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/api/', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--no-server-header"]
