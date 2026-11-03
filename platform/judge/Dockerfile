# Poker Run judge sandbox image. Build from the repo root or platform/judge:
#   docker build -t poker-run-judge platform/judge
# The worker runs it per submission with --network none, --read-only, a small
# /tmp tmpfs, uid 65534, no capabilities (see platform/API.md, "Sandbox").
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Only the runner goes into the image; the solver and inputs arrive in /job (read-only mount).
COPY runner.py /runner.py
RUN chmod 0444 /runner.py

# nobody:nogroup; the worker also passes --user 65534:65534.
USER 65534:65534
WORKDIR /tmp

CMD ["python3", "/runner.py", "/job", "2.0"]
