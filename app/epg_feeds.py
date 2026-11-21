import hashlib
import logging
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.db import connect, utcnow
from app.epg import EpgParseError, iter_xmltv

log = logging.getLogger(__name__)

MAX_BYTES = 80 * 1024 * 1024
# These URLs answered with a gzip XMLTV file on 2026-11-21. No account is required.
BUILTIN = (
    ("AU", "https://epgshare01.online/epgshare01/epg_ripper_AU1.xml.gz"),
    ("CA", "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz"),
    ("DE", "https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz"),
    ("ES", "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz"),
    ("FR", "https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz"),
    ("GB", "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"),
    ("IE", "https://epgshare01.online/epgshare01/epg_ripper_IE1.xml.gz"),
    ("IT", "https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz"),
    ("NL", "https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz"),
    ("US", "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"),
    ("AU", "https://epg.pw/xmltv/epg_AU.xml.gz"),
    ("CA", "https://epg.pw/xmltv/epg_CA.xml.gz"),
    ("DE", "https://epg.pw/xmltv/epg_DE.xml.gz"),
    ("FR", "https://epg.pw/xmltv/epg_FR.xml.gz"),
    ("GB", "https://epg.pw/xmltv/epg_GB.xml.gz"),
    ("US", "https://epg.pw/xmltv/epg_US.xml.gz"),
)


class FeedError(Exception):
    pass


def ensure_builtin(conn) -> None:
    for country, url in BUILTIN:
        conn.execute(
            """
            INSERT INTO epg_feeds(url, country, enabled, builtin)
            VALUES (?, ?, 1, 1)
            ON CONFLICT(url) DO NOTHING
            """,
            (url, country),
        )


def catalog_countries(conn) -> set[str]:
    found = set()
    rows = conn.execute(
        "SELECT DISTINCT country FROM channels WHERE country IS NOT NULL AND country != ''"
    ).fetchall()
    for row in rows:
        code = row["country"].strip().upper()
        if code == "UK":
            code = "GB"
        if code:
            found.add(code)
    return found


def feed_is_active(row, countries: set[str]) -> bool:
    if not row["enabled"]:
        return False
    if not row["country"]:
        return True
    return row["country"] in countries


def refresh_programme_feeds(database_path: str, fetch=None) -> dict:
    from app.guide_data import cache_dir

    cache = cache_dir(database_path)
    conn = connect(database_path)
    summary = {"feeds": 0, "failed": 0, "matched": 0, "programmes": 0}
    try:
        ensure_builtin(conn)
        countries = catalog_countries(conn)
        rows = conn.execute("SELECT * FROM epg_feeds ORDER BY country, url").fetchall()
        conn.commit()
        for row in rows:
            if not feed_is_active(row, countries):
                continue
            summary["feeds"] += 1
            try:
                stored, matched = _apply_feed(conn, cache, row, fetch)
            except (FeedError, EpgParseError, OSError) as exc:
                summary["failed"] += 1
                log.warning("guide feed skipped %s: %s", row["url"], exc)
                conn.execute(
                    """
                    UPDATE epg_feeds
                    SET last_fetched_at = ?, last_status = ?, last_matched = 0
                    WHERE id = ?
                    """,
                    (utcnow(), str(exc), row["id"]),
                )
                conn.commit()
                continue
            summary["matched"] += matched
            summary["programmes"] += stored
            host = urlsplit(row["url"]).hostname or row["url"]
            conn.execute(
                """
                UPDATE epg_feeds
                SET last_fetched_at = ?, last_status = 'ok', last_matched = ?
                WHERE id = ?
                """,
                (utcnow(), matched, row["id"]),
            )
            conn.commit()
            log.info("guide feed %s matched %s channels, %s programmes", host, matched, stored)
    finally:
        conn.close()
    return summary


def _apply_feed(conn, cache: Path, row, fetch) -> tuple[int, int]:
    target = cache / f"feed-{hashlib.sha256(row['url'].encode()).hexdigest()[:16]}.xml"
    from app.guide_data import guide_name

    _download(row["url"], target, fetch)
    by_id, by_name = _channel_indexes(conn)
    xml_channel: dict[str, int] = {}
    cleared: set[int] = set()
    pending: list[tuple] = []
    stored = 0
    for kind, *payload in iter_xmltv(target):
        if kind == "channel":
            channel_id, display = payload
            db_id = by_id.get(channel_id)
            if db_id is None:
                for label in display:
                    db_id = by_name.get(guide_name(label))
                    if db_id is not None:
                        break
            if db_id is not None:
                xml_channel[channel_id] = db_id
            continue
        programme = payload[0]
        db_id = xml_channel.get(programme["xmltv_id"])
        if db_id is None:
            continue
        if db_id not in cleared:
            conn.execute("DELETE FROM programs WHERE channel_id = ?", (db_id,))
            cleared.add(db_id)
        pending.append(
            (
                db_id,
                programme["start_at"],
                programme["stop_at"],
                programme["title"],
                programme["episode_title"],
                programme["description"],
                programme["season"],
                programme["episode"],
            )
        )
        stored += 1
        if len(pending) >= 400:
            _insert(conn, pending)
            pending.clear()
    if pending:
        _insert(conn, pending)
    host = urlsplit(row["url"]).hostname or row["url"]
    for db_id in cleared:
        current = conn.execute("SELECT guide_source FROM channels WHERE id = ?", (db_id,)).fetchone()
        if current["guide_source"] == "manual":
            conn.execute(
                "UPDATE channels SET programme_source = ?, updated_at = ? WHERE id = ?",
                (host, utcnow(), db_id),
            )
        else:
            conn.execute(
                """
                UPDATE channels
                SET programme_source = ?,
                    guide_source = COALESCE(guide_source, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (host, host, utcnow(), db_id),
            )
    return stored, len(cleared)


def _insert(conn, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO programs (
            channel_id, start_at, stop_at, title, episode_title, description, season, episode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _channel_indexes(conn):
    from app.guide_data import guide_name

    by_id = {}
    by_name = {}
    for row in conn.execute("SELECT id, tvg_id, guide_match_id, title, tvg_name FROM channels"):
        for key in (row["tvg_id"], row["guide_match_id"]):
            if key and key not in by_id:
                by_id[key] = row["id"]
        for label in (row["tvg_name"], row["title"]):
            name = guide_name(label)
            if name and name not in by_name:
                by_name[name] = row["id"]
    return by_id, by_name


def _download(url: str, dest: Path, fetch) -> None:
    if fetch is not None:
        try:
            payload = fetch(url)
        except Exception as exc:
            raise FeedError(f"guide download failed for {url}: {exc}") from exc
        if not payload:
            raise FeedError(f"guide URL returned nothing for {url}")
        data = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise FeedError(f"guide download is too large: {url}")
        dest.write_bytes(data)
        return
    temporary = dest.with_suffix(".part")
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=120,
            follow_redirects=True,
            headers={"User-Agent": "HomeIPTV/0.1"},
        ) as response:
            if response.status_code != 200:
                raise FeedError(f"guide URL returned HTTP {response.status_code} for {url}")
            size = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FeedError(f"guide download is too large: {url}")
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        raise FeedError(f"guide download failed for {url}: {exc}") from exc
    except FeedError:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(dest)
