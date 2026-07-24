# Rom Sync - web interface on port 8484
#   /config  catalog, settings, selection     /data  the synced files
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOLROMS_HOST=0.0.0.0 \
    LOLROMS_PORT=8484 \
    LOLROMS_CONFIG=/config \
    LOLROMS_DATA=/data \
    LOLROMS_DATA_LOCKED=1 \
    LOLROMS_NO_BROWSER=1 \
    PUID=99 \
    PGID=100 \
    UMASK=000

# tzdata: TZ (e.g. America/New_York) drives file dates and the sync schedule
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

EXPOSE 8484
VOLUME ["/config", "/data"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('LOLROMS_PORT', '8484'), timeout=5)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
