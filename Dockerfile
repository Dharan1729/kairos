# Multi-stage: build wheels (hnswlib and psycopg2 may need a compiler) in a
# builder image, run in a slim image without a compiler.
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md LICENSE ./
COPY kairos/ kairos/
# CPU-only torch keeps the image about 1.5 GB smaller than the default CUDA build.
RUN pip install --no-cache-dir --prefix=/install \
        --extra-index-url https://download.pytorch.org/whl/cpu ".[postgres]"

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
RUN useradd --create-home --uid 1000 kairos
COPY alembic/ alembic/
COPY alembic.ini .
# Pre-create directories that are mounted as volumes so Docker gives them
# the non-root user's ownership.
RUN mkdir -p /home/kairos/.cache/huggingface /home/kairos/.kairos \
    && chown -R kairos:kairos /app /home/kairos
USER kairos
# The embedding model (about 90 MB) is downloaded on first use into
# $HOME/.cache/huggingface; mount a volume there to keep it across restarts.
ENV HOME=/home/kairos KAIROS_HOME=/home/kairos/.kairos PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1
CMD ["uvicorn", "kairos.api:app", "--host", "0.0.0.0", "--port", "8000"]
