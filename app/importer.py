import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.catalog import upsert_parsed_channel
from app.db import connect, utcnow
from app.playlist import MAX_PLAYLIST_BYTES, PlaylistParseError, parse_playlist

log = logging.getLogger(__name__)


class PlaylistFetchError(Exception):
    pass


def fetch_playlist_text(url: str) -> str:
    _require_http(url)
    try:
        response = httpx.get(
            url,
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "HomeIPTV/0.1"},
        )
    except httpx.ConnectError as exc:
        raise PlaylistFetchError(f"could not connect to playlist URL: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise PlaylistFetchError(f"playlist URL timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PlaylistFetchError(f"playlist request failed: {exc}") from exc
    if response.status_code != 200:
        raise PlaylistFetchError(f"playlist URL returned HTTP {response.status_code}")
    if len(response.content) > MAX_PLAYLIST_BYTES:
        raise PlaylistFetchError("playlist is too large")
    return response.text


def _require_http(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PlaylistFetchError("playlist URL must be http or https")


def import_playlist_text(database_path: str, playlist_id: int, text: str) -> dict:
    try:
        parsed = parse_playlist(text)
    except PlaylistParseError:
        raise
    conn = connect(database_path)
    try:
        playlist = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if playlist is None:
            raise KeyError(playlist_id)
        seen: set[str] = set()
        imported = 0
        updated = 0
        for channel in parsed.channels:
            outcome = upsert_parsed_channel(conn, channel, playlist_id, None)
            seen.add(channel.url)
            if outcome == "imported":
                imported += 1
            else:
                updated += 1
        removed = 0
        rows = conn.execute(
            "SELECT id, url FROM channels WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()
        for row in rows:
            if row["url"] not in seen:
                conn.execute("DELETE FROM channels WHERE id = ?", (row["id"],))
                removed += 1
        conn.execute(
            "UPDATE playlists SET last_imported_at = ? WHERE id = ?",
            (utcnow(), playlist_id),
        )
        conn.commit()
        return {
            "playlist_id": playlist_id,
            "imported": imported,
            "updated": updated,
            "removed": removed,
            "warnings": parsed.warnings,
        }
    finally:
        conn.close()


def store_playlist_file(data_dir: str, playlist_id: int, text: str) -> str:
    path = Path(data_dir) / "playlists" / f"{playlist_id}.m3u"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def load_playlist_source(database_path: str, playlist_id: int) -> str:
    conn = connect(database_path)
    try:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(playlist_id)
    if row["source_url"]:
        return fetch_playlist_text(row["source_url"])
    if row["file_path"]:
        path = Path(row["file_path"])
        if not path.is_file():
            raise PlaylistFetchError("stored playlist file is missing")
        return path.read_text(encoding="utf-8")
    raise PlaylistFetchError("playlist has no source")
