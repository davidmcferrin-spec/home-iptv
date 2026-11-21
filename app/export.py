from xml.sax.saxutils import escape

from app.db import setting


def playlist_group(conn) -> str:
    value = (setting(conn, "playlist_group") or "tag").strip().lower()
    if value not in {"tag", "language", "country", "source"}:
        return "tag"
    return value


def selected_channels(conn, tag, language, country, online_only: bool, include_adult: bool):
    clauses = []
    params: list = []
    if not include_adult:
        clauses.append("c.adult = 0")
    if online_only:
        clauses.append("c.status = 'online'")
    if language:
        clauses.append("c.language = ?")
        params.append(language.lower())
    if country:
        clauses.append("c.country = ?")
        params.append(country.upper())
    if tag:
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM channel_tags ct
                JOIN tags t ON t.id = ct.tag_id
                WHERE ct.channel_id = c.id AND t.name = ? COLLATE NOCASE
            )
            """
        )
        params.append(tag)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"SELECT c.* FROM channels c {where} ORDER BY c.guide_sort, c.id",
        params,
    ).fetchall()


def folder_name(conn, row, mode: str) -> str:
    if mode == "language":
        return row["language"] or "Unknown"
    if mode == "country":
        return row["country"] or "Unknown"
    if mode == "source":
        if row["playlist_id"]:
            playlist = conn.execute("SELECT name FROM playlists WHERE id = ?", (row["playlist_id"],)).fetchone()
            return playlist["name"] if playlist else "Playlist"
        if row["device_id"]:
            return "HDHomeRun"
        return "Channels"
    tag = conn.execute(
        """
        SELECT t.name FROM channel_tags ct
        JOIN tags t ON t.id = ct.tag_id
        WHERE ct.channel_id = ?
        ORDER BY t.name COLLATE NOCASE
        LIMIT 1
        """,
        (row["id"],),
    ).fetchone()
    if tag:
        return tag["name"]
    return row["group_title"] or "Channels"


def _attr(value: str | None) -> str:
    return escape(value or "", {'"': "&quot;"})


def render_m3u(conn, rows, base: str, guide_url: str, mode: str) -> str:
    lines = [f'#EXTM3U url-tvg="{_attr(guide_url)}"']
    for row in rows:
        tvg_id = row["tvg_id"] or f"channel-{row['id']}"
        name = row["tvg_name"] or row["title"]
        lines.append(
            "#EXTINF:-1 "
            f'tvg-id="{_attr(tvg_id)}" '
            f'tvg-name="{_attr(name)}" '
            f'tvg-logo="{_attr(base + "/api/logo/" + str(row["id"]))}" '
            f'group-title="{_attr(folder_name(conn, row, mode))}",'
            f"{row['title']}"
        )
        lines.append(f"{base}/play/{row['id']}")
    return "\n".join(lines) + "\n"


def render_xmltv(conn, rows) -> str:
    ids = [row["id"] for row in rows]
    programmes = []
    if ids:
        marks = ",".join("?" for _ in ids)
        programmes = conn.execute(
            f"SELECT * FROM programs WHERE channel_id IN ({marks}) ORDER BY start_at",
            ids,
        ).fetchall()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<tv>"]
    for row in rows:
        tvg_id = row["tvg_id"] or f"channel-{row['id']}"
        parts.append(f'<channel id="{_attr(tvg_id)}"><display-name>{escape(row["title"])}</display-name></channel>')
    by_id = {row["id"]: row["tvg_id"] or f"channel-{row['id']}" for row in rows}
    for programme in programmes:
        start = _xmltv_time(programme["start_at"])
        stop = _xmltv_time(programme["stop_at"])
        channel = _attr(by_id.get(programme["channel_id"]))
        parts.append(f'<programme start="{start}" stop="{stop}" channel="{channel}">')
        parts.append(f"<title>{escape(programme['title'])}</title>")
        if programme["description"]:
            parts.append(f"<desc>{escape(programme['description'])}</desc>")
        parts.append("</programme>")
    parts.append("</tv>")
    return "\n".join(parts) + "\n"


def _xmltv_time(value: str) -> str:
    text = value.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
    return f"{text[:14]} +0000"
