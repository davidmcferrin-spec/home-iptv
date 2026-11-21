import gzip
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from app.db import stamp

NS_RE = re.compile(r"\{[^}]+\}")
ONSCREEN_RE = re.compile(r"S(\d+)\s*E(\d+)", re.IGNORECASE)


class EpgParseError(Exception):
    pass


def _local(tag: str) -> str:
    return NS_RE.sub("", tag)


def _text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_when(value: str) -> str:
    text = value.strip()
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return stamp(parsed)
    raise EpgParseError(f"could not parse programme time {value!r}")


def _episode(programme: ET.Element) -> tuple[int | None, int | None]:
    for child in programme:
        if _local(child.tag) != "episode-num":
            continue
        system = (child.get("system") or "").lower()
        text = (child.text or "").strip()
        if system == "xmltv_ns":
            parts = text.split(".")
            season = int(parts[0]) + 1 if parts and parts[0].isdigit() else None
            episode = int(parts[1]) + 1 if len(parts) > 1 and parts[1].isdigit() else None
            return season, episode
        if system == "onscreen":
            match = ONSCREEN_RE.search(text)
            if match:
                return int(match.group(1)), int(match.group(2))
    return None, None


def parse_xmltv(text: str) -> tuple[dict[str, list[str]], list[dict]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise EpgParseError(f"invalid XMLTV: {exc}") from exc
    if _local(root.tag) != "tv":
        raise EpgParseError("XMLTV root element must be tv")

    names: dict[str, list[str]] = {}
    for channel in root:
        if _local(channel.tag) != "channel":
            continue
        channel_id = channel.get("id")
        if not channel_id:
            continue
        display = []
        for child in channel:
            if _local(child.tag) == "display-name" and child.text and child.text.strip():
                display.append(child.text.strip())
        names[channel_id] = display

    programmes = []
    for node in root:
        if _local(node.tag) != "programme":
            continue
        channel_id = node.get("channel")
        start = node.get("start")
        stop = node.get("stop")
        title = _text(node, "title")
        if not channel_id or not start or not stop or not title:
            continue
        season, episode = _episode(node)
        programmes.append(
            {
                "xmltv_id": channel_id,
                "start_at": _parse_when(start),
                "stop_at": _parse_when(stop),
                "title": title,
                "episode_title": _text(node, "sub-title"),
                "description": _text(node, "desc"),
                "season": season,
                "episode": episode,
            }
        )
    return names, programmes


def open_xmltv(path: Path):
    raw = path.open("rb")
    magic = raw.read(2)
    raw.seek(0)
    if magic == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=raw)
    return raw


def iter_xmltv(path: Path):
    """Yield channel and programme events without keeping the document."""
    stream = open_xmltv(path)
    try:
        try:
            iterator = ET.iterparse(stream, events=("start", "end"))
            _event, root = next(iterator)
            for event, element in iterator:
                if event != "end":
                    continue
                tag = _local(element.tag)
                if tag == "channel":
                    channel_id = element.get("id")
                    display = []
                    for child in list(element):
                        if _local(child.tag) == "display-name" and child.text and child.text.strip():
                            display.append(child.text.strip())
                    if channel_id:
                        yield ("channel", channel_id, display)
                    element.clear()
                    root.clear()
                elif tag == "programme":
                    channel_id = element.get("channel")
                    start = element.get("start")
                    stop = element.get("stop")
                    title = _text(element, "title")
                    if channel_id and start and stop and title:
                        try:
                            start_at = _parse_when(start)
                            stop_at = _parse_when(stop)
                        except EpgParseError:
                            element.clear()
                            root.clear()
                            continue
                        season, episode = _episode(element)
                        yield (
                            "programme",
                            {
                                "xmltv_id": channel_id,
                                "start_at": start_at,
                                "stop_at": stop_at,
                                "title": title,
                                "episode_title": _text(element, "sub-title"),
                                "description": _text(element, "desc"),
                                "season": season,
                                "episode": episode,
                            },
                        )
                    element.clear()
                    root.clear()
        except ET.ParseError as exc:
            raise EpgParseError(f"invalid XMLTV: {exc}") from exc
    finally:
        stream.close()
