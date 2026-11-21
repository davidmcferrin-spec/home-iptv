import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utcnow() -> str:
    return stamp(datetime.now(timezone.utc))


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        options = [row[0] for row in conn.execute("PRAGMA compile_options")]
        if "ENABLE_FTS5" not in options:
            raise RuntimeError("this Python sqlite3 build does not include FTS5")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                source_url TEXT,
                file_path TEXT,
                interval_minutes INTEGER,
                last_imported_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hdhomerun_devices (
                id INTEGER PRIMARY KEY,
                host TEXT NOT NULL UNIQUE,
                device_id TEXT,
                friendly_name TEXT,
                model TEXT,
                tuner_count INTEGER NOT NULL DEFAULT 1,
                base_url TEXT,
                lineup_url TEXT,
                manual INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT
            );

            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                tvg_id TEXT,
                tvg_name TEXT,
                logo_url TEXT,
                group_title TEXT,
                guide_number TEXT,
                guide_sort INTEGER NOT NULL DEFAULT 1000000000,
                language TEXT,
                language_source TEXT,
                language_step TEXT,
                country TEXT,
                country_source TEXT,
                country_step TEXT,
                adult INTEGER NOT NULL DEFAULT 0,
                adult_source TEXT,
                status TEXT NOT NULL DEFAULT 'unknown',
                status_detail TEXT,
                last_checked_at TEXT,
                width INTEGER,
                height INTEGER,
                video_codec TEXT,
                audio_codec TEXT,
                audio_language TEXT,
                bitrate INTEGER,
                playlist_id INTEGER REFERENCES playlists(id) ON DELETE SET NULL,
                device_id INTEGER REFERENCES hdhomerun_devices(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS channels_status ON channels(status);
            CREATE INDEX IF NOT EXISTS channels_language ON channels(language);
            CREATE INDEX IF NOT EXISTS channels_country ON channels(country);
            CREATE INDEX IF NOT EXISTS channels_adult ON channels(adult);
            CREATE INDEX IF NOT EXISTS channels_playlist ON channels(playlist_id);
            CREATE INDEX IF NOT EXISTS channels_device ON channels(device_id);
            CREATE INDEX IF NOT EXISTS channels_guide ON channels(guide_sort);

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE
            );

            CREATE TABLE IF NOT EXISTS channel_tags (
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (channel_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                start_at TEXT NOT NULL,
                stop_at TEXT NOT NULL,
                title TEXT NOT NULL,
                episode_title TEXT,
                description TEXT,
                season INTEGER,
                episode INTEGER
            );

            CREATE INDEX IF NOT EXISTS programs_window ON programs(channel_id, start_at, stop_at);

            CREATE TABLE IF NOT EXISTS verify_queue (
                channel_id INTEGER PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
                enqueued_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS server_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS channels_fts
            USING fts5(title, content='channels', content_rowid='id', tokenize='unicode61')
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS channels_fts_insert AFTER INSERT ON channels BEGIN
                INSERT INTO channels_fts(rowid, title) VALUES (new.id, new.title);
            END;

            CREATE TRIGGER IF NOT EXISTS channels_fts_delete AFTER DELETE ON channels BEGIN
                INSERT INTO channels_fts(channels_fts, rowid, title)
                VALUES ('delete', old.id, old.title);
            END;

            CREATE TRIGGER IF NOT EXISTS channels_fts_update AFTER UPDATE OF title ON channels BEGIN
                INSERT INTO channels_fts(channels_fts, rowid, title)
                VALUES ('delete', old.id, old.title);
                INSERT INTO channels_fts(rowid, title) VALUES (new.id, new.title);
            END;
            """
        )
        _seed_settings(conn)
        _ensure_column(conn, "playlists", "epg_url", "TEXT")
        _ensure_column(conn, "channels", "logo_source", "TEXT")
        _ensure_column(conn, "channels", "guide_source", "TEXT")
        _ensure_column(conn, "channels", "guide_match_id", "TEXT")
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in present:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _seed_settings(conn: sqlite3.Connection) -> None:
    defaults = {
        "hdhr_emulation": "0",
        "emulated_tuner_count": "4",
        "emulated_friendly_name": "Home IPTV",
        "idle_timeout_seconds": "",
        "verify_interval_minutes": "",
        "verify_concurrency": "",
        "verify_timeout_seconds": "",
        "public_http_base": "",
        "public_webrtc_base": "",
        "public_hls_base": "",
        "webrtc_additional_host": "",
        "guide_refreshed_at": "",
        "device_id": "",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT INTO server_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value),
        )


def setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO server_settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
