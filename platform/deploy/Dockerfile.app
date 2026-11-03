# syntax=docker/dockerfile:1
#
# One image for both the api and the worker (platform/API.md, "Processes").
#
#   api:    uvicorn prplatform.app:app            (the default CMD below)
#   worker: python -m prplatform.worker           (command override in docker-compose.yml)
#
# Build context is the REPOSITORY ROOT (docker-compose.yml sets context: ../..).
# Files sent to the build are limited by Dockerfile.app.dockerignore next to this file.
#
# How the worker starts sandboxes: this image contains only the docker CLI (no
# daemon). docker-compose.yml mounts the host's /var/run/docker.sock into the
# worker and adds the container user to the host's "docker" group (DOCKER_GID),
# so `docker run ... poker-run-judge` starts a sibling container on the host.
# The job directory is a host path (/srv/pokerrun/jobs) mounted at the SAME path
# inside the worker, so `-v {dir}:/job:ro` names a directory the host daemon can
# see. The worker itself never executes submitted code. See RUNBOOK.md, "Security notes".

# Static docker CLI binary (client only). Any client >= API 1.44 works with current daemons.
FROM docker:27.3.1-cli AS dockercli

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app

# Dependencies first so code changes do not reinstall them.
COPY platform/server/requirements.txt /app/server/requirements.txt
RUN pip install -r /app/server/requirements.txt

# The game package (the checker is imported by the worker) and the platform server.
COPY poker_run /app/poker_run
COPY platform/server /app/server
# The local sandbox's runner, kept at the path Settings.runner_path() expects
# (/app/server/prplatform/config.py -> parents[2] = /app -> /app/judge/runner.py).
# Production uses PR_SANDBOX=docker; this only keeps a local fallback working.
COPY platform/judge/runner.py /app/judge/runner.py

# Unprivileged runtime user. uid/gid 10001 must own the host data/jobs/backups
# directories (bootstrap.sh does the chown).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --home-dir /home/app --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data /backups \
 && chown 10001:10001 /data /backups

# `prplatform` lives in /app/server, `poker_run` in /app.
ENV PYTHONPATH=/app:/app/server \
    PR_DB=/data/platform.db \
    HOME=/home/app

USER 10001:10001
EXPOSE 8000

# --proxy-headers: Caddy is the only client (the api port is not published), so
# trusting X-Forwarded-For from any peer on the compose network is safe.
CMD ["uvicorn", "prplatform.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
