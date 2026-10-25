# syntax=docker/dockerfile:1
# Where Countries Stand: the site, its API and the data tools in one image. The data lives in the /data volume.
FROM python:3.12.7-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY config ./config
COPY data_static ./data_static
COPY web/site ./web/site
RUN uv sync --frozen --no-dev --extra embed
ENV WCS_DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
CMD ["wcs", "serve", "--host", "0.0.0.0", "--port", "8000", "--behind-proxy"]
