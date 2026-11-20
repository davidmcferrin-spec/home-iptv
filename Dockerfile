FROM node:22-bookworm-slim AS assets
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
RUN mkdir -p /out && cp node_modules/hls.js/dist/hls.min.js /out/hls.min.js

FROM python:3.12-slim-bookworm

# GID 100 already exists in current Debian slim, so the entrypoint applies PUID and PGID.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /config --no-create-home app

WORKDIR /opt/home-iptv
COPY pyproject.toml README.md ./
COPY app ./app
COPY web ./web
COPY --from=assets /out/hls.min.js ./web/vendor/hls.min.js
RUN pip install --no-cache-dir .

COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

VOLUME ["/config", "/data"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
