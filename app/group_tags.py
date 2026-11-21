import json
import re
import sqlite3

from app.detect import suggest_adult
from app.logos import strip_emoji

_SPACE = re.compile(r"\s+")


def group_tag_names(group: str | None) -> list[str]:
    if not group:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in group.split(";"):
        cleaned = _SPACE.sub(" ", strip_emoji(part)).strip(" -")
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    return names


def tag_is_adult(name: str) -> bool:
    return suggest_adult(None, name)


def migrate_group_tags(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, name FROM tags").fetchall()
    for row in rows:
        parts = group_tag_names(row["name"])
        if parts == [row["name"]]:
            continue
        channel_ids = [
            item["channel_id"]
            for item in conn.execute(
                "SELECT channel_id FROM channel_tags WHERE tag_id = ?",
                (row["id"],),
            ).fetchall()
        ]
        conn.execute("DELETE FROM channel_tags WHERE tag_id = ?", (row["id"],))
        conn.execute("DELETE FROM tags WHERE id = ?", (row["id"],))
        for channel_id in channel_ids:
            for name in parts:
                tag_id = _ensure(conn, name)
                conn.execute(
                    "INSERT INTO channel_tags(channel_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (channel_id, tag_id),
                )


def refresh_adult_flags(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, title, group_title FROM channels WHERE adult = 0 AND COALESCE(adult_source, '') != 'manual'"
    ).fetchall()
    for row in rows:
        if suggest_adult(row["title"], row["group_title"]):
            conn.execute(
                "UPDATE channels SET adult = 1, adult_source = 'detected' WHERE id = ?",
                (row["id"],),
            )


def _ensure(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT INTO tags(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
    found = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    return int(found["id"])


def load_filter_prefs(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM server_settings WHERE key = 'guide_filters'").fetchone()
    if row is None or not row["value"]:
        return {"hidden": [], "pinned": [], "labels": {}}
    try:
        payload = json.loads(row["value"])
    except json.JSONDecodeError:
        return {"hidden": [], "pinned": [], "labels": {}}
    return {
        "hidden": [str(item) for item in payload.get("hidden") or []],
        "pinned": [str(item) for item in payload.get("pinned") or []],
        "labels": {str(key): str(value) for key, value in (payload.get("labels") or {}).items()},
    }


def save_filter_prefs(conn: sqlite3.Connection, prefs: dict) -> dict:
    cleaned = {
        "hidden": _names(prefs.get("hidden")),
        "pinned": _names(prefs.get("pinned")),
        "labels": {},
    }
    labels = prefs.get("labels") or {}
    if isinstance(labels, dict):
        for key, value in labels.items():
            name = _SPACE.sub(" ", strip_emoji(str(key))).strip()
            label = _SPACE.sub(" ", strip_emoji(str(value))).strip()
            if name and label:
                cleaned["labels"][name.casefold()] = label[:80]
    from app.db import set_setting

    set_setting(conn, "guide_filters", json.dumps(cleaned, ensure_ascii=False))
    return cleaned


def _names(values) -> list[str]:
    names = []
    seen = set()
    for value in values or []:
        name = _SPACE.sub(" ", strip_emoji(str(value))).strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def merge_tags(conn: sqlite3.Connection, source: str, target: str) -> None:
    source_name = _SPACE.sub(" ", strip_emoji(source)).strip()
    target_name = _SPACE.sub(" ", strip_emoji(target)).strip()
    if not source_name or not target_name:
        raise ValueError("tag name is empty")
    if source_name.casefold() == target_name.casefold():
        raise ValueError("choose a different tag to merge into")
    source_row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (source_name,)).fetchone()
    if source_row is None:
        raise KeyError(source_name)
    target_id = _ensure(conn, target_name)
    conn.execute(
        """
        INSERT INTO channel_tags(channel_id, tag_id)
        SELECT channel_id, ? FROM channel_tags WHERE tag_id = ?
        ON CONFLICT DO NOTHING
        """,
        (target_id, source_row["id"]),
    )
    conn.execute("DELETE FROM channel_tags WHERE tag_id = ?", (source_row["id"],))
    conn.execute("DELETE FROM tags WHERE id = ?", (source_row["id"],))
    prefs = load_filter_prefs(conn)
    prefs["hidden"] = [name for name in prefs["hidden"] if name.casefold() != source_name.casefold()]
    prefs["pinned"] = [
        target_name if name.casefold() == source_name.casefold() else name for name in prefs["pinned"]
    ]
    prefs["labels"].pop(source_name.casefold(), None)
    save_filter_prefs(conn, prefs)


def filter_catalog(conn: sqlite3.Connection, include_adult: bool) -> dict:
    prefs = load_filter_prefs(conn)
    rows = conn.execute(
        """
        SELECT t.id, t.name, COUNT(c.id) AS n,
               SUM(CASE WHEN c.adult = 0 THEN 1 ELSE 0 END) AS open_count
        FROM tags t
        LEFT JOIN channel_tags ct ON ct.tag_id = t.id
        LEFT JOIN channels c ON c.id = ct.channel_id
        GROUP BY t.id
        ORDER BY n DESC, t.name COLLATE NOCASE
        """
    ).fetchall()
    hidden = {name.casefold() for name in prefs["hidden"]}
    pinned = [name.casefold() for name in prefs["pinned"]]
    tags = []
    for row in rows:
        adult = tag_is_adult(row["name"]) or (row["n"] and not row["open_count"])
        if adult and not include_adult:
            continue
        label = prefs["labels"].get(row["name"].casefold()) or strip_emoji(row["name"])
        tags.append(
            {
                "id": row["id"],
                "name": row["name"],
                "label": label,
                "count": row["n"],
                "hidden": row["name"].casefold() in hidden,
                "pinned": row["name"].casefold() in pinned,
                "adult": bool(adult),
            }
        )
    return {**prefs, "tags": tags}
