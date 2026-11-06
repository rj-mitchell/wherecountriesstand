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
# uv's download cache is a build cache, kept between builds on the server but never in the image; packages are
# copied out of it, not linked to it
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
# The dependencies first, in a layer of their own: it is rebuilt only when uv.lock or pyproject.toml change, not
# for every change to the code or the pages, which is most deploys.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra embed --no-install-project
# Then the site itself (seconds)
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra embed
COPY config ./config
COPY data_static ./data_static
COPY web/site ./web/site
COPY web/partials ./web/partials
ENV WCS_DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
USER wcs
EXPOSE 8000
CMD ["wcs", "serve", "--host", "0.0.0.0", "--port", "8000", "--behind-proxy"]
