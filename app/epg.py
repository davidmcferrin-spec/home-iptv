import re
import xml.etree.ElementTree as ET
from datetime import datetime

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
