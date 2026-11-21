import logging
import os
import sqlite3
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app.catalog import (
    channel_json,
    channel_tags,
    enqueue,
    programs_for,
    replace_epg,
    search_channels,
    set_manual_fields,
    import_lineup_channels,
)
from app.db import connect, parse_stamp, setting, set_setting, stamp, utcnow
from app.epg import EpgParseError
from app.hdhomerun import (
    DeviceFetchError,
    device_id_text,
    fetch_device,
    scan_lan,
)
from app.hub import MediaMTXError, StreamOffline, StreamStartError, TunersBusy
from app.public_url import is_loopback, playback_base, request_hostname, rewrite_playback
from app.importer import (
    PlaylistFetchError,
    fetch_playlist_text,
    import_playlist_text,
    load_playlist_source,
    store_playlist_file,
)
from app.playlist import MAX_PLAYLIST_BYTES, PlaylistParseError, parse_playlist

router = APIRouter()
log = logging.getLogger(__name__)

SORTS = {"title", "status", "language", "country", "number"}


def _db(request: Request):
    return connect(request.app.state.cfg.database_path)


class PlaylistURL(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str
    interval_minutes: int | None = None


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    interval_minutes: int | None = None
    clear_interval: bool = False


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class TagBulk(BaseModel):
    channel_ids: list[int]
    action: str


class ChannelPatch(BaseModel):
    language: str | None = None
    country: str | None = None
    adult: bool | None = None
    guide_number: str | None = None
    logo_url: str | None = None
    tvg_id: str | None = None


class BulkPatch(BaseModel):
    ids: list[int]
    language: str | None = None
    country: str | None = None
    adult: bool | None = None
    add_tag_ids: list[int] = Field(default_factory=list)
    remove_tag_ids: list[int] = Field(default_factory=list)


class DeviceIn(BaseModel):
    host: str


class FeedIn(BaseModel):
    url: str


class FeedPatch(BaseModel):
    enabled: bool


class SettingsPatch(BaseModel):
    hdhr_emulation: bool | None = None
    emulated_tuner_count: int | None = None
    emulated_friendly_name: str | None = None
    idle_timeout_seconds: int | None = None
    verify_interval_minutes: int | None = None
    verify_concurrency: int | None = None
    verify_timeout_seconds: int | None = None
    public_http_base: str | None = None
    public_webrtc_base: str | None = None
    public_hls_base: str | None = None
    webrtc_additional_host: str | None = None


class EpgURL(BaseModel):
    url: str


def _interval(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 5 or value > 10080:
        raise HTTPException(400, "interval_minutes must be between 5 and 10080")
    return value


def _playlist_json(row, counts: dict | None = None) -> dict:
    payload = {
        "id": row["id"],
        "name": row["name"],
        "source_url": row["source_url"],
        "file_path": row["file_path"],
        "interval_minutes": row["interval_minutes"],
        "last_imported_at": row["last_imported_at"],
        "created_at": row["created_at"],
    }
    if counts is not None:
        payload.update(counts)
    return payload


def _playlist_counts(conn, playlist_id: int) -> dict:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS channel_count,
            COALESCE(SUM(CASE WHEN status = 'unknown' THEN 1 ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END), 0) AS online,
            COALESCE(SUM(CASE WHEN status = 'offline' THEN 1 ELSE 0 END), 0) AS offline
        FROM channels
        WHERE playlist_id = ?
        """,
        (playlist_id,),
    ).fetchone()
    queued = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM verify_queue
        JOIN channels ON channels.id = verify_queue.channel_id
        WHERE channels.playlist_id = ?
        """,
        (playlist_id,),
    ).fetchone()["n"]
    count = int(row["channel_count"])
    pending = int(row["pending"])
    online = int(row["online"])
    offline = int(row["offline"])
    if count == 0:
        status = "empty"
    elif pending:
        status = "checking"
    elif online and offline:
        status = "mixed"
    elif offline:
        status = "offline"
    else:
        status = "online"
    return {
        "channel_count": count,
        "pending": pending,
        "online": online,
        "offline": offline,
        "queued": int(queued),
        "status": status,
    }


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(400, "playlist name is empty")
    return cleaned


def _reject_duplicate_playlist(conn, name: str, url: str | None) -> None:
    if url:
        existing = conn.execute("SELECT id FROM playlists WHERE source_url = ?", (url,)).fetchone()
        if existing is not None:
            raise HTTPException(409, "a playlist with this URL already exists")
    existing = conn.execute(
        "SELECT id FROM playlists WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if existing is not None:
        raise HTTPException(409, "a playlist with this name already exists")


@router.get("/api/health")
def health() -> dict:
    return {"ok": True}


@router.get("/api/channels")
def list_channels(
    request: Request,
    q: str | None = None,
    tag: list[str] = Query(default=[]),
    status: str | None = None,
    language: list[str] = Query(default=[]),
    country: list[str] = Query(default=[]),
    include_adult: bool = False,
    sort: str = "number",
    order: str = "asc",
    limit: int = 200,
    offset: int = 0,
) -> dict:
    if sort not in SORTS:
        raise HTTPException(400, "unknown sort")
    if order not in {"asc", "desc"}:
        raise HTTPException(400, "unknown order")
    if status is not None and status not in {"unknown", "online", "offline"}:
        raise HTTPException(400, "unknown status")
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    conn = _db(request)
    try:
        return search_channels(
            conn,
            q=q,
            tags=tag,
            status=status,
            languages=[item.lower() for item in language],
            countries=[item.upper() for item in country],
            include_adult=include_adult,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    finally:
        conn.close()


@router.get("/api/channels/{channel_id}")
def get_channel(request: Request, channel_id: int, include_adult: bool = False) -> dict:
    conn = _db(request)
    try:
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if row is None or (row["adult"] and not include_adult):
            raise HTTPException(404, "channel not found")
        tags = channel_tags(conn, [channel_id]).get(channel_id, [])
        return channel_json(row, tags)
    finally:
        conn.close()


@router.patch("/api/channels/{channel_id}")
def patch_channel(request: Request, channel_id: int, body: ChannelPatch) -> dict:
    fields = body.model_dump(exclude_unset=True)
    conn = _db(request)
    try:
        try:
            set_manual_fields(conn, channel_id, fields)
        except KeyError:
            raise HTTPException(404, "channel not found") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        tags = channel_tags(conn, [channel_id]).get(channel_id, [])
        return channel_json(row, tags)
    finally:
        conn.close()


def _channel_and_device(request: Request, channel_id: int, include_adult: bool) -> tuple[dict, dict | None]:
    conn = _db(request)
    try:
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if row is None or (row["adult"] and not include_adult):
            raise HTTPException(404, "channel not found")
        device = None
        if row["device_id"]:
            device_row = conn.execute(
                "SELECT * FROM hdhomerun_devices WHERE id = ?",
                (row["device_id"],),
            ).fetchone()
            device = None if device_row is None else dict(device_row)
        return dict(row), device
    finally:
        conn.close()


def _stored(conn, key: str) -> str:
    return (setting(conn, key) or "").strip()


def _playback_config(request: Request) -> tuple[str, str, str]:
    cfg = request.app.state.cfg
    conn = _db(request)
    try:
        webrtc = _stored(conn, "public_webrtc_base") or cfg.public_webrtc_base
        hls = _stored(conn, "public_hls_base") or cfg.public_hls_base
        extra = _stored(conn, "webrtc_additional_host") or (cfg.webrtc_additional_host or "")
    finally:
        conn.close()
    return webrtc, hls, extra


def _advertise_for_request(request: Request) -> None:
    hostname = request_hostname(request.headers.get("host"))
    _webrtc, _hls, configured = _playback_config(request)
    host = configured if configured and not is_loopback(configured) else hostname
    if is_loopback(host):
        return
    try:
        request.app.state.hub.advertise_host(host)
    except MediaMTXError as exc:
        log.warning("WebRTC additional host was not applied: %s", exc)


@router.post("/api/channels/{channel_id}/watch")
def watch_channel(request: Request, channel_id: int, include_adult: bool = False) -> dict:
    channel, device = _channel_and_device(request, channel_id, include_adult)
    _advertise_for_request(request)
    try:
        info = request.app.state.hub.ensure(channel, device, origin="watch")
    except TunersBusy as exc:
        raise HTTPException(503, "all tuners busy") from exc
    except StreamOffline as exc:
        raise HTTPException(409, str(exc)) from exc
    except StreamStartError as exc:
        raise HTTPException(502, str(exc)) from exc
    webrtc, hls, _extra = _playback_config(request)
    scheme = request.url.scheme or "http"
    return rewrite_playback(
        info,
        request_hostname(request.headers.get("host")),
        webrtc,
        hls,
        scheme,
    )


@router.get("/api/channels/{channel_id}/stream")
def stream_status(request: Request, channel_id: int, include_adult: bool = False) -> dict:
    _channel_and_device(request, channel_id, include_adult)
    return request.app.state.hub.status(channel_id)


@router.post("/api/channels/bulk")
def bulk_channels(request: Request, body: BulkPatch) -> dict:
    if not body.ids:
        raise HTTPException(400, "ids is empty")
    fields = {}
    incoming = body.model_dump(exclude_unset=True)
    for key in ("language", "country", "adult"):
        if key in incoming:
            fields[key] = incoming[key]
    conn = _db(request)
    try:
        for channel_id in body.ids:
            row = conn.execute("SELECT id FROM channels WHERE id = ?", (channel_id,)).fetchone()
            if row is None:
                raise HTTPException(404, f"channel {channel_id} not found")
            try:
                set_manual_fields(conn, channel_id, fields)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            for tag_id in body.add_tag_ids:
                if conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone() is None:
                    raise HTTPException(404, f"tag {tag_id} not found")
                conn.execute(
                    "INSERT INTO channel_tags(channel_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (channel_id, tag_id),
                )
            for tag_id in body.remove_tag_ids:
                conn.execute(
                    "DELETE FROM channel_tags WHERE channel_id = ? AND tag_id = ?",
                    (channel_id, tag_id),
                )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM channels WHERE id IN (%s) ORDER BY guide_sort, id" % ",".join("?" for _ in body.ids),
            body.ids,
        ).fetchall()
        tags = channel_tags(conn, [row["id"] for row in rows])
        return {"channels": [channel_json(row, tags.get(row["id"], [])) for row in rows]}
    finally:
        conn.close()


class VerifyMany(BaseModel):
    ids: list[int] | None = None


@router.post("/api/playlists/{playlist_id}/verify", status_code=202)
def verify_playlist(request: Request, playlist_id: int) -> dict:
    conn = _db(request)
    try:
        playlist = conn.execute("SELECT id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if playlist is None:
            raise HTTPException(404, "playlist not found")
        rows = conn.execute("SELECT id FROM channels WHERE playlist_id = ?", (playlist_id,)).fetchall()
        for row in rows:
            enqueue(conn, row["id"])
        conn.commit()
        return {"queued": len(rows)}
    finally:
        conn.close()


@router.post("/api/channels/verify", status_code=202)
def verify_many(request: Request, body: VerifyMany | None = None) -> dict:
    conn = _db(request)
    try:
        ids = None if body is None else body.ids
        if ids is not None and len(ids) == 0:
            return {"queued": 0}
        if ids:
            rows = conn.execute(
                "SELECT id FROM channels WHERE id IN (%s)" % ",".join("?" for _ in ids),
                ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM channels").fetchall()
        for row in rows:
            enqueue(conn, row["id"])
        conn.commit()
        return {"queued": len(rows)}
    finally:
        conn.close()


@router.post("/api/channels/{channel_id}/verify", status_code=202)
def verify_one(request: Request, channel_id: int) -> dict:
    conn = _db(request)
    try:
        row = conn.execute("SELECT id FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "channel not found")
        enqueue(conn, channel_id)
        conn.commit()
    finally:
        conn.close()
    return {"queued": True, "channel_id": channel_id}


@router.get("/api/tags")
def list_tags(request: Request, include_adult: bool = False) -> dict:
    conn = _db(request)
    try:
        adult_sql = "" if include_adult else "AND c.adult = 0"
        rows = conn.execute(
            f"""
            SELECT t.id, t.name, COUNT(c.id) AS channel_count
            FROM tags t
            LEFT JOIN channel_tags ct ON ct.tag_id = t.id
            LEFT JOIN channels c ON c.id = ct.channel_id {adult_sql}
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE
            """
        ).fetchall()
        return {
            "tags": [
                {"id": row["id"], "name": row["name"], "channel_count": row["channel_count"]}
                for row in rows
            ]
        }
    finally:
        conn.close()


@router.post("/api/tags", status_code=201)
def create_tag(request: Request, body: TagIn) -> dict:
    conn = _db(request)
    try:
        try:
            conn.execute("INSERT INTO tags(name) VALUES (?)", (body.name.strip(),))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "tag already exists") from exc
        conn.commit()
        row = conn.execute("SELECT id, name FROM tags WHERE name = ? COLLATE NOCASE", (body.name.strip(),)).fetchone()
        return {"id": row["id"], "name": row["name"]}
    finally:
        conn.close()


@router.patch("/api/tags/{tag_id}")
def rename_tag(request: Request, tag_id: int, body: TagIn) -> dict:
    conn = _db(request)
    try:
        try:
            cur = conn.execute("UPDATE tags SET name = ? WHERE id = ?", (body.name.strip(), tag_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "tag already exists") from exc
        if cur.rowcount == 0:
            raise HTTPException(404, "tag not found")
        conn.commit()
        return {"id": tag_id, "name": body.name.strip()}
    finally:
        conn.close()


@router.delete("/api/tags/{tag_id}", status_code=204)
def delete_tag(request: Request, tag_id: int) -> None:
    conn = _db(request)
    try:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "tag not found")
        conn.commit()
    finally:
        conn.close()


@router.post("/api/tags/{tag_id}/channels")
def tag_channels(request: Request, tag_id: int, body: TagBulk) -> dict:
    if body.action not in {"add", "remove"}:
        raise HTTPException(400, "action must be add or remove")
    if not body.channel_ids:
        raise HTTPException(400, "channel_ids is empty")
    conn = _db(request)
    try:
        tag = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if tag is None:
            raise HTTPException(404, "tag not found")
        for channel_id in body.channel_ids:
            if conn.execute("SELECT id FROM channels WHERE id = ?", (channel_id,)).fetchone() is None:
                raise HTTPException(404, f"channel {channel_id} not found")
            if body.action == "add":
                conn.execute(
                    "INSERT INTO channel_tags(channel_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (channel_id, tag_id),
                )
            else:
                conn.execute(
                    "DELETE FROM channel_tags WHERE channel_id = ? AND tag_id = ?",
                    (channel_id, tag_id),
                )
        conn.commit()
        return {"tag_id": tag_id, "action": body.action, "channel_ids": body.channel_ids}
    finally:
        conn.close()


def _create_playlist(conn, name: str, url: str | None, interval: int | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO playlists(name, source_url, interval_minutes, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name.strip(), url, interval, utcnow()),
    )
    return int(cur.lastrowid)


@router.get("/api/playlists")
def list_playlists(request: Request) -> dict:
    conn = _db(request)
    try:
        rows = conn.execute("SELECT * FROM playlists ORDER BY name COLLATE NOCASE").fetchall()
        return {"playlists": [_playlist_json(row, _playlist_counts(conn, row["id"])) for row in rows]}
    finally:
        conn.close()


@router.post("/api/playlists", status_code=201)
def create_playlist_url(request: Request, body: PlaylistURL) -> dict:
    interval = _interval(body.interval_minutes)
    name = _clean_name(body.name)
    try:
        text = fetch_playlist_text(body.url)
    except PlaylistFetchError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        parse_playlist(text)
    except PlaylistParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = _db(request)
    try:
        _reject_duplicate_playlist(conn, name, body.url)
        playlist_id = _create_playlist(conn, name, body.url, interval)
        conn.commit()
    finally:
        conn.close()
    try:
        result = import_playlist_text(request.app.state.cfg.database_path, playlist_id, text)
    except PlaylistParseError as exc:
        _delete_playlist(request, playlist_id)
        raise HTTPException(400, str(exc)) from exc
    return result


@router.post("/api/playlists/upload", status_code=201)
async def upload_playlist(
    request: Request,
    name: str = Form(...),
    interval_minutes: int | None = Form(None),
    file: UploadFile = File(...),
) -> dict:
    interval = _interval(interval_minutes)
    cleaned = _clean_name(name)
    raw = await file.read(MAX_PLAYLIST_BYTES + 1)
    if len(raw) > MAX_PLAYLIST_BYTES:
        raise HTTPException(413, "playlist is too large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "playlist must be UTF-8") from exc
    try:
        parse_playlist(text)
    except PlaylistParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = _db(request)
    try:
        _reject_duplicate_playlist(conn, cleaned, None)
        playlist_id = _create_playlist(conn, cleaned, None, interval)
        conn.commit()
    finally:
        conn.close()
    path = store_playlist_file(request.app.state.cfg.data_dir, playlist_id, text)
    conn = _db(request)
    try:
        conn.execute("UPDATE playlists SET file_path = ? WHERE id = ?", (path, playlist_id))
        conn.commit()
    finally:
        conn.close()
    try:
        return import_playlist_text(request.app.state.cfg.database_path, playlist_id, text)
    except PlaylistParseError as exc:
        _delete_playlist(request, playlist_id)
        raise HTTPException(400, str(exc)) from exc


def _delete_playlist(request: Request, playlist_id: int) -> None:
    conn = _db(request)
    try:
        conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        conn.commit()
    finally:
        conn.close()


@router.post("/api/playlists/{playlist_id}/import")
def reimport_playlist(request: Request, playlist_id: int) -> dict:
    try:
        text = load_playlist_source(request.app.state.cfg.database_path, playlist_id)
        return import_playlist_text(request.app.state.cfg.database_path, playlist_id, text)
    except KeyError:
        raise HTTPException(404, "playlist not found") from None
    except PlaylistFetchError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PlaylistParseError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/api/playlists/{playlist_id}")
def patch_playlist(request: Request, playlist_id: int, body: PlaylistPatch) -> dict:
    interval = _interval(body.interval_minutes) if "interval_minutes" in body.model_fields_set else None
    conn = _db(request)
    try:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "playlist not found")
        name = body.name.strip() if body.name is not None else row["name"]
        if body.clear_interval:
            minutes = None
        elif "interval_minutes" in body.model_fields_set:
            minutes = interval
        else:
            minutes = row["interval_minutes"]
        conn.execute(
            "UPDATE playlists SET name = ?, interval_minutes = ? WHERE id = ?",
            (name, minutes, playlist_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return _playlist_json(updated)
    finally:
        conn.close()


@router.delete("/api/playlists/{playlist_id}", status_code=204)
def delete_playlist(request: Request, playlist_id: int) -> None:
    conn = _db(request)
    try:
        cur = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "playlist not found")
        conn.commit()
    finally:
        conn.close()


def _feed_json(row, countries: set[str]) -> dict:
    from app.epg_feeds import feed_is_active

    return {
        "id": row["id"],
        "url": row["url"],
        "country": row["country"],
        "enabled": bool(row["enabled"]),
        "builtin": bool(row["builtin"]),
        "active": feed_is_active(row, countries),
        "last_fetched_at": row["last_fetched_at"],
        "last_status": row["last_status"],
        "last_matched": row["last_matched"],
    }


@router.get("/api/epg/feeds")
def list_feeds(request: Request) -> dict:
    from app.epg_feeds import catalog_countries, ensure_builtin

    conn = _db(request)
    try:
        ensure_builtin(conn)
        conn.commit()
        countries = catalog_countries(conn)
        rows = conn.execute("SELECT * FROM epg_feeds ORDER BY country, url").fetchall()
        return {"feeds": [_feed_json(row, countries) for row in rows]}
    finally:
        conn.close()


@router.post("/api/epg/feeds", status_code=201)
def add_feed(request: Request, body: FeedIn) -> dict:
    parsed = urlparse(body.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "guide URL must be http or https")
    conn = _db(request)
    try:
        try:
            conn.execute(
                "INSERT INTO epg_feeds(url, country, enabled, builtin) VALUES (?, NULL, 1, 0)",
                (body.url.strip(),),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "guide URL already exists") from exc
        conn.commit()
        row = conn.execute("SELECT * FROM epg_feeds WHERE url = ?", (body.url.strip(),)).fetchone()
        from app.epg_feeds import catalog_countries

        return _feed_json(row, catalog_countries(conn))
    finally:
        conn.close()


@router.patch("/api/epg/feeds/{feed_id}")
def patch_feed(request: Request, feed_id: int, body: FeedPatch) -> dict:
    conn = _db(request)
    try:
        cur = conn.execute(
            "UPDATE epg_feeds SET enabled = ? WHERE id = ?",
            (1 if body.enabled else 0, feed_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "guide feed not found")
        conn.commit()
        row = conn.execute("SELECT * FROM epg_feeds WHERE id = ?", (feed_id,)).fetchone()
        from app.epg_feeds import catalog_countries

        return _feed_json(row, catalog_countries(conn))
    finally:
        conn.close()


@router.post("/api/epg")
def import_epg_url(request: Request, body: EpgURL) -> dict:
    parsed = urlparse(body.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "epg URL must be http or https")
    try:
        response = httpx.get(body.url, timeout=60, follow_redirects=True, headers={"User-Agent": "HomeIPTV/0.1"})
    except httpx.HTTPError as exc:
        raise HTTPException(422, f"epg request failed: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(422, f"epg URL returned HTTP {response.status_code}")
    return _store_epg(request, response.text)


@router.post("/api/epg/upload")
async def import_epg_file(request: Request, file: UploadFile = File(...)) -> dict:
    raw = await file.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "epg file is too large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "epg file must be UTF-8") from exc
    return _store_epg(request, text)


def _store_epg(request: Request, text: str) -> dict:
    conn = _db(request)
    try:
        try:
            result = replace_epg(conn, text)
        except EpgParseError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        return result
    finally:
        conn.close()


@router.get("/api/programs")
def list_programs(
    request: Request,
    start: str,
    end: str,
    include_adult: bool = False,
) -> dict:
    try:
        window_start = stamp(parse_stamp(start))
        window_end = stamp(parse_stamp(end))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = _db(request)
    try:
        return {"programs": programs_for(conn, window_start, window_end, include_adult)}
    finally:
        conn.close()


@router.post("/api/hdhomerun/discover")
def discover_hdhomerun(request: Request) -> dict:
    found = scan_lan(request.app.state.cfg.discovery_timeout_seconds)
    devices = []
    seen = set()
    for address, packet in found:
        identity = device_id_text(packet.device_id) or packet.base_url or address[0]
        if identity in seen:
            continue
        seen.add(identity)
        devices.append(
            {
                "address": address[0],
                "device_id": device_id_text(packet.device_id),
                "tuner_count": packet.tuner_count,
                "base_url": packet.base_url or f"http://{address[0]}",
                "lineup_url": packet.lineup_url,
            }
        )
    return {"devices": devices}


@router.get("/api/hdhomerun/devices")
def list_devices(request: Request) -> dict:
    conn = _db(request)
    try:
        rows = conn.execute("SELECT * FROM hdhomerun_devices ORDER BY host").fetchall()
        return {"devices": [_device_json(row) for row in rows]}
    finally:
        conn.close()


def _device_json(row) -> dict:
    return {
        "id": row["id"],
        "host": row["host"],
        "device_id": row["device_id"],
        "friendly_name": row["friendly_name"],
        "model": row["model"],
        "tuner_count": row["tuner_count"],
        "base_url": row["base_url"],
        "lineup_url": row["lineup_url"],
        "manual": bool(row["manual"]),
        "last_seen_at": row["last_seen_at"],
    }


@router.post("/api/hdhomerun/devices", status_code=201)
def add_device(request: Request, body: DeviceIn) -> dict:
    try:
        canonical, info, channels = fetch_device(body.host)
    except DeviceFetchError as exc:
        raise HTTPException(422, str(exc)) from exc
    conn = _db(request)
    try:
        conn.execute(
            """
            INSERT INTO hdhomerun_devices (
                host, device_id, friendly_name, model, tuner_count, base_url, lineup_url, manual, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(host) DO UPDATE SET
                device_id = excluded.device_id,
                friendly_name = excluded.friendly_name,
                model = excluded.model,
                tuner_count = excluded.tuner_count,
                base_url = excluded.base_url,
                lineup_url = excluded.lineup_url,
                manual = 1,
                last_seen_at = excluded.last_seen_at
            """,
            (
                canonical,
                info["device_id"],
                info["friendly_name"],
                info["model"],
                info["tuner_count"],
                canonical,
                info["lineup_url"],
                utcnow(),
            ),
        )
        row = conn.execute("SELECT * FROM hdhomerun_devices WHERE host = ?", (canonical,)).fetchone()
        summary = import_lineup_channels(conn, row["id"], channels)
        conn.commit()
        return {"device": _device_json(row), **summary}
    finally:
        conn.close()


@router.post("/api/hdhomerun/devices/{device_id}/import")
def refresh_device(request: Request, device_id: int) -> dict:
    conn = _db(request)
    try:
        row = conn.execute("SELECT * FROM hdhomerun_devices WHERE id = ?", (device_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "device not found")
    try:
        canonical, info, channels = fetch_device(row["host"])
    except DeviceFetchError as exc:
        raise HTTPException(422, str(exc)) from exc
    conn = _db(request)
    try:
        conn.execute(
            """
            UPDATE hdhomerun_devices
            SET device_id = ?, friendly_name = ?, model = ?, tuner_count = ?,
                base_url = ?, lineup_url = ?, last_seen_at = ?
            WHERE id = ?
            """,
            (
                info["device_id"],
                info["friendly_name"],
                info["model"],
                info["tuner_count"],
                canonical,
                info["lineup_url"],
                utcnow(),
                device_id,
            ),
        )
        summary = import_lineup_channels(conn, device_id, channels)
        conn.commit()
        return summary
    finally:
        conn.close()


@router.delete("/api/hdhomerun/devices/{device_id}", status_code=204)
def delete_device(request: Request, device_id: int) -> None:
    conn = _db(request)
    try:
        cur = conn.execute("DELETE FROM hdhomerun_devices WHERE id = ?", (device_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "device not found")
        conn.commit()
    finally:
        conn.close()


def _optional_int(conn, key: str, fallback: int, minimum: int) -> int:
    raw = _stored(conn, key)
    if not raw:
        return fallback
    try:
        return max(minimum, int(raw))
    except ValueError:
        return fallback


def _port(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw else fallback
    except ValueError:
        return fallback


@router.get("/api/settings")
def get_settings(request: Request) -> dict:
    cfg = request.app.state.cfg
    conn = _db(request)
    try:
        hostname = request_hostname(request.headers.get("host"))
        http_base = _stored(conn, "public_http_base") or cfg.public_http_base
        webrtc_base = _stored(conn, "public_webrtc_base") or cfg.public_webrtc_base
        hls_base = _stored(conn, "public_hls_base") or cfg.public_hls_base
        extra = _stored(conn, "webrtc_additional_host") or (cfg.webrtc_additional_host or "")
        scheme = request.url.scheme or "http"
        return {
            "hdhr_emulation": setting(conn, "hdhr_emulation") == "1",
            "emulated_tuner_count": int(setting(conn, "emulated_tuner_count") or "4"),
            "emulated_friendly_name": setting(conn, "emulated_friendly_name") or "Home IPTV",
            "idle_timeout_seconds": _optional_int(conn, "idle_timeout_seconds", cfg.idle_timeout_seconds, 5),
            "verify_interval_minutes": _optional_int(conn, "verify_interval_minutes", cfg.verify_interval_minutes, 5),
            "verify_concurrency": _optional_int(conn, "verify_concurrency", cfg.verify_concurrency, 1),
            "verify_timeout_seconds": _optional_int(conn, "verify_timeout_seconds", cfg.verify_timeout_seconds, 3),
            "public_http_base": _stored(conn, "public_http_base"),
            "public_webrtc_base": _stored(conn, "public_webrtc_base"),
            "public_hls_base": _stored(conn, "public_hls_base"),
            "webrtc_additional_host": _stored(conn, "webrtc_additional_host"),
            "effective_http_base": playback_base(http_base, hostname, _port("WEB_PORT", 9595), scheme),
            "effective_webrtc_base": playback_base(webrtc_base, hostname, _port("WEBRTC_PORT", 8889), scheme),
            "effective_hls_base": playback_base(hls_base, hostname, _port("HLS_PORT", 8888), scheme),
            "effective_webrtc_host": extra if extra and not is_loopback(extra) else ("" if is_loopback(hostname) else hostname),
            "ports": {
                "http": _port("WEB_PORT", 9595),
                "webrtc": _port("WEBRTC_PORT", 8889),
                "webrtc_udp": _port("WEBRTC_UDP_PORT", 8189),
                "hls": _port("HLS_PORT", 8888),
            },
            "ports_note": "Host ports are set in Compose or the Unraid template.",
        }
    finally:
        conn.close()


@router.patch("/api/settings")
def patch_settings(request: Request, body: SettingsPatch) -> dict:
    conn = _db(request)
    try:
        data = body.model_dump(exclude_unset=True)
        if "emulated_tuner_count" in data:
            if data["emulated_tuner_count"] is None or data["emulated_tuner_count"] < 1:
                raise HTTPException(400, "emulated_tuner_count must be at least 1")
            set_setting(conn, "emulated_tuner_count", str(data["emulated_tuner_count"]))
        if "idle_timeout_seconds" in data:
            if data["idle_timeout_seconds"] is None or data["idle_timeout_seconds"] < 5:
                raise HTTPException(400, "idle_timeout_seconds must be at least 5")
            set_setting(conn, "idle_timeout_seconds", str(data["idle_timeout_seconds"]))
        if "verify_interval_minutes" in data:
            if data["verify_interval_minutes"] is None or data["verify_interval_minutes"] < 5:
                raise HTTPException(400, "verify_interval_minutes must be at least 5")
            set_setting(conn, "verify_interval_minutes", str(data["verify_interval_minutes"]))
        if "verify_concurrency" in data:
            if data["verify_concurrency"] is None or data["verify_concurrency"] < 1:
                raise HTTPException(400, "verify_concurrency must be at least 1")
            set_setting(conn, "verify_concurrency", str(data["verify_concurrency"]))
        if "verify_timeout_seconds" in data:
            if data["verify_timeout_seconds"] is None or data["verify_timeout_seconds"] < 3:
                raise HTTPException(400, "verify_timeout_seconds must be at least 3")
            set_setting(conn, "verify_timeout_seconds", str(data["verify_timeout_seconds"]))
        for key in ("public_http_base", "public_webrtc_base", "public_hls_base"):
            if key not in data or data[key] is None:
                continue
            value = data[key].strip()
            if value:
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise HTTPException(400, f"{key} must be an http or https URL")
            set_setting(conn, key, value)
        if "webrtc_additional_host" in data and data["webrtc_additional_host"] is not None:
            set_setting(conn, "webrtc_additional_host", data["webrtc_additional_host"].strip())
        if "hdhr_emulation" in data and data["hdhr_emulation"] is not None:
            set_setting(conn, "hdhr_emulation", "1" if data["hdhr_emulation"] else "0")
        if "emulated_friendly_name" in data and data["emulated_friendly_name"] is not None:
            name = data["emulated_friendly_name"].strip()
            if not name:
                raise HTTPException(400, "emulated_friendly_name is empty")
            set_setting(conn, "emulated_friendly_name", name)
        conn.commit()
    finally:
        conn.close()
    return get_settings(request)
