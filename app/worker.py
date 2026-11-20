import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from app.catalog import enqueue_stale, save_probe, take_jobs
from app.config import Settings, settings
from app.db import connect, init_db, parse_stamp, setting
from app.importer import PlaylistFetchError, import_playlist_text, load_playlist_source
from app.playlist import PlaylistParseError
from app.verify import probe_url

log = logging.getLogger(__name__)


def _interval(conn, cfg: Settings) -> int:
    raw = setting(conn, "verify_interval_minutes")
    if raw:
        try:
            return max(5, int(raw))
        except ValueError:
            log.warning("verify_interval_minutes is not an integer: %s", raw)
    return cfg.verify_interval_minutes


def process_queue(database_path: str, concurrency: int, timeout_seconds: int) -> int:
    conn = connect(database_path)
    try:
        jobs = take_jobs(conn, concurrency)
        conn.commit()
    finally:
        conn.close()
    if not jobs:
        return 0
    from concurrent.futures import ThreadPoolExecutor

    def _probe(row):
        return row["id"], probe_url(row["url"], timeout_seconds)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(_probe, jobs))
    conn = connect(database_path)
    try:
        for channel_id, result in results:
            save_probe(conn, channel_id, result)
            log.info(
                "verified channel %s status %s video %s audio %s",
                channel_id,
                result.status,
                result.video_codec,
                result.audio_codec,
            )
        conn.commit()
    finally:
        conn.close()
    return len(results)


def reimport_due(database_path: str) -> None:
    conn = connect(database_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM playlists
            WHERE interval_minutes IS NOT NULL AND interval_minutes > 0
            """
        ).fetchall()
    finally:
        conn.close()
    now = datetime.now(timezone.utc)
    for row in rows:
        if row["last_imported_at"]:
            try:
                last = parse_stamp(row["last_imported_at"])
            except ValueError:
                last = None
            if last is not None and last + timedelta(minutes=row["interval_minutes"]) > now:
                continue
        try:
            text = load_playlist_source(database_path, row["id"])
            import_playlist_text(database_path, row["id"], text)
            log.info("reimported playlist %s", row["id"])
        except (PlaylistFetchError, PlaylistParseError, OSError) as exc:
            log.warning("playlist %s reimport failed: %s", row["id"], exc)


def maintain(database_path: str, cfg: Settings) -> None:
    conn = connect(database_path)
    try:
        added = enqueue_stale(conn, _interval(conn, cfg))
        conn.commit()
    finally:
        conn.close()
    if added:
        log.info("queued %s channel checks", added)
    reimport_due(database_path)


def worker_loop(stop: threading.Event, cfg: Settings | None = None) -> None:
    cfg = cfg or settings()
    init_db(cfg.database_path)
    while not stop.is_set():
        try:
            maintain(cfg.database_path, cfg)
            process_queue(cfg.database_path, cfg.verify_concurrency, cfg.verify_timeout_seconds)
        except sqlite3.Error as exc:
            log.warning("worker database error: %s", exc)
        stop.wait(2)


def main() -> None:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker_loop(threading.Event())


if __name__ == "__main__":
    main()
