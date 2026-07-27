# ---- Builder: install deps into a venv -------------------------------------
FROM python:3.12-slim AS builder

# Some transitive deps (e.g. aiohttp, pulled in by line-bot-sdk) ship C
# extensions and fall back to building from source when no matching wheel is
# available for this exact Python/arch combo.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY app app/
COPY config.yaml .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Data/secrets are mounted as volumes at runtime; create them here so the
# non-root user owns them even before a volume is attached.
RUN mkdir -p data secrets logs \
    && chown -R appuser:appuser /app \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/app/data", "/app/secrets"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
