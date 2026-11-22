# Development image: Python 3.12 + uv. The virtualenv lives in the bind-mounted repo (.venv),
# so dependency changes do not need an image rebuild.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "uv==0.9.*"

ENV UV_LINK_MODE=copy \
    UV_CACHE_DIR=/cache/uv \
    UV_PROJECT_ENVIRONMENT=/src/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /src
