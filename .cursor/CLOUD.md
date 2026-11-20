# Cloud environment

- Python 3.12 and ffmpeg (`ffprobe` is required for verification tests).
- `python -m pip install -e ".[dev]"` from the repo root.
- Tests: `python -m playwright install chromium` then `python -m pytest`. No credentials. Unit tests do not need MediaMTX; `docker compose up` does, and it pulls `bluenviron/mediamtx`.
- The API stores its SQLite file at `DATABASE_PATH` (default `/config/home-iptv.sqlite`) and uploads under `DATA_DIR` (default `/data`). Point both at writable directories.
- Set `EMBED_WORKER=0` when a separate worker process is already running.
