FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 100 app \
    && useradd --system --uid 99 --gid app --home-dir /config --no-create-home app

WORKDIR /opt/home-iptv
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

VOLUME ["/config", "/data"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
