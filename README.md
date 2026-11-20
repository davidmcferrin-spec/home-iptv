# Home IPTV

Home IPTV is a personal, self-hosted app for a LAN. It imports M3U and M3U8 playlists and HDHomeRun lineups, checks each stream with ffprobe, and keeps a SQLite index with full-text search. Point it only at streams you have the rights to view. It does not ship channel lists, and it will fetch whatever URL you give it. Do not expose it to the public internet: there is no login.

Playback (one upstream pull per channel, WebRTC, and a set-top guide) is added on top of this index.

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
| POST | `/api/tags` | Create a tag. PATCH renames, DELETE removes |
| POST | `/api/epg/upload` | Import XMLTV |
| POST | `/api/hdhomerun/discover` | UDP discovery |
| POST | `/api/hdhomerun/devices` | Add a tuner by host or base URL and import `/lineup.json` |
| GET | `/api/health` | Liveness |

`tests/fixtures/sample.m3u` is a made-up playlist of non-routable URLs for the parser. It is not a channel lineup.

## Tests

```sh
python -m pytest
```

GitHub Actions runs the same command on Ubuntu with ffmpeg installed.
