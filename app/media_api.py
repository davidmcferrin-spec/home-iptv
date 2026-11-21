import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.db import connect, set_setting, setting, utcnow
from app.media_paths import PathEscape, file_inside, library_directory, media_root, suggest_libraries
from app.media_play import choose_mode, detect_accel, image_subtitle, text_subtitle, vtt_command
from app.media_scan import scan_library
from app.media_session import SessionBusy, SessionStartError, close_session, open_session, reap, session_dir

log = logging.getLogger(__name__)
router = APIRouter()
_scan_lock = threading.Lock()
_scanning: set[int] = set()
_KINDS = {"tv", "movies", "misc"}
_SEGMENT = re.compile(r"^seg\d+\.ts$")
_accel_cache = {"at": 0.0, "value": None}
_reaper_started = False


class LibraryBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=400)
    kind: str


class SettingsBody(BaseModel):
    tmdb_api_key: str = ""


class ProgressBody(BaseModel):
    position: float = 0
    duration: float | None = None
    watched: bool = False


def _conn():
    return connect(settings().database_path)


def _ensure_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def loop() -> None:
        while True:
            time.sleep(10)
            reap()

    threading.Thread(target=loop, name="media-reaper", daemon=True).start()


def start_scan(library_id: int) -> None:
    with _scan_lock:
        if library_id in _scanning:
            return
        _scanning.add(library_id)

    def run() -> None:
        cfg = settings()
        conn = connect(cfg.database_path)
        key = setting(conn, "tmdb_api_key") or ""
        client = httpx.Client(timeout=15) if key.strip() else None
        try:
            scan_library(conn, library_id, media_root(), Path(cfg.data_dir), client=client)
        except Exception as exc:
            log.exception("scan of library %s failed", library_id)
            conn.execute(
                """
                INSERT INTO media_scans (library_id, state, done, total, message, updated_at)
                VALUES (?, 'error', 0, 0, ?, ?)
                ON CONFLICT(library_id) DO UPDATE SET state = 'error', message = excluded.message, updated_at = excluded.updated_at
                """,
                (library_id, str(exc), utcnow()),
            )
            conn.commit()
        finally:
            if client is not None:
                client.close()
            conn.close()
            with _scan_lock:
                _scanning.discard(library_id)

    threading.Thread(target=run, name=f"media-scan-{library_id}", daemon=True).start()


def _library_json(row, scan) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "path": row["rel_path"],
        "kind": row["kind"],
        "scan": {
            "state": scan["state"] if scan else "idle",
            "done": scan["done"] if scan else 0,
            "total": scan["total"] if scan else 0,
            "message": scan["message"] if scan else "",
        },
    }


def _check_path(rel_path: str) -> None:
    root = media_root()
    if not root.is_dir():
        raise HTTPException(400, "/media is not mounted")
    try:
        directory = library_directory(root, rel_path)
    except PathEscape as exc:
        raise HTTPException(400, str(exc)) from exc
    if not directory.is_dir():
        raise HTTPException(400, f"{rel_path} is not a directory under /media")


@router.get("/api/media/suggestions")
def suggestions():
    conn = _conn()
    try:
        taken = {row["rel_path"] for row in conn.execute("SELECT rel_path FROM media_libraries")}
        return {"suggestions": suggest_libraries(media_root(), taken)}
    finally:
        conn.close()


@router.get("/api/media/libraries")
def list_libraries():
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM media_libraries ORDER BY name COLLATE NOCASE").fetchall()
        scans = {row["library_id"]: row for row in conn.execute("SELECT * FROM media_scans")}
        return {"libraries": [_library_json(row, scans.get(row["id"])) for row in rows]}
    finally:
        conn.close()


@router.post("/api/media/libraries", status_code=201)
def create_library(body: LibraryBody):
    if body.kind not in _KINDS:
        raise HTTPException(400, "kind must be tv, movies, or misc")
    rel = body.path.strip().strip("/")
    _check_path(rel)
    conn = _conn()
    try:
        exists = conn.execute("SELECT id FROM media_libraries WHERE rel_path = ? COLLATE NOCASE", (rel,)).fetchone()
        if exists:
            raise HTTPException(409, f"{rel} is already a library")
        cursor = conn.execute(
            "INSERT INTO media_libraries (name, rel_path, kind, created_at) VALUES (?, ?, ?, ?)",
            (body.name.strip(), rel, body.kind, utcnow()),
        )
        conn.commit()
        library_id = int(cursor.lastrowid)
    finally:
        conn.close()
    start_scan(library_id)
    return {"id": library_id}


@router.delete("/api/media/libraries/{library_id}")
def delete_library(library_id: int):
    conn = _conn()
    try:
        deleted = conn.execute("DELETE FROM media_libraries WHERE id = ?", (library_id,))
        conn.commit()
        if deleted.rowcount == 0:
            raise HTTPException(404, "library was not found")
    finally:
        conn.close()
    return {"deleted": library_id}


@router.post("/api/media/libraries/{library_id}/rescan")
def rescan(library_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM media_libraries WHERE id = ?", (library_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "library was not found")
    start_scan(library_id)
    return {"id": library_id, "state": "running"}


@router.get("/api/media/settings")
def media_settings():
    conn = _conn()
    try:
        key = (setting(conn, "tmdb_api_key") or "").strip()
        return {"tmdb_configured": bool(key)}
    finally:
        conn.close()


@router.put("/api/media/settings")
def save_media_settings(body: SettingsBody):
    conn = _conn()
    try:
        set_setting(conn, "tmdb_api_key", body.tmdb_api_key.strip())
        conn.commit()
    finally:
        conn.close()
    return {"tmdb_configured": bool(body.tmdb_api_key.strip())}


@router.get("/api/media/items")
def list_items(kind: str = "", q: str = ""):
    if kind and kind not in {"show", "movie", "file"}:
        raise HTTPException(400, "kind must be show, movie, or file")
    conn = _conn()
    try:
        sql = """
            SELECT items.*, libraries.kind AS library_kind, libraries.name AS library_name
            FROM media_items items
            JOIN media_libraries libraries ON libraries.id = items.library_id
            WHERE items.parent_id IS NULL
        """
        args: list = []
        if kind:
            sql += " AND items.kind = ?"
            args.append(kind)
        if q.strip():
            sql += " AND items.title LIKE ? ESCAPE '\\'"
            args.append("%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY items.sort_title"
        rows = conn.execute(sql, args).fetchall()
        return {"items": [_item_json(conn, row) for row in rows]}
    finally:
        conn.close()


@router.get("/api/media/items/{item_id}")
def item_detail(item_id: int):
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT items.*, libraries.kind AS library_kind, libraries.name AS library_name
            FROM media_items items
            JOIN media_libraries libraries ON libraries.id = items.library_id
            WHERE items.id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "item was not found")
        payload = _item_json(conn, row)
        payload["children"] = [
            _item_json(conn, child)
            for child in conn.execute(
                "SELECT * FROM media_items WHERE parent_id = ? ORDER BY season_number, episode_number, sort_title",
                (item_id,),
            )
        ]
        if row["kind"] == "show":
            for child in payload["children"]:
                child["children"] = [
                    _item_json(conn, episode)
                    for episode in conn.execute(
                        "SELECT * FROM media_items WHERE parent_id = ? ORDER BY episode_number, sort_title",
                        (child["id"],),
                    )
                ]
        return payload
    finally:
        conn.close()


@router.get("/api/media/items/{item_id}/poster")
def item_poster(item_id: int):
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT items.poster_rel, items.poster_file, libraries.rel_path
            FROM media_items items
            JOIN media_libraries libraries ON libraries.id = items.library_id
            WHERE items.id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "item was not found")
        if row["poster_file"]:
            path = Path(settings().data_dir) / row["poster_file"]
            data = Path(settings().data_dir).resolve()
            try:
                ready = path.resolve()
            except OSError as exc:
                raise HTTPException(404, "poster is missing") from exc
            if data not in ready.parents or not ready.is_file():
                raise HTTPException(404, "poster is missing")
            return FileResponse(ready)
        if row["poster_rel"]:
            try:
                directory = library_directory(media_root(), row["rel_path"])
                ready = file_inside(directory, row["poster_rel"])
            except (PathEscape, FileNotFoundError) as exc:
                raise HTTPException(404, "poster is missing") from exc
            return FileResponse(ready)
        raise HTTPException(404, "poster is missing")
    finally:
        conn.close()


@router.put("/api/media/files/{file_id}/progress")
def save_progress(file_id: int, body: ProgressBody):
    conn = _conn()
    try:
        found = conn.execute("SELECT id, duration FROM media_files WHERE id = ?", (file_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "file was not found")
        duration = body.duration if body.duration is not None else found["duration"]
        watched = 1 if body.watched else 0
        if duration and body.position >= max(duration - 10, duration * 0.9):
            watched = 1
        conn.execute(
            """
            INSERT INTO media_progress (file_id, position, duration, watched, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                position = excluded.position, duration = excluded.duration,
                watched = excluded.watched, updated_at = excluded.updated_at
            """,
            (file_id, max(0, body.position), duration, watched, utcnow()),
        )
        conn.commit()
        return {"file_id": file_id, "position": max(0, body.position), "watched": bool(watched)}
    finally:
        conn.close()


@router.get("/api/media/continue")
def continue_watching():
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT files.id AS file_id, files.rel_path, files.duration AS file_duration,
                   progress.position, progress.updated_at, items.id AS item_id, items.title,
                   items.kind, items.season_number, items.episode_number, items.parent_id
            FROM media_progress progress
            JOIN media_files files ON files.id = progress.file_id
            JOIN media_items items ON items.id = files.item_id
            WHERE progress.watched = 0 AND progress.position > 5
            ORDER BY progress.updated_at DESC
            LIMIT 40
            """
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


def _file_row(conn, file_id: int):
    return conn.execute(
        """
        SELECT files.*, libraries.rel_path AS library_path
        FROM media_files files
        JOIN media_libraries libraries ON libraries.id = files.library_id
        WHERE files.id = ?
        """,
        (file_id,),
    ).fetchone()


def _media_file(row) -> Path:
    try:
        directory = library_directory(media_root(), row["library_path"])
        return file_inside(directory, row["rel_path"])
    except PathEscape as exc:
        raise HTTPException(403, "path must stay inside the library") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "file is missing") from exc


@router.get("/api/media/accel")
def accel():
    now = time.monotonic()
    if _accel_cache["value"] is not None and now - _accel_cache["at"] < 60:
        return _accel_cache["value"]
    value = detect_accel()
    _accel_cache["at"] = now
    _accel_cache["value"] = value
    return value


@router.get("/api/media/files/{file_id}/play")
def play_file(
    file_id: int,
    start: float = 0,
    audio: int = 0,
    subtitle: int | None = None,
):
    _ensure_reaper()
    conn = _conn()
    try:
        row = _file_row(conn, file_id)
        if row is None:
            raise HTTPException(404, "file was not found")
        path = _media_file(row)
        audio_tracks = json.loads(row["audio_tracks"] or "[]")
        subtitle_tracks = json.loads(row["subtitle_tracks"] or "[]")
        if audio < 0 or (audio_tracks and audio >= len(audio_tracks)):
            raise HTTPException(400, "audio track was not found")
        burn = None
        vtt = None
        if subtitle is not None:
            if subtitle < 0 or subtitle >= len(subtitle_tracks):
                raise HTTPException(400, "subtitle track was not found")
            codec = subtitle_tracks[subtitle].get("codec")
            if image_subtitle(codec):
                burn = subtitle
            elif text_subtitle(codec):
                vtt = f"/api/media/files/{file_id}/subtitles/{subtitle_tracks[subtitle].get('index')}.vtt"
            else:
                raise HTTPException(400, f"subtitle codec {codec} is not supported")
        mode = choose_mode(row["container"], row["video_codec"], [track.get("codec") or "" for track in audio_tracks])
        payload = {
            "file_id": file_id,
            "mode": "direct" if mode == "direct" else "hls",
            "plan": mode,
            "start": max(0, start),
            "duration": row["duration"],
            "audio": audio_tracks,
            "subtitles": subtitle_tracks,
            "vtt": vtt,
        }
        if mode == "direct":
            payload["url"] = f"/api/media/files/{file_id}/raw"
            return payload
        status = accel()
        device = status["device"] if status.get("available") else None
        try:
            session = open_session(
                source=str(path),
                work_root=Path(settings().data_dir) / "media-hls",
                mode=mode,
                start=max(0, start),
                audio_index=audio,
                burn_subtitle=burn,
                vaapi_device=device,
            )
        except SessionBusy as exc:
            raise HTTPException(429, str(exc)) from exc
        except SessionStartError as exc:
            raise HTTPException(502, str(exc)) from exc
        payload["session"] = session.id
        payload["url"] = f"/api/media/sessions/{session.id}/index.m3u8"
        payload["file_start"] = session.file_start
        return payload
    finally:
        conn.close()


@router.get("/api/media/files/{file_id}/raw")
def raw_file(file_id: int):
    conn = _conn()
    try:
        row = _file_row(conn, file_id)
        if row is None:
            raise HTTPException(404, "file was not found")
        return FileResponse(_media_file(row))
    finally:
        conn.close()


@router.get("/api/media/files/{file_id}/subtitles/{stream_index}.vtt")
def subtitle_vtt(file_id: int, stream_index: int):
    conn = _conn()
    try:
        row = _file_row(conn, file_id)
        if row is None:
            raise HTTPException(404, "file was not found")
        tracks = json.loads(row["subtitle_tracks"] or "[]")
        if not any(track.get("index") == stream_index and text_subtitle(track.get("codec")) for track in tracks):
            raise HTTPException(404, "text subtitle was not found")
        path = _media_file(row)
        dest = Path(settings().data_dir) / "media-vtt" / f"{file_id}-{stream_index}.vtt"
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(vtt_command("ffmpeg", str(path), stream_index, str(dest)), capture_output=True, timeout=40, check=False)
            if proc.returncode != 0 or not dest.is_file():
                dest.unlink(missing_ok=True)
                detail = proc.stderr.decode(errors="replace").strip().splitlines()
                tail = detail[-1] if detail else f"ffmpeg exited {proc.returncode}"
                raise HTTPException(502, f"subtitle extract failed: {tail}")
        return FileResponse(dest, media_type="text/vtt")
    finally:
        conn.close()


@router.get("/api/media/sessions/{session_id}/{name}")
def session_file(session_id: str, name: str):
    if name != "index.m3u8" and not _SEGMENT.match(name):
        raise HTTPException(404, "segment was not found")
    directory = session_dir(session_id)
    if directory is None:
        raise HTTPException(404, "transcode session was not found")
    path = directory / name
    if not path.is_file():
        raise HTTPException(404, "segment is not ready")
    media_type = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    return FileResponse(path, media_type=media_type)


@router.delete("/api/media/sessions/{session_id}")
def delete_session(session_id: str):
    close_session(session_id)
    return {"closed": session_id}


def _item_json(conn, row) -> dict:
    files = conn.execute(
        """
        SELECT files.id, files.rel_path, files.duration, files.container, files.video_codec,
               files.width, files.height, files.audio_tracks, files.subtitle_tracks,
               progress.position, progress.watched
        FROM media_files files
        LEFT JOIN media_progress progress ON progress.file_id = files.id
        WHERE files.item_id = ?
        ORDER BY files.rel_path
        """,
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "library_id": row["library_id"],
        "kind": row["kind"],
        "title": row["title"],
        "year": row["year"],
        "season": row["season_number"],
        "episode": row["episode_number"],
        "overview": row["overview"],
        "has_poster": bool(row["poster_rel"] or row["poster_file"]),
        "files": [
            {
                "id": item["id"],
                "path": item["rel_path"],
                "duration": item["duration"],
                "container": item["container"],
                "video_codec": item["video_codec"],
                "width": item["width"],
                "height": item["height"],
                "audio_tracks": json.loads(item["audio_tracks"] or "[]"),
                "subtitle_tracks": json.loads(item["subtitle_tracks"] or "[]"),
                "position": item["position"] or 0,
                "watched": bool(item["watched"]),
            }
            for item in files
        ],
    }
