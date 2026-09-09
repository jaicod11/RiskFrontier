FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq is needed at runtime by psycopg2; curl backs the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Test dependencies are excluded by default so the production image does not
# ship pytest. docker-compose passes INSTALL_DEV=true for local development,
# where the suite runs inside the container.
ARG INSTALL_DEV=false

COPY requirements.txt requirements-dev.txt ./
RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
         pip install -r requirements-dev.txt; \
       else \
         pip install -r requirements.txt; \
       fi

COPY . .

# Render supplies $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# One uvicorn worker under gunicorn. Sizing for a 512 MB instance: the import
# graph (pandas + numpy + scipy + yfinance) measures 176 MB resident before
# serving a single request, and the analytical endpoints allocate on top of
# that. A second worker would fit only just, and would not help — the slow
# paths are CPU-bound numpy on a shared-CPU free instance, so concurrency buys
# contention rather than throughput. gunicorn still earns its place by
# restarting a worker that dies instead of dropping the service.
# WEB_CONCURRENCY is honoured if a larger instance is ever used.
#
# --timeout must exceed the slowest endpoint; the bootstrap path is the
# constraint. See core/limits.py and the deployment section of the README.
CMD ["sh", "-c", "gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers ${WEB_CONCURRENCY:-1} \
  --bind 0.0.0.0:${PORT:-8000} \
  --timeout ${GUNICORN_TIMEOUT:-120} \
  --graceful-timeout 30 \
  --keep-alive 65 \
  --access-logfile - \
  --error-logfile -"]
