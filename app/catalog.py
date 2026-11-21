import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import stamp, utcnow
from app.detect import detect, guide_sort_key, merge_detected, normalize_country, normalize_language, suggest_adult
from app.epg import parse_xmltv
from app.hdhomerun import LineupChannel
from app.playlist import ParsedChannel


def next_guide_number(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT guide_number FROM channels
        WHERE guide_number GLOB '[0-9]*' AND instr(guide_number, '.') = 0
        ORDER BY CAST(guide_number AS INTEGER) DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return "1"
    try:
        return str(int(row["guide_number"]) + 1)
    except ValueError:
        return "1"


def ensure_tag(conn: sqlite3.Connection, name: str) -> int:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("tag name is empty")
    conn.execute("INSERT INTO tags(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (cleaned,))
    row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (cleaned,)).fetchone()
    return int(row["id"])


def _replace_group_tag(conn: sqlite3.Connection, channel_id: int, old_group: str | None, new_group: str | None) -> None:
    if old_group and old_group != new_group:
        conn.execute(
            """
            DELETE FROM channel_tags
            WHERE channel_id = ?
              AND tag_id = (SELECT id FROM tags WHERE name = ? COLLATE NOCASE)
            """,
            (channel_id, old_group),
        )
    if new_group:
        tag_id = ensure_tag(conn, new_group)
        conn.execute(
            "INSERT INTO channel_tags(channel_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (channel_id, tag_id),
        )


def upsert_parsed_channel(
    conn: sqlite3.Connection,
    channel: ParsedChannel,
    playlist_id: int | None,
    device_id: int | None,
    guide_number: str | None = None,
    extra_tags: tuple[str, ...] = (),
) -> str:
    existing = conn.execute("SELECT * FROM channels WHERE url = ?", (channel.url,)).fetchone()
    found = detect(
        channel.tvg_language,
        channel.tvg_country,
        channel.group_title,
        channel.title,
    )
    adult = 1 if suggest_adult(channel.title, channel.group_title) else 0
    now = utcnow()
    if existing is None:
        number = guide_number or next_guide_number(conn)
        language, language_source = found.language, ("detected" if found.language else None)
        country, country_source = found.country, ("detected" if found.country else None)
        # Keep the detection step so a later audio probe can outrank a title guess
        # without outranking tvg or group. The column stores detected/manual for the API;
        # the step is kept beside it.
        conn.execute(
            """
            INSERT INTO channels (
                url, title, tvg_id, tvg_name, logo_url, group_title, guide_number, guide_sort,
                language, language_source, country, country_source, adult, adult_source,
                playlist_id, device_id, created_at, updated_at,
                language_step, country_step, logo_source, guide_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'detected', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel.url,
                channel.title,
                channel.tvg_id,
                channel.tvg_name,
                channel.logo_url,
                channel.group_title,
                number,
                guide_sort_key(number),
                language,
                language_source,
                country,
                country_source,
                adult,
                playlist_id,
                device_id,
                now,
                now,
                found.language_step,
                found.country_step,
                "m3u" if channel.logo_url else None,
                "m3u" if channel.tvg_id else None,
            ),
        )
        channel_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        _replace_group_tag(conn, channel_id, None, channel.group_title)
        for tag in extra_tags:
            tag_id = ensure_tag(conn, tag)
            conn.execute(
                "INSERT INTO channel_tags(channel_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (channel_id, tag_id),
            )
        conn.execute(
            "INSERT INTO verify_queue(channel_id, enqueued_at) VALUES (?, ?) ON CONFLICT(channel_id) DO NOTHING",
            (channel_id, now),
        )
        return "imported"

    language, language_step = merge_detected(
        existing["language"],
        existing["language_step"] or ("manual" if existing["language_source"] == "manual" else None),
        found.language,
        found.language_step,
    )
    country, country_step = merge_detected(
        existing["country"],
        existing["country_step"] or ("manual" if existing["country_source"] == "manual" else None),
        found.country,
        found.country_step,
    )
    if existing["adult_source"] == "manual":
        adult_value = existing["adult"]
        adult_source = "manual"
    else:
        adult_value = adult
        adult_source = "detected"
    number = guide_number or existing["guide_number"]
    if existing["title_source"] == "manual":
        title = existing["title"]
    else:
        title = channel.title
    if existing["guide_source"] == "manual":
        tvg_id = existing["tvg_id"]
        guide_source = "manual"
    else:
        tvg_id = channel.tvg_id or existing["tvg_id"]
        guide_source = existing["guide_source"] or ("m3u" if channel.tvg_id else None)
    if existing["logo_source"] == "manual":
        logo_url = existing["logo_url"]
        logo_source = "manual"
    elif channel.logo_url:
        logo_url = channel.logo_url
        logo_source = "m3u"
    else:
        logo_url = existing["logo_url"]
        logo_source = existing["logo_source"]
    conn.execute(
        """
        UPDATE channels SET
            title = ?, title_source = ?, tvg_id = ?, tvg_name = ?, logo_url = ?, group_title = ?,
            guide_number = ?, guide_sort = ?,
            language = ?, language_source = ?, language_step = ?,
            country = ?, country_source = ?, country_step = ?,
            adult = ?, adult_source = ?,
            playlist_id = COALESCE(?, playlist_id),
            device_id = COALESCE(?, device_id),
            logo_source = ?, guide_source = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            title,
            existing["title_source"],
            tvg_id,
            channel.tvg_name,
            logo_url,
            channel.group_title,
            number,
            guide_sort_key(number),
            language,
            "manual" if language_step == "manual" else ("detected" if language else None),
            language_step,
            country,
            "manual" if country_step == "manual" else ("detected" if country else None),
            country_step,
            adult_value,
            adult_source,
            playlist_id,
            device_id,
            logo_source,
            guide_source,
            now,
            existing["id"],
        ),
    )
    _replace_group_tag(conn, int(existing["id"]), existing["group_title"], channel.group_title)
    for tag in extra_tags:
        tag_id = ensure_tag(conn, tag)
        conn.execute(
            "INSERT INTO channel_tags(channel_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (int(existing["id"]), tag_id),
        )
    return "updated"


def apply_audio_language(conn: sqlite3.Connection, channel_id: int, audio_language: str | None) -> None:
    row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if row is None or not audio_language:
        return
    found = detect(None, None, None, None, audio_language=audio_language)
    current_step = row["language_step"] or ("manual" if row["language_source"] == "manual" else None)
    language, step = merge_detected(row["language"], current_step, found.language, found.language_step)
    if language == row["language"] and step == current_step:
        return
    conn.execute(
        """
        UPDATE channels
        SET language = ?, language_source = ?, language_step = ?, audio_language = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            language,
            "manual" if step == "manual" else ("detected" if language else None),
            step,
            audio_language,
            utcnow(),
            channel_id,
        ),
    )


def import_lineup_channels(
    conn: sqlite3.Connection,
    device_row_id: int,
    channels: list[LineupChannel],
) -> dict[str, int]:
    seen: set[str] = set()
    imported = 0
    updated = 0
    for item in channels:
        parsed = ParsedChannel(
            url=item.url,
            title=item.guide_name,
            tvg_name=item.guide_name,
            group_title="OTA",
        )
        outcome = upsert_parsed_channel(
            conn,
            parsed,
            playlist_id=None,
            device_id=device_row_id,
            guide_number=item.guide_number,
            extra_tags=("OTA",),
        )
        seen.add(item.url)
        if outcome == "imported":
            imported += 1
        else:
            updated += 1
    removed = 0
    rows = conn.execute("SELECT id, url FROM channels WHERE device_id = ?", (device_row_id,)).fetchall()
    for row in rows:
        if row["url"] not in seen:
            conn.execute("DELETE FROM channels WHERE id = ?", (row["id"],))
            removed += 1
    return {"imported": imported, "updated": updated, "removed": removed}


def save_probe(conn: sqlite3.Connection, channel_id: int, result) -> None:
    conn.execute(
        """
        UPDATE channels SET
            status = ?, status_detail = ?, last_checked_at = ?,
            width = ?, height = ?, video_codec = ?, audio_codec = ?,
            audio_language = ?, bitrate = ?, video_profile = ?, has_b_frames = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            result.status,
            result.detail,
            utcnow(),
            result.width,
            result.height,
            result.video_codec,
            result.audio_codec,
            result.audio_language,
            result.bitrate,
            getattr(result, "video_profile", None),
            getattr(result, "has_b_frames", None),
            utcnow(),
            channel_id,
        ),
    )
    if result.status == "online":
        apply_audio_language(conn, channel_id, result.audio_language)


def enqueue(conn: sqlite3.Connection, channel_id: int) -> None:
    conn.execute(
        """
        INSERT INTO verify_queue(channel_id, enqueued_at) VALUES (?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET enqueued_at = excluded.enqueued_at
        """,
        (channel_id, utcnow()),
    )


def take_jobs(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT c.* FROM verify_queue q
        JOIN channels c ON c.id = q.channel_id
        ORDER BY q.enqueued_at
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if rows:
        conn.executemany("DELETE FROM verify_queue WHERE channel_id = ?", [(row["id"],) for row in rows])
    return rows


def enqueue_stale(conn: sqlite3.Connection, interval_minutes: int, now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    cutoff = stamp(moment - timedelta(minutes=int(interval_minutes)))
    before = conn.execute("SELECT COUNT(*) AS n FROM verify_queue").fetchone()["n"]
    conn.execute(
        """
        INSERT INTO verify_queue(channel_id, enqueued_at)
        SELECT id, ?
        FROM channels
        WHERE last_checked_at IS NULL
           OR last_checked_at <= ?
        ON CONFLICT(channel_id) DO NOTHING
        """,
        (utcnow(), cutoff),
    )
    after = conn.execute("SELECT COUNT(*) AS n FROM verify_queue").fetchone()["n"]
    return int(after - before)


def channel_tags(conn: sqlite3.Connection, channel_ids: list[int]) -> dict[int, list[dict]]:
    if not channel_ids:
        return {}
    placeholders = ",".join("?" for _ in channel_ids)
    rows = conn.execute(
        f"""
        SELECT ct.channel_id, t.id, t.name
        FROM channel_tags ct
        JOIN tags t ON t.id = ct.tag_id
        WHERE ct.channel_id IN ({placeholders})
        ORDER BY t.name COLLATE NOCASE
        """,
        channel_ids,
    ).fetchall()
    grouped: dict[int, list[dict]] = {channel_id: [] for channel_id in channel_ids}
    for row in rows:
        grouped[int(row["channel_id"])].append({"id": row["id"], "name": row["name"]})
    return grouped


def channel_json(row: sqlite3.Row, tags: list[dict]) -> dict[str, Any]:
    source_type = "hdhomerun" if row["device_id"] else "playlist" if row["playlist_id"] else None
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "title_source": row["title_source"],
        "tvg_id": row["tvg_id"],
        "tvg_name": row["tvg_name"],
        "logo_url": row["logo_url"],
        "group_title": row["group_title"],
        "guide_number": row["guide_number"],
        "language": row["language"],
        "language_source": row["language_source"],
        "country": row["country"],
        "country_source": row["country_source"],
        "adult": bool(row["adult"]),
        "adult_source": row["adult_source"],
        "status": row["status"],
        "status_detail": row["status_detail"],
        "last_checked_at": row["last_checked_at"],
        "width": row["width"],
        "height": row["height"],
        "video_codec": row["video_codec"],
        "video_profile": row["video_profile"],
        "has_b_frames": row["has_b_frames"],
        "playback_mode": row["playback_mode"] or "auto",
        "hidden": bool(row["hidden"]),
        "notes": row["notes"],
        "org_logo_url": row["org_logo_url"],
        "audio_codec": row["audio_codec"],
        "audio_language": row["audio_language"],
        "bitrate": row["bitrate"],
        "tags": tags,
        "logo_source": row["logo_source"],
        "guide_source": row["guide_source"],
        "guide_match_id": row["guide_match_id"],
        "programme_source": row["programme_source"],
        "source": {"type": source_type, "id": row["device_id"] or row["playlist_id"]},
    }


def search_channels(
    conn: sqlite3.Connection,
    q: str | None,
    tags: list[str],
    status: str | None,
    languages: list[str],
    countries: list[str],
    include_adult: bool,
    sort: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    where, params = _filters(q, tags, status, languages, countries, include_adult)
    sort_sql = {
        "title": "c.title COLLATE NOCASE",
        "status": "c.status",
        "language": "c.language COLLATE NOCASE",
        "country": "c.country COLLATE NOCASE",
        "number": "c.guide_sort",
    }[sort]
    direction = "DESC" if order == "desc" else "ASC"
    total = conn.execute(f"SELECT COUNT(*) AS n FROM channels c {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT c.* FROM channels c
        {where}
        ORDER BY {sort_sql} {direction}, c.id ASC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    tag_map = channel_tags(conn, [row["id"] for row in rows])
    return {
        "total": total,
        "channels": [channel_json(row, tag_map.get(row["id"], [])) for row in rows],
    }


def _filters(
    q: str | None,
    tags: list[str],
    status: str | None,
    languages: list[str],
    countries: list[str],
    include_adult: bool,
) -> tuple[str, list]:
    clauses = ["1 = 1"]
    params: list = []
    if not include_adult:
        clauses.append("c.adult = 0")
    if status:
        clauses.append("c.status = ?")
        params.append(status)
    if languages:
        clauses.append("c.language IN (%s)" % ",".join("?" for _ in languages))
        params.extend(languages)
    if countries:
        clauses.append("c.country IN (%s)" % ",".join("?" for _ in countries))
        params.extend(countries)
    if tags:
        clauses.append(
            "("
            + " OR ".join(
                """EXISTS (
                    SELECT 1 FROM channel_tags ct
                    JOIN tags t ON t.id = ct.tag_id
                    WHERE ct.channel_id = c.id AND t.name = ? COLLATE NOCASE
                )"""
                for _ in tags
            )
            + ")"
        )
        params.extend(tags)
    fts = _fts_query(q or "")
    join = ""
    if fts:
        join = "JOIN channels_fts fts ON fts.rowid = c.id"
        clauses.append("channels_fts MATCH ?")
        params.append(fts)
    return f"{join} WHERE " + " AND ".join(clauses), params


def _fts_query(q: str) -> str:
    tokens = []
    current = []
    for char in q:
        if char.isalnum() or char == "_":
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    if not tokens:
        return ""
    return " AND ".join(f'"{token}"' for token in tokens)


def set_manual_fields(
    conn: sqlite3.Connection,
    channel_id: int,
    fields: dict,
) -> None:
    assignments = []
    params: list = []
    if "language" in fields:
        language = normalize_language(fields["language"]) if fields["language"] else None
        if fields["language"] and language is None:
            raise ValueError("unknown language")
        assignments += ["language = ?", "language_source = 'manual'", "language_step = 'manual'"]
        params.append(language)
    if "country" in fields:
        country = normalize_country(fields["country"]) if fields["country"] else None
        if fields["country"] and country is None:
            raise ValueError("unknown country")
        assignments += ["country = ?", "country_source = 'manual'", "country_step = 'manual'"]
        params.append(country)
    if "adult" in fields:
        assignments += ["adult = ?", "adult_source = 'manual'"]
        params.append(1 if fields["adult"] else 0)
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title:
            raise ValueError("channel name is empty")
        assignments += ["title = ?", "title_source = 'manual'"]
        params.append(title)
    if "logo_url" in fields:
        from app.logos import acceptable_logo

        raw_logo = (fields["logo_url"] or "").strip() or None
        if raw_logo and acceptable_logo(raw_logo) is None:
            raise ValueError("logo URL is not a usable http(s) address")
        assignments += ["logo_url = ?", "logo_source = 'manual'"]
        params.append(raw_logo)
    if "playback_mode" in fields:
        mode = (fields["playback_mode"] or "auto").lower()
        if mode not in {"auto", "webrtc", "hls", "transcode"}:
            raise ValueError("unknown playback mode")
        assignments += ["playback_mode = ?"]
        params.append(mode)
    if "hidden" in fields:
        assignments += ["hidden = ?"]
        params.append(1 if fields["hidden"] else 0)
    if "notes" in fields:
        assignments += ["notes = ?"]
        params.append((fields["notes"] or "").strip() or None)
    if "tvg_id" in fields:
        tvg_id = (fields["tvg_id"] or "").strip() or None
        assignments += ["tvg_id = ?", "guide_source = 'manual'", "guide_match_id = ?"]
        params.extend([tvg_id, tvg_id])
    if "guide_number" in fields:
        number = (fields["guide_number"] or "").strip() or None
        assignments += ["guide_number = ?", "guide_sort = ?"]
        params.extend([number, guide_sort_key(number)])
    if not assignments:
        return
    assignments.append("updated_at = ?")
    params.append(utcnow())
    params.append(channel_id)
    cur = conn.execute(f"UPDATE channels SET {', '.join(assignments)} WHERE id = ?", params)
    if cur.rowcount == 0:
        raise KeyError(channel_id)


def programs_for(conn: sqlite3.Connection, start: str, end: str, include_adult: bool) -> list[dict]:
    adult_clause = "" if include_adult else "AND c.adult = 0"
    rows = conn.execute(
        f"""
        SELECT p.* FROM programs p
        JOIN channels c ON c.id = p.channel_id
        WHERE p.stop_at > ? AND p.start_at < ?
        {adult_clause}
        ORDER BY p.start_at
        """,
        (start, end),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_epg(conn: sqlite3.Connection, xml_text: str) -> dict[str, int]:
    names, programmes = parse_xmltv(xml_text)
    channels = conn.execute("SELECT id, tvg_id, title, adult FROM channels").fetchall()
    by_tvg = {row["tvg_id"]: row["id"] for row in channels if row["tvg_id"]}
    by_name = {row["title"].casefold(): row["id"] for row in channels}
    matched_ids: set[int] = set()
    stored = 0
    skipped = 0
    pending: list[tuple] = []
    for programme in programmes:
        channel_id = by_tvg.get(programme["xmltv_id"])
        if channel_id is None:
            for name in names.get(programme["xmltv_id"], []):
                channel_id = by_name.get(name.casefold())
                if channel_id is not None:
                    break
        if channel_id is None:
            skipped += 1
            continue
        matched_ids.add(channel_id)
        pending.append(
            (
                channel_id,
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
    if matched_ids:
        conn.executemany("DELETE FROM programs WHERE channel_id = ?", [(channel_id,) for channel_id in matched_ids])
    conn.executemany(
        """
        INSERT INTO programs (
            channel_id, start_at, stop_at, title, episode_title, description, season, episode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        pending,
    )
    return {"stored": stored, "skipped": skipped, "channels": len(matched_ids)}
