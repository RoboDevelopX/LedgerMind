# Ledgermind: FastAPI app + static UI. Models run on Nebius Token Factory, so the image needs no GPU.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    LEDGERMIND_MULTIUSER=1 \
    LEDGERMIND_DATA_DIR=/data \
    LEDGERMIND_DB=/data/ledgermind.db

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config.yaml ./
COPY ledgermind ./ledgermind
COPY scripts ./scripts
COPY sample_data ./sample_data

RUN useradd --create-home --uid 10001 ledgermind && mkdir -p /data && chown ledgermind /data
USER ledgermind
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=2)"

CMD ["sh", "-c", "uvicorn ledgermind.server:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
