import re
from dataclasses import dataclass, field

ATTR_RE = re.compile(r"([A-Za-z0-9_-]+)=\"([^\"]*)\"")
MAX_PLAYLIST_BYTES = 20 * 1024 * 1024


class PlaylistParseError(Exception):
    pass


@dataclass
class ParsedChannel:
    url: str
    title: str
    tvg_id: str | None = None
    tvg_name: str | None = None
    logo_url: str | None = None
    tvg_language: str | None = None
    tvg_country: str | None = None
    group_title: str | None = None


@dataclass
class ParseResult:
    channels: list[ParsedChannel] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _split_extinf(body: str) -> tuple[str, str]:
    in_quotes = False
    for index, char in enumerate(body):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            return body[:index], body[index + 1 :].strip()
    return body, ""


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def parse_playlist(text: str) -> ParseResult:
    if text.startswith("\ufeff"):
        text = text[1:]
    result = ParseResult()
    pending: tuple[int, dict[str, str], str] | None = None
    seen: dict[str, int] = {}

    def flush_missing(line_no: int) -> None:
        nonlocal pending
        if pending is not None:
            result.warnings.append(f"line {pending[0]}: EXTINF without a URL")
            pending = None
        return line_no

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            flush_missing(line_no)
            body = line.split(":", 1)[1] if ":" in line else ""
            meta, display = _split_extinf(body)
            attrs = {key.lower(): value for key, value in ATTR_RE.findall(meta)}
            pending = (line_no, attrs, display)
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            result.warnings.append(f"line {line_no}: URL without EXTINF")
            continue
        _line_no, attrs, display = pending
        pending = None
        url = line
        title = _blank(display) or _blank(attrs.get("tvg-name")) or url
        channel = ParsedChannel(
            url=url,
            title=title,
            tvg_id=_blank(attrs.get("tvg-id")),
            tvg_name=_blank(attrs.get("tvg-name")),
            logo_url=_blank(attrs.get("tvg-logo")),
            tvg_language=_blank(attrs.get("tvg-language")),
            tvg_country=_blank(attrs.get("tvg-country")),
            group_title=_blank(attrs.get("group-title")),
        )
        if url in seen:
            result.warnings.append(f"line {line_no}: duplicate URL, keeping the later entry")
            result.channels[seen[url]] = channel
            continue
        seen[url] = len(result.channels)
        result.channels.append(channel)

    if pending is not None:
        result.warnings.append(f"line {pending[0]}: EXTINF without a URL")
    if not result.channels:
        raise PlaylistParseError("playlist did not contain any channels")
    return result
