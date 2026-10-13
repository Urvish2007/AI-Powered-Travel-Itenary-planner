# syntax=docker/dockerfile:1

# ---- Builder -------------------------------------------------------------
# Dependencies are installed once here; only the finished venv is copied to the runtime image.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_RETRIES=10

WORKDIR /build

# No compiler toolchain needed: every dependency ships a prebuilt wheel for
# Python 3.12 on Linux. (If a future dependency needs compiling, add
# build-essential back here.)

COPY requirements.txt requirements-ai.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt -r requirements-ai.txt

# ---- Runtime -------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Run as a non-root user: a container that does not need root should not have it.
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app migrations ./migrations

USER app

EXPOSE 8000

# The readiness probe checks MongoDB; plain /health only proves the process is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]