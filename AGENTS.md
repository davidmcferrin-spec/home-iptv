# Home IPTV

Personal LAN app. It imports playlists and HDHomeRun lineups the operator is allowed to watch, indexes them in SQLite, and restreams one upstream pull per channel to browsers over WebRTC.

## Layout

- `app/main.py` builds the FastAPI app.
- `app/api.py` is the HTTP API.
- `app/playlist.py` parses M3U/M3U8.
- `app/detect.py` detects language, country, and the adult flag.
- `app/catalog.py` writes channels, tags, search, and EPG rows.
- `app/verify.py` runs ffprobe. `app/worker.py` drains the queue and reimports playlists.
- `app/hdhomerun.py` speaks the published HDHomeRun discovery and lineup HTTP API.
- `app/hub.py` pulls each channel once with ffmpeg and publishes it to MediaMTX. WebRTC uses WHEP. LL-HLS is the fallback.
- `app/emulation.py` is the optional HDHomeRun device (off by default).
- `app/epg.py` reads XMLTV.
- `mediamtx.yml` is the relay config. `scripts/test_sources.py` serves local ffmpeg patterns.
- `tests/` covers the parser, detection, adult filtering, lineup parsing, the search API, the single upstream pull, and emulation.

## Run

```sh
python -m pip install -e ".[dev]"
DATABASE_PATH=./config/home-iptv.sqlite DATA_DIR=./data python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

`EMBED_WORKER=1` (the default) verifies streams in this process. Docker Compose runs `python -m app.worker` beside the API instead.

## Test

```sh
python -m pytest
```

Passing tests print a single summary line. Failures print the failing line.
