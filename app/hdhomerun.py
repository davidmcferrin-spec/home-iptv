import json
import socket
import time
import zlib
from urllib.parse import urlparse

import httpx
from dataclasses import dataclass

DISCOVER_PORT = 65001
TYPE_DISCOVER_REQ = 0x0002
TYPE_DISCOVER_RPY = 0x0003
DEVICE_TYPE_TUNER = 0x00000001
DEVICE_ID_WILDCARD = 0xFFFFFFFF

TAG_DEVICE_TYPE = 0x01
TAG_DEVICE_ID = 0x02
TAG_TUNER_COUNT = 0x10
TAG_LINEUP_URL = 0x27
TAG_BASE_URL = 0x2A


class PacketError(Exception):
    pass


@dataclass(frozen=True)
class DiscoverPacket:
    packet_type: int
    device_type: int | None
    device_id: int | None
    tuner_count: int | None
    base_url: str | None
    lineup_url: str | None


@dataclass(frozen=True)
class LineupChannel:
    guide_number: str
    guide_name: str
    url: str
    hd: bool


def _encode_length(length: int) -> bytes:
    if length < 0:
        raise PacketError("negative tag length")
    if length <= 127:
        return bytes([length])
    if length > 0x7FFF:
        raise PacketError("tag value is too long")
    return bytes([(length & 0x7F) | 0x80, (length >> 7) & 0xFF])


def _read_length(payload: bytes, index: int) -> tuple[int, int]:
    if index >= len(payload):
        raise PacketError("truncated tag length")
    first = payload[index]
    if first & 0x80 == 0:
        return first, index + 1
    if index + 1 >= len(payload):
        raise PacketError("truncated two-byte tag length")
    return (first & 0x7F) | (payload[index + 1] << 7), index + 2


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _crc(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def build_packet(packet_type: int, payload: bytes) -> bytes:
    header = packet_type.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload
    return header + _crc(header).to_bytes(4, "little")


def build_discover_request() -> bytes:
    payload = _tlv(TAG_DEVICE_TYPE, DEVICE_TYPE_TUNER.to_bytes(4, "big"))
    payload += _tlv(TAG_DEVICE_ID, DEVICE_ID_WILDCARD.to_bytes(4, "big"))
    return build_packet(TYPE_DISCOVER_REQ, payload)


def build_discover_reply(
    device_id: int,
    tuner_count: int,
    base_url: str,
    lineup_url: str,
) -> bytes:
    payload = _tlv(TAG_DEVICE_TYPE, DEVICE_TYPE_TUNER.to_bytes(4, "big"))
    payload += _tlv(TAG_DEVICE_ID, (device_id & 0xFFFFFFFF).to_bytes(4, "big"))
    payload += _tlv(TAG_TUNER_COUNT, int(tuner_count).to_bytes(4, "big"))
    payload += _tlv(TAG_BASE_URL, base_url.encode("utf-8"))
    payload += _tlv(TAG_LINEUP_URL, lineup_url.encode("utf-8"))
    return build_packet(TYPE_DISCOVER_RPY, payload)


def parse_packet(data: bytes) -> DiscoverPacket | None:
    if len(data) < 8:
        return None
    body, crc_bytes = data[:-4], data[-4:]
    if _crc(body) != int.from_bytes(crc_bytes, "little"):
        return None
    packet_type = int.from_bytes(body[0:2], "big")
    length = int.from_bytes(body[2:4], "big")
    if length != len(body) - 4:
        return None
    payload = body[4:]
    values: dict[int, bytes] = {}
    index = 0
    try:
        while index < len(payload):
            tag = payload[index]
            tag_len, index = _read_length(payload, index + 1)
            if index + tag_len > len(payload):
                return None
            values[tag] = payload[index : index + tag_len]
            index += tag_len
    except PacketError:
        return None

    def integer(tag: int) -> int | None:
        raw = values.get(tag)
        if raw is None or len(raw) == 0:
            return None
        if len(raw) == 1:
            return raw[0]
        return int.from_bytes(raw[:4], "big")

    def text(tag: int) -> str | None:
        raw = values.get(tag)
        if not raw:
            return None
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip() or None

    return DiscoverPacket(
        packet_type=packet_type,
        device_type=integer(TAG_DEVICE_TYPE),
        device_id=integer(TAG_DEVICE_ID),
        tuner_count=integer(TAG_TUNER_COUNT),
        base_url=text(TAG_BASE_URL),
        lineup_url=text(TAG_LINEUP_URL),
    )


def device_id_text(device_id: int | None) -> str | None:
    if device_id is None or device_id == DEVICE_ID_WILDCARD:
        return None
    return f"{device_id & 0xFFFFFFFF:08X}"


def parse_discover_json(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("discover.json must be an object")
    device_id = str(payload.get("DeviceID") or "").strip()
    base_url = str(payload.get("BaseURL") or "").strip()
    if not device_id and not base_url:
        raise ValueError("discover.json is missing DeviceID and BaseURL")
    tuner_raw = payload.get("TunerCount", 1)
    try:
        tuner_count = max(1, int(tuner_raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("TunerCount is not an integer") from exc
    return {
        "device_id": device_id.upper() or None,
        "friendly_name": str(payload.get("FriendlyName") or "").strip() or None,
        "model": str(payload.get("ModelNumber") or "").strip() or None,
        "tuner_count": tuner_count,
        "base_url": base_url or None,
        "lineup_url": str(payload.get("LineupURL") or "").strip() or None,
    }


def parse_lineup(payload: object) -> list[LineupChannel]:
    if not isinstance(payload, list):
        raise ValueError("lineup.json must be a list")
    channels: list[LineupChannel] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"lineup entry {index} is not an object")
        guide_number = str(item.get("GuideNumber") or "").strip()
        guide_name = str(item.get("GuideName") or "").strip()
        url = str(item.get("URL") or "").strip()
        if not guide_number or not url:
            raise ValueError(f"lineup entry {index} is missing GuideNumber or URL")
        channels.append(
            LineupChannel(
                guide_number=guide_number,
                guide_name=guide_name or f"Channel {guide_number}",
                url=url,
                hd=bool(item.get("HD")),
            )
        )
    return channels


class DeviceFetchError(Exception):
    pass


def normalize_base(host: str) -> str:
    text = host.strip()
    if not text:
        raise DeviceFetchError("host is empty")
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DeviceFetchError("host must be an http address or IP")
    return f"{parsed.scheme}://{parsed.netloc}"


def fetch_device(host: str) -> tuple[str, dict, list[LineupChannel]]:
    base = normalize_base(host)
    try:
        discover_response = httpx.get(f"{base}/discover.json", timeout=8, follow_redirects=True)
    except httpx.ConnectError as exc:
        raise DeviceFetchError(f"could not connect to {base}: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise DeviceFetchError(f"timed out contacting {base}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise DeviceFetchError(f"discover request failed: {exc}") from exc
    if discover_response.status_code != 200:
        raise DeviceFetchError(f"discover.json returned HTTP {discover_response.status_code}")
    try:
        discover_payload = discover_response.json()
    except json.JSONDecodeError as exc:
        raise DeviceFetchError("discover.json is not JSON") from exc
    try:
        info = parse_discover_json(discover_payload)
    except ValueError as exc:
        raise DeviceFetchError(str(exc)) from exc
    canonical = info["base_url"] or base
    lineup_url = info["lineup_url"] or f"{canonical.rstrip('/')}/lineup.json"
    try:
        lineup_response = httpx.get(lineup_url, timeout=20, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise DeviceFetchError(f"lineup request failed: {exc}") from exc
    if lineup_response.status_code != 200:
        raise DeviceFetchError(f"lineup.json returned HTTP {lineup_response.status_code}")
    try:
        lineup_payload = lineup_response.json()
    except json.JSONDecodeError as exc:
        raise DeviceFetchError("lineup.json is not JSON") from exc
    try:
        channels = parse_lineup(lineup_payload)
    except ValueError as exc:
        raise DeviceFetchError(str(exc)) from exc
    return canonical, info, channels


def tuner_slot_open(tuner_count: int, active_channel_ids: set[int], channel_id: int) -> bool:
    if channel_id in active_channel_ids:
        return True
    return len(active_channel_ids) < tuner_count


def scan_lan(timeout_seconds: float) -> list[tuple[tuple, DiscoverPacket]]:
    sock = DiscoverySocket(listen=False)
    found: list[tuple[tuple, DiscoverPacket]] = []
    try:
        sock.send_request()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            received = sock.recv()
            if received is None:
                continue
            data, address = received
            parsed = parse_packet(data)
            if parsed is None or parsed.packet_type != TYPE_DISCOVER_RPY:
                continue
            found.append((address, parsed))
    finally:
        sock.close()
    return found


class DiscoverySocket:
    """One UDP socket for scans and, when emulation is on, for replies.

    Docker bridge networking drops broadcast replies, so a manual IP remains
    the reliable path unless the container uses host networking or macvlan.
    """

    def __init__(self, listen: bool, required: bool = False) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.bound_port: int | None = None
        if listen:
            try:
                self.sock.bind(("0.0.0.0", DISCOVER_PORT))
                self.bound_port = DISCOVER_PORT
            except OSError:
                if required:
                    self.sock.close()
                    raise
                # A scan can still send from an ephemeral port. Replies are unicast
                # back to that port; only the emulation listener must hold 65001.
                self.sock.bind(("0.0.0.0", 0))
                self.bound_port = self.sock.getsockname()[1]
        else:
            self.sock.bind(("0.0.0.0", 0))
            self.bound_port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)

    def send_request(self) -> None:
        packet = build_discover_request()
        self.sock.sendto(packet, ("255.255.255.255", DISCOVER_PORT))

    def recv(self) -> tuple[bytes, tuple] | None:
        try:
            return self.sock.recvfrom(4096)
        except TimeoutError:
            return None
        except socket.timeout:
            return None

    def sendto(self, packet: bytes, address: tuple) -> None:
        self.sock.sendto(packet, address)

    def close(self) -> None:
        self.sock.close()
