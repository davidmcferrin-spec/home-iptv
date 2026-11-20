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

There is no published image on GHCR. `.github/workflows/image.yml` can build `home-iptv:latest` for `linux/amd64` and `linux/arm64`, and it does not push. MediaMTX is the Docker Hub image `bluenviron/mediamtx:1.16.2`, which Compose pulls. The app and the worker are built on the Unraid server from this Dockerfile.

Compose Manager is the install that starts the app, the worker, and MediaMTX together. `unraid/home-iptv.xml` is a Community Applications template for the app container only. Use it after `docker build -t home-iptv:latest .`, and still run MediaMTX and the worker. Do not publish the WebRTC and HLS ports on both the template and the MediaMTX service.

### Compose Manager

1. Install the Compose Manager plugin from Community Applications.
2. Put a checkout of this repo on the server, for example under `/mnt/user/appdata/home-iptv/src`.
3. Create the appdata directories and point the stack at them:

```sh
mkdir -p /mnt/user/appdata/home-iptv/config /mnt/user/appdata/home-iptv/data
```

`CONFIG_PATH=/mnt/user/appdata/home-iptv/config` is mounted on `/config` (the SQLite index). `DATA_PATH=/mnt/user/appdata/home-iptv/data` is mounted on `/data` (uploads and stream logs).

4. Set `PUID` and `PGID`. `99` and `100` are the usual Unraid `nobody` and `users` values. The container applies them at start so files in those directories are owned by that account.
5. Set the addresses browsers on the LAN will use, with `<unraid-ip>` replaced by the server's LAN address:

- `PUBLIC_HTTP_BASE=http://<unraid-ip>:8080`
- `PUBLIC_WEBRTC_BASE=http://<unraid-ip>:8889`
- `PUBLIC_HLS_BASE=http://<unraid-ip>:8888`
- `WEBRTC_ADDITIONAL_HOST=<unraid-ip>`

6. Leave the published ports as they are unless something else on the server already uses them:

| Port | Protocol | What it is |
| --- | --- | --- |
| 8080 | TCP | Guide and API |
| 8889 | TCP | WebRTC signaling (WHEP) |
| 8189 | UDP | WebRTC media. This one must stay UDP. |
| 8888 | TCP | Low-latency HLS fallback |
| 8554 | TCP | RTSP inside the stack |
| 65001 | UDP | HDHomeRun discovery |

7. Add the stack in Compose Manager from `docker-compose.yml` and start it with a build (`docker compose up --build -d`). The app, worker, and MediaMTX each have a health check. The guide is `http://<unraid-ip>:8080/`.

Bridge networking is enough for playback when port 8189/udp is published and `WEBRTC_ADDITIONAL_HOST` is the LAN address. Docker bridge does not forward UDP broadcasts, so HDHomeRun discovery of a tuner on the LAN, and discovery of the optional emulated tuner by Plex, Jellyfin, or Channels, will not work on the bridge. For that, run the stack with host networking or a macvlan on the LAN. Adding a tuner by its IP still works on the bridge. Emulation stays off until `PATCH /api/settings` sets `hdhr_emulation`. The guide does not switch it on.

### Community Applications template

1. On the server, from a checkout: `docker build -t home-iptv:latest .`
2. Copy `unraid/home-iptv.xml` into the flash templates folder, or add the container in the Docker page from that template.
3. The template maps `/mnt/user/appdata/home-iptv/config` to `/config` and `/mnt/user/appdata/home-iptv/data` to `/data`, and sets `PUID` and `PGID` to `99` and `100`.
4. It publishes 8080/tcp, 8889/tcp, 8189/udp, 8888/tcp, and 65001/udp. Those MediaMTX ports belong on the MediaMTX container when you use Compose. The template's `MEDIAMTX_API` and `MEDIAMTX_RTSP` must be the address of that MediaMTX, and the worker must be a second container with the same `/config` and `/data` mounts and `EMBED_WORKER=1`.
5. Set `PUBLIC_HTTP_BASE`, `PUBLIC_WEBRTC_BASE`, `PUBLIC_HLS_BASE`, and `WEBRTC_ADDITIONAL_HOST` to the Unraid LAN address, as in the Compose steps. Use host networking or a macvlan when UDP discovery has to cross the LAN.

### First playlist

The guide has no import form. After the stack is up, upload a playlist you are allowed to watch:

```sh
curl -F name=Living\ room -F file=@playlist.m3u http://<unraid-ip>:8080/api/playlists/upload
```

`examples/test-channels.m3u` is not a channel lineup. It points at `python scripts/test_sources.py`, which serves ffmpeg test patterns. On a bridge network, `127.0.0.1` inside a container is the container, not the Unraid host, so change those URLs to an address the worker can open. The worker probes each stream and the guide lists the ones that are online. Adult channels stay hidden until the session control in the guide is turned on.

### Updates

Config and data stay in the appdata directories. From the checkout, `git pull`, then `docker compose up --build -d`. That rebuilds the app and the worker and restarts the stack. `docker compose pull` refreshes MediaMTX when the tag in the compose file changes. For the template, rebuild `home-iptv:latest` and restart that container. There is no GHCR tag to pull for the app.

## Tests

```sh
python -m pytest
```

GitHub Actions runs the same command on Ubuntu with ffmpeg installed.
