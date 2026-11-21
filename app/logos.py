import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import httpx

NEGATIVE_SECONDS = 24 * 60 * 60
MAX_BYTES = 2_000_000


def acceptable_logo(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url.strip())
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host or "_" in host:
        return None
    if parsed.username or parsed.password:
        return None
    labels = host.split(".")
    if any(not label or not all(char.isalnum() or char == "-" for char in label) for label in labels):
        return None
    if len(labels) < 2:
        return None
    return url.strip()


def initials_svg(title: str) -> bytes:
    letters = "".join(char for char in title if char.isalnum())[:2].upper() or "TV"
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="10" fill="#1c2433"/>'
        f'<text x="32" y="40" text-anchor="middle" fill="#d7dde8" '
        f'font-family="sans-serif" font-size="24">{escape(letters)}</text>'
        "</svg>"
    )
    return body.encode()


def _magic_image(data: bytes, content_type: str) -> bool:
    kind = content_type.split(";")[0].strip().lower()
    if kind.startswith("image/") and kind not in {"image/svg+xml"}:
        if data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8") or data[:6] in {b"GIF87a", b"GIF89a"}:
            return True
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return True
    if kind in {"image/svg+xml", "text/xml", "application/xml", "text/plain"} or data.lstrip().startswith(b"<svg"):
        return b"<svg" in data[:200].lstrip() or data.lstrip().startswith(b"<?xml")
    return False


def cached_logo(cache_dir: Path, channel_id: int, title: str, urls: list[str]) -> tuple[bytes, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    image = cache_dir / f"{channel_id}.img"
    kind = cache_dir / f"{channel_id}.type"
    failed = cache_dir / f"{channel_id}.fail"
    if image.is_file() and kind.is_file():
        return image.read_bytes(), kind.read_text(encoding="utf-8") or "image/png"
    if failed.is_file() and time.time() - failed.stat().st_mtime < NEGATIVE_SECONDS:
        return initials_svg(title), "image/svg+xml"
    for url in urls:
        if acceptable_logo(url) is None:
            continue
        try:
            response = httpx.get(url, timeout=8, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        data = response.content[: MAX_BYTES + 1]
        if len(data) > MAX_BYTES:
            continue
        content_type = response.headers.get("content-type", "")
        if not _magic_image(data, content_type):
            continue
        stored = "image/svg+xml" if b"<svg" in data[:200] else content_type.split(";")[0].strip() or "image/png"
        image.write_bytes(data)
        kind.write_text(stored, encoding="utf-8")
        if failed.exists():
            failed.unlink()
        return data, stored
    failed.write_text("failed", encoding="utf-8")
    return initials_svg(title), "image/svg+xml"


_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\uFE0F"
    "\u200D"
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    cleaned = _EMOJI.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()
