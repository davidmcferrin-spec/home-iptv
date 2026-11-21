import gzip
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.catalog import replace_epg
from app.db import connect, setting, set_setting, utcnow
from app.epg import EpgParseError

log = logging.getLogger(__name__)

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
LOGOS_URL = "https://iptv-org.github.io/api/logos.json"
GUIDES_URL = "https://iptv-org.github.io/api/guides.json"
MAX_BYTES = 40 * 1024 * 1024
REFRESH_SECONDS = 12 * 60 * 60
NAME_RE = re.compile(r"[^a-z0-9]+")
COUNTRY_ALIAS = {"GB": "UK", "UK": "GB"}


def normalize_name(value: str | None) -> str:
    return NAME_RE.sub("", (value or "").casefold())


def guide_name(value: str | None) -> str:
    text = normalize_name(value)
    for suffix in ("fhd", "uhd", "hd", "sd", "4k"):
        if text.endswith(suffix) and len(text) - len(suffix) >= 3:
            return text[: -len(suffix)]
    return text


def cache_dir(database_path: str) -> Path:
    path = Path(database_path).parent / "guide-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def refresh_guide(database_path: str, fetch=None) -> dict:
    getter = fetch or _http_get
    cache = cache_dir(database_path)
    channels = _cached_json(cache, "channels.json", CHANNELS_URL, getter)
    logos = _cached_json(cache, "logos.json", LOGOS_URL, getter)
    guides = _cached_json(cache, "guides.json", GUIDES_URL, getter)
    summary = {"matched": 0, "missed": 0, "logos": 0, "programmes": 0}
    if channels is None:
        log.warning("guide data was not downloaded")
        return summary
    by_id, by_name = _indexes(channels)
    logo_for = _best_logos(logos or [])
    conn = connect(database_path)
    try:
        rows = conn.execute("SELECT * FROM channels").fetchall()
        for row in rows:
            if row["guide_source"] == "manual":
                continue
            found = _match(row, by_id, by_name)
            if found is None:
                summary["missed"] += 1
                continue
            summary["matched"] += 1
            org = logo_for.get(found["id"])
            org_url = None if org is None else org["url"]
            logo = None if row["logo_source"] in {"manual", "m3u"} else org
            logo_url = None if logo is None else logo["url"]
            conn.execute(
                """
                UPDATE channels
                SET tvg_id = COALESCE(tvg_id, ?),
                    guide_match_id = ?,
                    guide_source = 'iptv-org',
                    logo_url = CASE WHEN logo_source IN ('manual', 'm3u') OR ? IS NULL THEN logo_url ELSE ? END,
                    logo_source = CASE WHEN logo_source IN ('manual', 'm3u') OR ? IS NULL THEN logo_source ELSE 'iptv-org' END,
                    org_logo_url = COALESCE(?, org_logo_url),
                    updated_at = ?
                WHERE id = ?
                """,
                (found["id"], found["id"], logo_url, logo_url, logo_url, org_url, utcnow(), row["id"]),
            )
            if logo is not None:
                summary["logos"] += 1
        summary["programmes"] = _load_programmes(conn, getter, cache, guides or [])
        conn.commit()
    finally:
        conn.close()
    from app.epg_feeds import refresh_programme_feeds

    feed_summary = refresh_programme_feeds(database_path, fetch=getter)
    summary["programmes"] += feed_summary["programmes"]
    summary["feeds"] = feed_summary["feeds"]
    log.info(
        "guide match: %s matched, %s missed, %s logos, %s programmes",
        summary["matched"],
        summary["missed"],
        summary["logos"],
        summary["programmes"],
    )
    conn = connect(database_path)
    try:
        set_setting(conn, "guide_refreshed_at", utcnow())
        conn.commit()
    finally:
        conn.close()
    return summary


def refresh_due(database_path: str, fetch=None) -> None:
    conn = connect(database_path)
    try:
        raw = setting(conn, "guide_refreshed_at") or ""
    finally:
        conn.close()
    if raw:
        from app.db import parse_stamp
        from datetime import datetime, timezone

        try:
            last = parse_stamp(raw)
        except ValueError:
            last = None
        if last is not None and (datetime.now(timezone.utc) - last).total_seconds() < REFRESH_SECONDS:
            return
    try:
        refresh_guide(database_path, fetch=fetch)
    except OSError as exc:
        log.warning("guide refresh failed: %s", exc)


def _match(row, by_id, by_name):
    tvg_id = row["tvg_id"]
    if tvg_id and tvg_id in by_id:
        return by_id[tvg_id]
    country = (row["country"] or "").upper()
    name = normalize_name(row["tvg_name"] or row["title"])
    if not name:
        return None
    found = by_name.get((name, country))
    if found is None and country in COUNTRY_ALIAS:
        found = by_name.get((name, COUNTRY_ALIAS[country]))
    return found


def _indexes(channels: list[dict]):
    by_id = {}
    by_name = {}
    for item in channels:
        if not item.get("id") or item.get("closed"):
            continue
        by_id[item["id"]] = item
        country = (item.get("country") or "").upper()
        for label in [item.get("name"), *(item.get("alt_names") or [])]:
            key = (normalize_name(label), country)
            if key[0] and key not in by_name:
                by_name[key] = item
    return by_id, by_name


def _best_logos(logos: list[dict]) -> dict[str, dict]:
    chosen: dict[str, dict] = {}
    for logo in logos:
        channel = logo.get("channel")
        if not channel or not logo.get("url"):
            continue
        current = chosen.get(channel)
        if current is None or _logo_rank(logo) > _logo_rank(current):
            chosen[channel] = logo
    return chosen


def _logo_rank(logo: dict) -> tuple:
    path = urlsplit(logo.get("url") or "").path.lower()
    image = 1 if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")) else 0
    return (image, 1 if logo.get("in_use") else 0, 0 if logo.get("feed") else 1, int(logo.get("width") or 0))


def _load_programmes(conn, getter, cache: Path, guides: list[dict]) -> int:
    stored = 0
    playlists = conn.execute("SELECT id, epg_url FROM playlists WHERE epg_url IS NOT NULL AND epg_url != ''").fetchall()
    for playlist in playlists:
        text = _cached_text(cache, f"playlist-{playlist['id']}.xml", playlist["epg_url"], getter)
        stored += _store_quiet(conn, text, "m3u")
    ids = {
        row["guide_match_id"]
        for row in conn.execute("SELECT guide_match_id FROM channels WHERE guide_match_id IS NOT NULL")
    }
    urls: dict[str, int] = {}
    for guide in guides:
        channel = guide.get("channel")
        if channel not in ids:
            continue
        for source in guide.get("sources") or []:
            url = source.get("url") if isinstance(source, dict) else None
            if url:
                urls[url] = urls.get(url, 0) + 1
    if ids and not urls and not playlists:
        log.info("iptv-org guide index has no XMLTV URLs for the matched channels")
    for url, _count in sorted(urls.items(), key=lambda item: item[1], reverse=True)[:3]:
        text = _cached_text(cache, _safe_name(url), url, getter)
        stored += _store_quiet(conn, text, "iptv-org")
    return stored


def _store_quiet(conn, text: str | None, source: str) -> int:
    if not text:
        return 0
    try:
        result = replace_epg(conn, text)
    except EpgParseError as exc:
        log.warning("guide XML was skipped: %s", exc)
        return 0
    if source == "m3u":
        conn.execute(
            """
            UPDATE channels SET guide_source = 'm3u'
            WHERE guide_source IS NULL AND id IN (SELECT DISTINCT channel_id FROM programs)
            """
        )
    return int(result["stored"])


def _cached_json(cache: Path, name: str, url: str, getter):
    text = _cached_text(cache, name, url, getter)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("guide cache %s is not JSON: %s", name, exc)
        return None


def _cached_text(cache: Path, name: str, url: str, getter) -> str | None:
    target = cache / name
    try:
        payload = getter(url)
    except Exception as exc:
        log.warning("guide download failed for %s: %s", url, exc)
        payload = None
    if payload:
        target.write_bytes(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
    elif not target.is_file():
        return None
    raw = target.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            log.warning("guide gzip failed for %s: %s", name, exc)
            return None
    return raw.decode("utf-8", errors="replace")


def _http_get(url: str) -> bytes | None:
    try:
        response = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": "HomeIPTV/0.1"})
    except httpx.HTTPError as exc:
        log.warning("guide request failed for %s: %s", url, exc)
        return None
    if response.status_code != 200:
        log.warning("guide URL returned HTTP %s for %s", response.status_code, url)
        return None
    if len(response.content) > MAX_BYTES:
        log.warning("guide download is too large: %s", url)
        return None
    return response.content


def _safe_name(url: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", url)[-80:]
    return f"{cleaned}.xml"
