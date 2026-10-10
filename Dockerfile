# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0

ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="Prism Adapter" \
      org.opencontainers.image.description="Standalone sandboxed Chromium transport for CLIProxyAPI" \
      org.opencontainers.image.source="https://github.com/power12317/prism-adapter" \
      org.opencontainers.image.licenses="LGPL-3.0-only" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/prism \
    PRISM_ADAPTER_CHROME=/opt/chromium/chrome \
    CHROME_DEVEL_SANDBOX=/opt/chromium/chrome_sandbox \
    PRISM_ADAPTER_STATE_DIR=/var/lib/cliproxy-prism

WORKDIR /opt/prism-adapter
COPY requirements.txt requirements.lock ./
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock \
    && python -c 'import sys; assert sys.version_info >= (3, 12)' \
    && groupadd --gid 10001 prism \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/prism prism \
    && mkdir -p /var/lib/cliproxy-prism /opt/chromium \
    && chown 10001:10001 /var/lib/cliproxy-prism \
    && chmod 700 /var/lib/cliproxy-prism \
    && python -c 'from playwright.sync_api import sync_playwright; from pathlib import Path; p=sync_playwright().start(); c=Path(p.chromium.executable_path); assert c.is_file(), c; Path("/opt/chromium/chrome").symlink_to(c); s=c.parent/"chrome_sandbox"; assert s.is_file(), s; Path("/opt/chromium/chrome_sandbox").symlink_to(s); p.stop()' \
    && chmod -R go-w /ms-playwright

COPY *.py COPYING COPYING.LESSER UPSTREAM.md *manifest.json ./
COPY tests/ ./tests/
COPY container/ ./container/
RUN chmod 755 container/entrypoint.sh

# Root only initializes a new bind mount; entrypoint drops UID/GID and capabilities
# before running Python. Chromium retains its user-namespace sandbox.
ENTRYPOINT ["/opt/prism-adapter/container/entrypoint.sh"]
CMD ["python", "server.py"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "/opt/prism-adapter/container/healthcheck.py"]
