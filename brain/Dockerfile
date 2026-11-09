# Brain image: one image, three services (brain gRPC, mcp-procurement, mcp-notify) — the command differs.
# Light install: no `ingest` group (docling/torch/spaCy live on the build machine only).
# Build context = repository root:  docker build -f brain/Dockerfile .
FROM python:3.14.10-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/brain/.venv
RUN pip install --no-cache-dir uv==0.13.6 \
    && useradd --system --uid 10001 --home /app app
WORKDIR /app/brain

COPY brain/pyproject.toml brain/uv.lock brain/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY brain/ ./
COPY prompts/ /app/prompts/
RUN mkdir -p /data/po && chown -R app /data /app
USER app
ENV REPO_ROOT=/app PROMPTS_DIR=/app/prompts PO_DIR=/data/po PATH=/app/brain/.venv/bin:$PATH
CMD ["python", "-m", "agent.server"]
