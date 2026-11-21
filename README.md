# Home IPTV

Home IPTV is a personal, self-hosted app for a LAN. It imports M3U and M3U8 playlists and HDHomeRun lineups, checks each stream with ffprobe, and keeps a SQLite index with full-text search. Point it only at streams you have the rights to view. It does not ship channel lists, and it will fetch whatever URL you give it. Do not expose it to the public internet: there is no login.

The browser plays a channel over WebRTC (WHEP) when the video is WebRTC-safe. H.264 with B-frames or a High profile, and anything other than H.264, VP8, VP9, or AV1, plays over low-latency HLS instead. If the picture stalls for four seconds, the player switches to HLS. Each channel can override that with Auto, WebRTC, HLS, or Transcode.

## What it stores

- Playlist channels, deduped by URL. EXTINF `tvg-id`, `tvg-name`, `tvg-logo`, `tvg-language`, `tvg-country`, and `group-title` are kept. `group-title` also becomes a tag.
- Language and country. The order is tvg fields, then `group-title`, then the audio language ffprobe reports, then a title heuristic. Each value is marked detected or manual. A manual edit is kept across reimports.
- An adult flag, suggested from the title and group (the phrase "Adult Swim" is left alone) and editable by hand. Search, tag counts, and channel lookup omit adult channels unless `include_adult=true`. The server does not remember that choice.
- Online or offline status, last check time, resolution, codecs, and bitrate.
- Optional XMLTV programmes, matched on `tvg-id` or display name.
- HDHomeRun tuners added by UDP discovery or by a manual base URL. Docker bridge networking often drops UDP broadcast replies, so use a manual IP there, or run the stack with host networking or a macvlan so discovery can see the LAN. Imported lineup channels are tagged `OTA` and keep their guide numbers. The tuner count is stored so playback can share one tuner across every viewer of a channel and refuse another channel when none are free.

## Run with Docker

```sh
docker compose pull && docker compose up -d
```

That pulls `ghcr.io/davidmcferrin-spec/home-iptv:latest` for the app and the worker. To build the image on this machine instead:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

The guide and API are published on port 9595 (`WEB_PORT`). The container listens on 8080. SQLite lives in the `config` volume and uploads in the `data` volume. `PUID` and `PGID` default to `99` and `100`.

The `worker` service probes streams with a bounded thread pool (`VERIFY_CONCURRENCY`, `VERIFY_TIMEOUT_SECONDS`) and repeats on `VERIFY_INTERVAL_MINUTES`. It also reimports any playlist that has `interval_minutes` set. Upload or URL import queues a check immediately; `POST /api/channels/{id}/verify` queues another one.

MediaMTX fans a channel out to every viewer. The API publishes each channel into MediaMTX with one ffmpeg process, on demand, and stops it after the idle timeout with no readers. A second viewer of the same channel reuses that process. Published ports are 9595 for the guide, 8889 for WebRTC signaling, 8189/udp and 8189/tcp for WebRTC media, 8888 for LL-HLS, and 8554 for RTSP. MediaMTX does not advertise container bridge addresses. The MediaMTX control API stays on the Docker network and is not published. Playback addresses are taken from the browser's request host. Settings can override them.

`POST /api/channels/{id}/watch` returns `whep_url` and `llhls_url`. `GET /api/channels/{id}/stream` reports `upstream_pulls` (0 or 1) and the MediaMTX reader list.

ffmpeg copies video only when it is WebRTC-safe: VP8, VP9, AV1, or H.264 with no B-frames and a profile other than High. AAC is transcoded to Opus for WebRTC. H.264 with B-frames is published as-is and played over LL-HLS, which can decode those frames. HEVC and other video is transcoded to H.264 baseline with libx264 `veryfast`, no B-frames, and a two-second GOP, and that is what the Transcode override always does. Idle channels are stopped.

HDHomeRun emulation is off until `PATCH /api/settings` sets `hdhr_emulation` to true. It then serves `/discover.json`, `/lineup.json`, and `/lineup_status.json`, and MPEG-TS at `/auto/v{guide}`. Those streams read the hub's local RTSP publish, so they do not open a second upstream pull. Adult channels are left out of the emulated lineup. The virtual tuner count (`emulated_tuner_count`, default 4) and a real tuner's `TunerCount` both return HTTP 503 with `all tuners busy` when a new channel would need a tuner that is already in use. UDP discovery replies are sent on port 65001 (`HDHR_UDP_PORT`). Bridge networking does not carry those broadcasts; use host networking or a macvlan on Unraid if Plex, Jellyfin, or Channels should find the virtual tuner by itself. A manual base URL works on the bridge.

`examples/test-channels.m3u` points at `python scripts/test_sources.py`, which serves ffmpeg test patterns and logs one line per upstream connection. It is not a channel lineup.

## Run from a checkout

```sh
python -m pip install -e ".[dev]"
mkdir -p config data
DATABASE_PATH=./config/home-iptv.sqlite DATA_DIR=./data python -m uvicorn app.main:app --port 9595
```

With the default `EMBED_WORKER=1`, the API process also runs the verifier.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/playlists` | Import a JSON body `{name, url, interval_minutes}` |
| POST | `/api/playlists/upload` | Import an M3U file (`name`, `file`) |
| POST | `/api/playlists/{id}/import` | Reimport now |
| GET | `/api/channels` | Search and filter. Query: `q`, `tag`, `status`, `language`, `country`, `sort` (`title`, `status`, `language`, `country`, `number`), `order`, `include_adult` |
| PATCH | `/api/channels/{id}` | Set name, logo, guide match, language, country, adult, guide number, or tags by hand |
| POST | `/api/channels/bulk` | Set language, country, adult, or tags on many channels |
| GET | `/playlist.m3u` | Online channels as M3U. Query: `tag`, `language`, `country`, `online`, `include_adult` |
| GET | `/guide.xml` | XMLTV for the same filters |
| GET | `/play/{id}` | Start the hub pull and redirect to LL-HLS |
| POST | `/api/channels/{id}/watch` | Start or reuse the upstream pull. Returns WHEP and LL-HLS URLs |
| GET | `/api/channels/{id}/stream` | Upstream pull count and MediaMTX readers |
| POST | `/api/tags` | Create a tag. PATCH renames, DELETE removes |
| POST | `/api/epg/upload` | Import XMLTV |
| POST | `/api/hdhomerun/discover` | UDP discovery |
| POST | `/api/hdhomerun/devices` | Add a tuner by host or base URL and import `/lineup.json` |
| GET | `/api/health` | Liveness |

`tests/fixtures/sample.m3u` is a made-up playlist of non-routable URLs for the parser. It is not a channel lineup.

## Guide

Open `/` in a browser on the LAN. The guide is a dark overlay on the live picture: filter chips, a clock and date, a program info panel, and a channel grid. Arrow keys move the highlight, Enter watches the focused channel, Page Up and Page Down change channel, and the number keys tune a guide number. On a phone the guide collapses to a now-and-next list with a swipeable grid, and tapping a channel plays full screen. Sources (`I`, or the Sources button) uploads an M3U or M3U8 file, adds a playlist URL with an optional re-import interval, lists each playlist with checked, online, and offline counts, and adds an HDHomeRun by IP. A playlist header `url-tvg` or `x-tvg-url` is used for the guide. Otherwise channels are matched by tvg-id, then by name and country, against the public iptv-org channel and logo data. Programme listings come from that header when it is present, and from built-in public XMLTV feeds for the countries in the catalog (epgshare01 and epg.pw, no account). A channel matches a feed by tvg-id, including an iptv-org id, then by display name. Settings lists each feed with an enable switch, the last fetch, and how many channels matched, and can add another guide URL. Logos are filled when the playlist has none. A manual edit wins. A feed that fails is logged and skipped.

Edit, or the E key, changes the focused channel. Checkboxes select several channels and Edit then sets a tag, language, country, or the adult flag on all of them. A saved name, logo, guide match, language, country, adult flag, or tag is marked manual and is kept on the next import.

The default view (country, languages, tags, sort, and the starting channel) stays in this browser (`localStorage`). Server settings, including the public bases, verify timing, the idle timeout, HDHomeRun emulation, and playlist folders, are stored in SQLite and edited from the same Settings screen. Playlist folders are the first tag or category, language, country, or source. Use in VLC shows the playlist and guide URLs, copy buttons, and a QR code for the playlist. Host ports are shown there and changed in Compose or the template. Show adult content is in Settings and lasts for the browser session (`sessionStorage`). A new tab starts with adult channels hidden. Adult channels stay out of the grid, the list, search, and counts until that control is on.

On Android, Android TV, or Google TV, open VLC, choose Network, and paste the playlist URL from Settings. On iPad, open VLC, choose Network, and paste the same URL. VLC has no Roku app. A Roku needs a different app that accepts an M3U URL.

If `POST /api/channels/{id}/watch` returns `all tuners busy`, the guide shows "All tuners are busy" and does not open another upstream pull.

## Unraid

The default install pulls `ghcr.io/davidmcferrin-spec/home-iptv`. `.github/workflows/image.yml` builds `linux/amd64` and `linux/arm64` and pushes that image on pushes to `main` and on `v*` tags. The app and the worker use the same image; the worker starts with `python -m app.worker`. MediaMTX stays `bluenviron/mediamtx:1.16.2`. The app writes MediaMTX's config into the config volume when the stack starts, so the compose file does not need a second config file beside it.

A private GitHub repository publishes a private package. Unraid cannot pull it while logged out. Create a personal access token with `read:packages`, sign the server in to `ghcr.io`, or make the package public. A public package needs no login.

1. Install the Compose Manager plugin from Community Applications.
2. Add a stack, paste `docker-compose.yml`, and choose Compose Up.
3. Open `http://<unraid-ip>:9595/`.

The stack uses `/mnt/user/appdata/home-iptv/config` and `/mnt/user/appdata/home-iptv/data`, and `PUID`/`PGID` `99`/`100`, with no env file. Playback addresses are detected from the browser. Change them, the verify timing, the idle timeout, or HDHomeRun emulation in Settings. Host ports stay in the compose file or the template.

| Port | Protocol | What it is |
| --- | --- | --- |
| 9595 | TCP | Guide and API |
| 8889 | TCP | WebRTC signaling (WHEP) |
| 8189 | UDP and TCP | WebRTC media. UDP is the usual path. TCP is the fallback. |
| 8888 | TCP | Low-latency HLS fallback |
| 8554 | TCP | RTSP inside the stack |
| 65001 | UDP | HDHomeRun discovery |

Bridge networking does not forward UDP broadcasts, so HDHomeRun discovery across the LAN needs host networking or a macvlan. Adding a tuner by its IP still works on the bridge. Emulation stays off until Settings turns it on.

`unraid/home-iptv.xml` is a Community Applications template for the app container only. It uses the GHCR image. You still need MediaMTX and the worker from the compose file. Do not publish the WebRTC and HLS ports on both the template and the MediaMTX service. To build on the server instead of pulling, use `docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d`.

### First playlist

After the stack is up, open the guide and use Sources to upload an M3U or M3U8 file, add a playlist URL, or add an HDHomeRun by IP. The same upload is available from the shell:

```sh
curl -F name=Living\ room -F file=@playlist.m3u http://<unraid-ip>:9595/api/playlists/upload
```

`examples/test-channels.m3u` is not a channel lineup. It points at `python scripts/test_sources.py`, which serves ffmpeg test patterns. On a bridge network, `127.0.0.1` inside a container is the container, not the Unraid host, so change those URLs to an address the worker can open. The worker probes each stream and the guide lists the ones that are online. Adult channels stay hidden until Settings turns on show adult content for this browser session.

### Updates

Config and data stay in the appdata directories. `docker compose pull` and `docker compose up -d` refresh the app, the worker, and MediaMTX. For the template, pull `ghcr.io/davidmcferrin-spec/home-iptv:latest` and restart the container. A local build uses `docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d`.

## Tests

```sh
python -m pytest
```

GitHub Actions runs the same command on Ubuntu with ffmpeg installed.
