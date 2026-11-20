# Home IPTV

Home IPTV is a personal, self-hosted app for a LAN. It imports M3U and M3U8 playlists and HDHomeRun lineups, checks each stream with ffprobe, and keeps a SQLite index with full-text search. Point it only at streams you have the rights to view. It does not ship channel lists, and it will fetch whatever URL you give it. Do not expose it to the public internet: there is no login.

The browser plays a channel over WebRTC (WHEP). Low-latency HLS is the fallback when the WebRTC handshake cannot complete.

## What it stores

- Playlist channels, deduped by URL. EXTINF `tvg-id`, `tvg-name`, `tvg-logo`, `tvg-language`, `tvg-country`, and `group-title` are kept. `group-title` also becomes a tag.
- Language and country. The order is tvg fields, then `group-title`, then the audio language ffprobe reports, then a title heuristic. Each value is marked detected or manual. A manual edit is kept across reimports.
- An adult flag, suggested from the title and group (the phrase "Adult Swim" is left alone) and editable by hand. Search, tag counts, and channel lookup omit adult channels unless `include_adult=true`. The server does not remember that choice.
- Online or offline status, last check time, resolution, codecs, and bitrate.
- Optional XMLTV programmes, matched on `tvg-id` or display name.
- HDHomeRun tuners added by UDP discovery or by a manual base URL. Docker bridge networking often drops UDP broadcast replies, so use a manual IP there, or run the stack with host networking or a macvlan so discovery can see the LAN. Imported lineup channels are tagged `OTA` and keep their guide numbers. The tuner count is stored so playback can share one tuner across every viewer of a channel and refuse another channel when none are free.

## Run with Docker

```sh
docker compose up --build -d
```

The API listens on port 8080 (`WEB_PORT`). SQLite lives in the `config` volume and uploads in the `data` volume. `PUID` and `PGID` default to `99` and `100`.

The `worker` service probes streams with a bounded thread pool (`VERIFY_CONCURRENCY`, `VERIFY_TIMEOUT_SECONDS`) and repeats on `VERIFY_INTERVAL_MINUTES`. It also reimports any playlist that has `interval_minutes` set. Upload or URL import queues a check immediately; `POST /api/channels/{id}/verify` queues another one.

MediaMTX fans a channel out to every viewer. The API publishes each channel into MediaMTX with one ffmpeg process, on demand, and stops it after `IDLE_TIMEOUT_SECONDS` with no readers. A second viewer of the same channel reuses that process. Published ports are `WEB_PORT` (8080), `WEBRTC_PORT` (8889), `WEBRTC_UDP_PORT` (8189/udp), `HLS_PORT` (8888), and `RTSP_PORT` (8554). The MediaMTX control API stays on the Docker network and is not published. Set `PUBLIC_HTTP_BASE`, `PUBLIC_WEBRTC_BASE`, and `PUBLIC_HLS_BASE` to the addresses browsers and other LAN devices use, and set `WEBRTC_ADDITIONAL_HOST` to the server's LAN IP so WebRTC candidates are reachable.

`POST /api/channels/{id}/watch` returns `whep_url` and `llhls_url`. `GET /api/channels/{id}/stream` reports `upstream_pulls` (0 or 1) and the MediaMTX reader list.

ffmpeg remuxes with stream copy when video is WebRTC-capable (H.264, VP8, VP9, AV1) and audio is WebRTC-capable (Opus, PCMU, PCMA, G.722). Most IPTV sources are H.264 with AAC. AAC cannot be carried in WebRTC, so only the audio is transcoded to Opus. That is cheap. H.265/HEVC video is transcoded to H.264 with libx264 `ultrafast` and `zerolatency`, which can use a full core per active 720p or 1080p channel. Idle channels are stopped, so the cost is limited to channels someone is watching.

HDHomeRun emulation is off until `PATCH /api/settings` sets `hdhr_emulation` to true. It then serves `/discover.json`, `/lineup.json`, and `/lineup_status.json`, and MPEG-TS at `/auto/v{guide}`. Those streams read the hub's local RTSP publish, so they do not open a second upstream pull. Adult channels are left out of the emulated lineup. The virtual tuner count (`emulated_tuner_count`, default 4) and a real tuner's `TunerCount` both return HTTP 503 with `all tuners busy` when a new channel would need a tuner that is already in use. UDP discovery replies are sent on port 65001 (`HDHR_UDP_PORT`). Bridge networking does not carry those broadcasts; use host networking or a macvlan on Unraid if Plex, Jellyfin, or Channels should find the virtual tuner by itself. A manual base URL works on the bridge.

`examples/test-channels.m3u` points at `python scripts/test_sources.py`, which serves ffmpeg test patterns and logs one line per upstream connection. It is not a channel lineup.

## Run from a checkout

```sh
python -m pip install -e ".[dev]"
mkdir -p config data
DATABASE_PATH=./config/home-iptv.sqlite DATA_DIR=./data python -m uvicorn app.main:app --port 8080
```

With the default `EMBED_WORKER=1`, the API process also runs the verifier.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/playlists` | Import a JSON body `{name, url, interval_minutes}` |
| POST | `/api/playlists/upload` | Import an M3U file (`name`, `file`) |
| POST | `/api/playlists/{id}/import` | Reimport now |
| GET | `/api/channels` | Search and filter. Query: `q`, `tag`, `status`, `language`, `country`, `sort` (`title`, `status`, `language`, `country`, `number`), `order`, `include_adult` |
| PATCH | `/api/channels/{id}` | Set language, country, adult, or guide number by hand |
| POST | `/api/channels/bulk` | The same fields, plus `add_tag_ids` and `remove_tag_ids` |
| POST | `/api/channels/{id}/watch` | Start or reuse the upstream pull. Returns WHEP and LL-HLS URLs |
| GET | `/api/channels/{id}/stream` | Upstream pull count and MediaMTX readers |
| POST | `/api/tags` | Create a tag. PATCH renames, DELETE removes |
| POST | `/api/epg/upload` | Import XMLTV |
| POST | `/api/hdhomerun/discover` | UDP discovery |
| POST | `/api/hdhomerun/devices` | Add a tuner by host or base URL and import `/lineup.json` |
| GET | `/api/health` | Liveness |

`tests/fixtures/sample.m3u` is a made-up playlist of non-routable URLs for the parser. It is not a channel lineup.

## Guide

Open `/` in a browser on the LAN. The guide is a dark overlay on the live picture: filter chips, a clock and date, a program info panel, and a channel grid. Arrow keys move the highlight, Enter watches the focused channel, Page Up and Page Down change channel, and the number keys tune a guide number. On a phone the guide collapses to a now-and-next list with a swipeable grid, and tapping a channel plays full screen.

Settings are stored in this browser only (`localStorage`): default country, languages, tags, sort, and the starting channel. Show adult content is a separate control and lasts for the browser session (`sessionStorage`). A new tab starts with adult channels hidden. Adult channels stay out of the grid, the list, search, and counts until that control is on.

If `POST /api/channels/{id}/watch` returns `all tuners busy`, the guide shows "All tuners are busy" and does not open another upstream pull.

## Unraid

Use the Compose Manager plugin and this repository's `docker-compose.yml`. That stack is the app, the verifier, and MediaMTX.

1. Build is done by Compose (`docker compose up --build -d` from a checkout, or point the stack at this directory).
2. Map `CONFIG_PATH` to `/mnt/user/appdata/home-iptv/config` and `DATA_PATH` to `/mnt/user/appdata/home-iptv/data`.
3. Set `PUID` and `PGID` (99 and 100 are the usual Unraid values).
4. Set `PUBLIC_HTTP_BASE`, `PUBLIC_WEBRTC_BASE`, and `PUBLIC_HLS_BASE` to `http://<unraid-ip>` plus ports 8080, 8889, and 8888. Set `WEBRTC_ADDITIONAL_HOST` to that IP so browsers can open the WebRTC UDP port (`WEBRTC_UDP_PORT`, default 8189).
5. Open `http://<unraid-ip>:8080/`.

HDHomeRun UDP discovery does not cross Docker bridge networking. For discovery of a real tuner, or so Plex, Jellyfin, or Channels can find the optional emulated tuner, run the stack with host networking or a macvlan. Adding a tuner by IP still works on the bridge. Emulation stays off until you enable it in `PATCH /api/settings` or from a client of that API. The guide has no server-side switch for it; set `hdhr_emulation` with the settings API.

`unraid/home-iptv.xml` is a Community Applications template for an image named `home-iptv:latest`. Build that tag on the server before adding the template (`docker build -t home-iptv:latest .`). The template carries the web port, the WebRTC UDP port, `/config`, `/data`, `PUID`, and `PGID`. The compose file is the install that also starts MediaMTX and the worker.

The Dockerfile uses multi-arch base images. `.github/workflows/image.yml` builds `linux/amd64` and `linux/arm64` when you run it by hand. For a local playback fallback, the image copies `hls.js` from npm. A checkout can do the same with `npm ci` and `mkdir -p web/vendor && cp node_modules/hls.js/dist/hls.min.js web/vendor/hls.min.js`.

## Tests

```sh
python -m pytest
```

GitHub Actions runs the same command on Ubuntu with ffmpeg installed.
