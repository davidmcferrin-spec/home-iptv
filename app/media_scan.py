import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.db import utcnow
from app.media_names import parse_video, sort_title

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts", ".mpg", ".mpeg", ".wmv"}
_POSTER_NAMES = {"poster.jpg", "poster.jpeg", "poster.png", "poster.webp", "folder.jpg", "folder.jpeg", "cover.jpg", "cover.png"}


@dataclass(frozen=True)
class ProbeResult:
    duration: float | None
    container: str | None
    video_codec: str | None
    width: int | None
    height: int | None
    audio: list[dict]
    subtitles: list[dict]


def ffprobe_file(path: Path) -> ProbeResult:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True,
            timeout=40,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffprobe failed for {path.name}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        tail = detail[-1] if detail else f"ffprobe exited {proc.returncode}"
        raise RuntimeError(f"ffprobe failed for {path.name}: {tail}")
    try:
        payload = json.loads(proc.stdout.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path.name}") from exc
    return parse_probe(payload)


def parse_probe(payload: dict) -> ProbeResult:
    fmt = payload.get("format") or {}
    duration = _float(fmt.get("duration"))
    container = fmt.get("format_name")
    video_codec = None
    width = None
    height = None
    audio = []
    subtitles = []
    for stream in payload.get("streams") or []:
        kind = stream.get("codec_type")
        tags = stream.get("tags") or {}
        language = tags.get("language") or tags.get("LANGUAGE")
        title = tags.get("title") or tags.get("TITLE")
        if kind == "video" and video_codec is None:
            video_codec = stream.get("codec_name")
            width = stream.get("width")
            height = stream.get("height")
        elif kind == "audio":
            audio.append({
                "index": stream.get("index"),
                "codec": stream.get("codec_name"),
                "language": language,
                "title": title,
                "channels": stream.get("channels"),
            })
        elif kind == "subtitle":
            subtitles.append({
                "index": stream.get("index"),
                "codec": stream.get("codec_name"),
                "language": language,
                "title": title,
            })
    return ProbeResult(duration, container, video_codec, width, height, audio, subtitles)


def grab_thumbnail(source: Path, dest: Path, duration: float | None) -> bool:
    offset = 1.0
    if duration and duration > 2:
        offset = min(duration * 0.1, 30)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.3f}",
                "-i", str(source), "-frames:v", "1", "-vf", "scale=320:-1", "-y", str(dest),
            ],
            capture_output=True,
            timeout=40,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("thumbnail failed for %s: %s", source.name, exc)
        return False
    if proc.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        return False
    return True


def local_poster(library_dir: Path, file_path: Path) -> str | None:
    directory = file_path.parent
    root = library_dir.resolve()
    while True:
        try:
            names = list(directory.iterdir())
        except OSError:
            names = []
        for candidate in names:
            if candidate.is_file() and candidate.name.casefold() in _POSTER_NAMES:
                try:
                    return candidate.resolve().relative_to(root).as_posix()
                except ValueError:
                    return None
        if directory.resolve() == root or root not in directory.resolve().parents:
            return None
        directory = directory.parent


def lookup_tmdb(client: httpx.Client, key: str, kind: str, title: str, year: int | None) -> dict | None:
    route = "search/tv" if kind == "tv" else "search/movie"
    params = {"api_key": key, "query": title}
    if year:
        params["first_air_date_year" if kind == "tv" else "year"] = str(year)
    try:
        response = client.get(f"https://api.themoviedb.org/3/{route}", params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("TMDB lookup failed for %s: %s", title, exc)
        return None
    results = payload.get("results") or []
    if not results:
        return None
    hit = results[0]
    return {
        "id": hit.get("id"),
        "overview": hit.get("overview") or "",
        "poster_path": hit.get("poster_path"),
        "title": hit.get("name") or hit.get("title") or title,
    }


def download_poster(client: httpx.Client, poster_path: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = client.get(f"https://image.tmdb.org/t/p/w342{poster_path}")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("TMDB poster download failed: %s", exc)
        return False
    dest.write_bytes(response.content)
    return True


def list_videos(library_dir: Path) -> list[Path]:
    root = library_dir.resolve()
    found = []
    for path in library_dir.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if root not in resolved.parents:
            continue
        found.append(path)
    return sorted(found, key=lambda item: item.as_posix().casefold())


def _float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item(conn, library_id: int, parent_id: int | None, kind: str, title: str, *, year=None, season=None, episode=None) -> int:
    row = conn.execute(
        """
        SELECT id FROM media_items
        WHERE library_id = ? AND parent_id IS ? AND kind = ? AND title = ? COLLATE NOCASE
          AND season_number IS ? AND episode_number IS ?
        """,
        (library_id, parent_id, kind, title, season, episode),
    ).fetchone()
    now = utcnow()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO media_items (
            library_id, parent_id, kind, title, sort_title, year, season_number, episode_number,
            overview, poster_rel, poster_file, tmdb_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', NULL, NULL, NULL, ?, ?)
        """,
        (library_id, parent_id, kind, title, sort_title(title), year, season, episode, now, now),
    )
    return int(cursor.lastrowid)


def _place(conn, library_id: int, kind: str, parsed) -> int:
    if kind == "movies":
        return _item(conn, library_id, None, "movie", parsed.title, year=parsed.year)
    if kind == "misc":
        return _item(conn, library_id, None, "file", parsed.title)
    show = _item(conn, library_id, None, "show", parsed.show or parsed.title, year=parsed.year)
    season_number = parsed.season if parsed.season is not None else 0
    season = _item(conn, library_id, show, "season", f"Season {season_number}", season=season_number)
    episode_number = parsed.episode if parsed.episode is not None else 0
    return _item(
        conn, library_id, season, "episode", parsed.title,
        season=season_number, episode=episode_number,
    )


def _set_poster(conn, item_id: int, poster_rel: str | None) -> None:
    if not poster_rel:
        return
    conn.execute(
        "UPDATE media_items SET poster_rel = COALESCE(poster_rel, ?), updated_at = ? WHERE id = ?",
        (poster_rel, utcnow(), item_id),
    )


def _enrich(conn, client: httpx.Client | None, key: str, data_dir: Path, library_id: int) -> str:
    if not key or client is None:
        return ""
    rows = conn.execute(
        """
        SELECT id, kind, title, year, tmdb_id, overview, poster_file
        FROM media_items
        WHERE library_id = ? AND kind IN ('show', 'movie') AND (tmdb_id IS NULL OR overview = '')
        """,
        (library_id,),
    ).fetchall()
    failed = ""
    for row in rows:
        hit = lookup_tmdb(client, key, "tv" if row["kind"] == "show" else "movie", row["title"], row["year"])
        if hit is None:
            failed = f"TMDB lookup failed for {row['title']}"
            continue
        poster_file = row["poster_file"]
        if hit.get("poster_path") and not poster_file and not conn.execute("SELECT poster_rel FROM media_items WHERE id = ?", (row["id"],)).fetchone()["poster_rel"]:
            dest = data_dir / "media-posters" / f"{row['id']}.jpg"
            if download_poster(client, hit["poster_path"], dest):
                poster_file = f"media-posters/{row['id']}.jpg"
        conn.execute(
            """
            UPDATE media_items
            SET tmdb_id = ?, overview = CASE WHEN overview = '' THEN ? ELSE overview END,
                poster_file = COALESCE(poster_file, ?), updated_at = ?
            WHERE id = ?
            """,
            (hit.get("id"), hit.get("overview") or "", poster_file, utcnow(), row["id"]),
        )
    return failed


def scan_library(conn, library_id: int, root: Path, data_dir: Path, *, probe=ffprobe_file, thumbs: bool = True, client: httpx.Client | None = None) -> None:
    library = conn.execute("SELECT id, rel_path, kind FROM media_libraries WHERE id = ?", (library_id,)).fetchone()
    if library is None:
        raise LookupError(f"library {library_id} was not found")
    from app.media_paths import PathEscape, library_directory

    try:
        directory = library_directory(root, library["rel_path"])
    except PathEscape as exc:
        _scan_state(conn, library_id, "error", 0, 0, str(exc))
        conn.commit()
        return
    if not directory.is_dir():
        _scan_state(conn, library_id, "error", 0, 0, f"{directory} is not a directory")
        conn.commit()
        return
    videos = list_videos(directory)
    _scan_state(conn, library_id, "running", 0, len(videos), "")
    conn.commit()
    seen: set[str] = set()
    problems: list[str] = []
    for index, path in enumerate(videos, start=1):
        rel = path.resolve().relative_to(directory.resolve()).as_posix()
        seen.add(rel)
        stat = path.stat()
        current = conn.execute(
            "SELECT id, size_bytes, mtime_ns, item_id FROM media_files WHERE library_id = ? AND rel_path = ?",
            (library_id, rel),
        ).fetchone()
        if current and current["size_bytes"] == stat.st_size and current["mtime_ns"] == stat.st_mtime_ns:
            _scan_state(conn, library_id, "running", index, len(videos), "")
            conn.commit()
            continue
        try:
            probed = probe(path)
        except RuntimeError as exc:
            problems.append(str(exc))
            probed = ProbeResult(None, path.suffix.lower().lstrip("."), None, None, None, [], [])
        parsed = parse_video(rel, library["kind"])
        item_id = _place(conn, library_id, library["kind"], parsed)
        poster = local_poster(directory, path)
        _set_poster(conn, item_id, poster)
        if library["kind"] == "tv":
            show = conn.execute(
                """
                SELECT show.id FROM media_items episode
                JOIN media_items season ON season.id = episode.parent_id
                JOIN media_items show ON show.id = season.parent_id
                WHERE episode.id = ?
                """,
                (item_id,),
            ).fetchone()
            if show and poster:
                _set_poster(conn, int(show["id"]), poster)
        now = utcnow()
        if current:
            conn.execute(
                """
                UPDATE media_files
                SET item_id = ?, size_bytes = ?, mtime_ns = ?, duration = ?, container = ?, video_codec = ?,
                    width = ?, height = ?, audio_tracks = ?, subtitle_tracks = ?, scanned_at = ?
                WHERE id = ?
                """,
                (
                    item_id, stat.st_size, stat.st_mtime_ns, probed.duration, probed.container, probed.video_codec,
                    probed.width, probed.height, json.dumps(probed.audio), json.dumps(probed.subtitles), now, current["id"],
                ),
            )
            file_id = current["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO media_files (
                    library_id, item_id, rel_path, size_bytes, mtime_ns, duration, container, video_codec,
                    width, height, audio_tracks, subtitle_tracks, scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    library_id, item_id, rel, stat.st_size, stat.st_mtime_ns, probed.duration, probed.container,
                    probed.video_codec, probed.width, probed.height, json.dumps(probed.audio), json.dumps(probed.subtitles), now,
                ),
            )
            file_id = cursor.lastrowid
        _scan_state(conn, library_id, "running", index, len(videos), "")
        conn.commit()
    if seen:
        holders = ",".join("?" for _ in seen)
        conn.execute(
            f"DELETE FROM media_files WHERE library_id = ? AND rel_path NOT IN ({holders})",
            (library_id, *sorted(seen)),
        )
    else:
        conn.execute("DELETE FROM media_files WHERE library_id = ?", (library_id,))
    _prune(conn, library_id)
    key = ""
    row = conn.execute("SELECT value FROM server_settings WHERE key = 'tmdb_api_key'").fetchone()
    if row:
        key = row["value"].strip()
    tmdb_problem = _enrich(conn, client, key, data_dir, library_id)
    if tmdb_problem:
        problems.append(tmdb_problem)
    if thumbs:
        _fill_thumbs(conn, library_id, directory, data_dir)
    message = problems[0] if problems else ""
    _scan_state(conn, library_id, "idle", len(videos), len(videos), message)
    conn.commit()


def _fill_thumbs(conn, library_id: int, directory: Path, data_dir: Path) -> None:
    rows = conn.execute(
        """
        SELECT items.id AS item_id, files.rel_path, files.duration
        FROM media_items items
        JOIN media_files files ON files.item_id = items.id
        WHERE items.library_id = ? AND items.poster_rel IS NULL AND items.poster_file IS NULL
        GROUP BY items.id
        """,
        (library_id,),
    ).fetchall()
    for row in rows:
        source = directory / row["rel_path"]
        if not source.is_file():
            continue
        digest = hashlib.sha1(row["rel_path"].encode()).hexdigest()[:16]
        dest = data_dir / "media-thumbs" / f"{library_id}-{digest}.jpg"
        if not dest.is_file() and not grab_thumbnail(source, dest, row["duration"]):
            continue
        if dest.is_file():
            conn.execute(
                "UPDATE media_items SET poster_file = ?, updated_at = ? WHERE id = ? AND poster_rel IS NULL AND poster_file IS NULL",
                (dest.relative_to(data_dir).as_posix(), utcnow(), row["item_id"]),
            )


def _prune(conn, library_id: int) -> None:
    conn.execute(
        """
        DELETE FROM media_items
        WHERE library_id = ? AND kind IN ('episode', 'movie', 'file')
          AND id NOT IN (SELECT item_id FROM media_files WHERE item_id IS NOT NULL)
        """,
        (library_id,),
    )
    conn.execute(
        """
        DELETE FROM media_items
        WHERE library_id = ? AND kind = 'season'
          AND id NOT IN (SELECT parent_id FROM media_items WHERE parent_id IS NOT NULL)
        """,
        (library_id,),
    )
    conn.execute(
        """
        DELETE FROM media_items
        WHERE library_id = ? AND kind = 'show'
          AND id NOT IN (SELECT parent_id FROM media_items WHERE parent_id IS NOT NULL)
        """,
        (library_id,),
    )


def _scan_state(conn, library_id: int, state: str, done: int, total: int, message: str) -> None:
    conn.execute(
        """
        INSERT INTO media_scans (library_id, state, done, total, message, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(library_id) DO UPDATE SET
            state = excluded.state, done = excluded.done, total = excluded.total,
            message = excluded.message, updated_at = excluded.updated_at
        """,
        (library_id, state, done, total, message, utcnow()),
    )
