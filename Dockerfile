# syntax=docker/dockerfile:1
# Where Countries Stand: the site, its API and the data tools in one image. The data lives in the /data volume.
FROM python:3.12.15-slim
# security fixes published since the base image was built
RUN apt-get update && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.32
# the site runs as its own user, not root (the same number as the charity's other sites)
RUN groupadd --system --gid 10001 wcs \
 && useradd --system --uid 10001 --gid wcs --home-dir /home/wcs --create-home wcs \
 && mkdir -p /data /backups && chown wcs:wcs /data /backups
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY config ./config
COPY data_static ./data_static
COPY web/site ./web/site
COPY web/partials ./web/partials
RUN uv sync --frozen --no-dev --extra embed --compile-bytecode
ENV WCS_DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
USER wcs
EXPOSE 8000
CMD ["wcs", "serve", "--host", "0.0.0.0", "--port", "8000", "--behind-proxy"]
