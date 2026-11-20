import logging
import subprocess
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db import connect, setting, set_setting
from app.hdhomerun import (
    DEVICE_TYPE_TUNER,
    DEVICE_ID_WILDCARD,
    TYPE_DISCOVER_REQ,
    DiscoverySocket,
    build_discover_reply,
    parse_packet,
)
from app.hub import StreamOffline, StreamStartError, TunersBusy

log = logging.getLogger(__name__)
router = APIRouter()


def emulation_enabled(database_path: str) -> bool:
    conn = connect(database_path)
    try:
        return setting(conn, "hdhr_emulation") == "1"
    finally:
        conn.close()


def emulated_device_id(database_path: str) -> str:
    conn = connect(database_path)
    try:
        current = (setting(conn, "device_id") or "").strip().upper()
        if current:
            return current
        import secrets

        generated = secrets.token_hex(4).upper()
        set_setting(conn, "device_id", generated)
        conn.commit()
        return generated
    finally:
        conn.close()


def reply_to_discover(packet: bytes, enabled: bool, device_id: int, tuner_count: int, base_url: str) -> bytes | None:
    if not enabled:
        return None
    parsed = parse_packet(packet)
    if parsed is None or parsed.packet_type != TYPE_DISCOVER_REQ:
        return None
    if parsed.device_type not in (None, DEVICE_TYPE_TUNER, DEVICE_ID_WILDCARD):
        return None
    base = base_url.rstrip("/")
    return build_discover_reply(device_id, tuner_count, base, f"{base}/lineup.json")


def _require_enabled(request: Request) -> None:
    if not emulation_enabled(request.app.state.cfg.database_path):
        raise HTTPException(404, "HDHomeRun emulation is off")


def _discover_body(request: Request) -> dict:
    cfg = request.app.state.cfg
    conn = connect(cfg.database_path)
    try:
        name = setting(conn, "emulated_friendly_name") or "Home IPTV"
        count = int(setting(conn, "emulated_tuner_count") or "4")
    finally:
        conn.close()
    base = cfg.public_http_base
    return {
        "FriendlyName": name,
        "ModelNumber": "HOME-IPTV",
        "FirmwareName": "home-iptv",
        "FirmwareVersion": "0.1.0",
        "DeviceID": emulated_device_id(cfg.database_path),
        "TunerCount": count,
        "BaseURL": base,
        "LineupURL": f"{base}/lineup.json",
    }


@router.get("/discover.json")
def discover_json(request: Request) -> dict:
    _require_enabled(request)
    return _discover_body(request)


@router.get("/lineup_status.json")
def lineup_status(request: Request) -> dict:
    _require_enabled(request)
    return {
        "ScanInProgress": 0,
        "ScanPossible": 0,
        "Source": "Home IPTV",
        "SourceList": ["Home IPTV"],
    }


@router.get("/lineup.json")
def lineup_json(request: Request) -> list:
    _require_enabled(request)
    cfg = request.app.state.cfg
    conn = connect(cfg.database_path)
    try:
        rows = conn.execute(
            """
            SELECT guide_number, title, height
            FROM channels
            WHERE adult = 0 AND guide_number IS NOT NULL AND guide_number != ''
            ORDER BY guide_sort, id
            """
        ).fetchall()
    finally:
        conn.close()
    base = cfg.public_http_base
    lineup = []
    for row in rows:
        entry = {
            "GuideNumber": row["guide_number"],
            "GuideName": row["title"],
            "URL": f"{base}/auto/v{row['guide_number']}",
        }
        if row["height"] and row["height"] >= 720:
            entry["HD"] = 1
        lineup.append(entry)
    return lineup


@router.get("/auto/v{guide_number}")
def stream_guide(request: Request, guide_number: str):
    _require_enabled(request)
    cfg = request.app.state.cfg
    conn = connect(cfg.database_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM channels
            WHERE guide_number = ? AND adult = 0
            ORDER BY id
            LIMIT 1
            """,
            (guide_number,),
        ).fetchone()
        device = None
        if row is not None and row["device_id"]:
            device = conn.execute(
                "SELECT * FROM hdhomerun_devices WHERE id = ?",
                (row["device_id"],),
            ).fetchone()
        channel = None if row is None else dict(row)
        device_dict = None if device is None else dict(device)
    finally:
        conn.close()
    if channel is None:
        raise HTTPException(404, "channel not found")
    try:
        request.app.state.hub.ensure(channel, device_dict, origin="emulation")
    except TunersBusy as exc:
        raise HTTPException(503, "all tuners busy") from exc
    except StreamOffline as exc:
        raise HTTPException(409, str(exc)) from exc
    except StreamStartError as exc:
        raise HTTPException(502, str(exc)) from exc
    rtsp_url = request.app.state.hub.rtsp_url(channel["id"])
    return StreamingResponse(mpegts_chunks(rtsp_url), media_type="video/mp2t")


def mpegts_chunks(rtsp_url: str):
    # A local remux from the hub's RTSP publish. The upstream pull stays the
    # single ffmpeg the hub already started for this channel.
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-c",
        "copy",
        "-f",
        "mpegts",
        "pipe:1",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        log.warning("MPEG-TS remux did not start: %s", exc)
        return
    assert process.stdout is not None
    try:
        while True:
            chunk = process.stdout.read(188 * 32)
            if not chunk:
                break
            yield chunk
    finally:
        process.kill()
        process.wait(timeout=3)


class DiscoveryService:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.sock = DiscoverySocket(listen=True, required=True)
        self._thread: threading.Thread | None = None

    def start(self, stop: threading.Event) -> None:
        self._thread = threading.Thread(target=self._loop, args=(stop,), name="hdhr-discovery", daemon=True)
        self._thread.start()

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            received = self.sock.recv()
            if received is None:
                continue
            data, address = received
            try:
                reply = self._reply(data)
            except OSError as exc:
                log.warning("HDHomeRun discovery reply failed: %s", exc)
                continue
            except ValueError as exc:
                log.warning("HDHomeRun discovery reply was not built: %s", exc)
                continue
            if reply is None:
                continue
            try:
                self.sock.sendto(reply, address)
            except OSError as exc:
                log.warning("HDHomeRun discovery send failed: %s", exc)

    def _reply(self, data: bytes) -> bytes | None:
        enabled = emulation_enabled(self.cfg.database_path)
        if not enabled:
            return None
        conn = connect(self.cfg.database_path)
        try:
            count = int(setting(conn, "emulated_tuner_count") or "4")
        finally:
            conn.close()
        device_id = int(emulated_device_id(self.cfg.database_path), 16)
        return reply_to_discover(data, True, device_id, count, self.cfg.public_http_base)

    def close(self) -> None:
        self.sock.close()


def start_discovery(cfg, stop: threading.Event) -> DiscoveryService | None:
    try:
        service = DiscoveryService(cfg)
    except OSError as exc:
        log.warning("HDHomeRun discovery port %s was not opened: %s", 65001, exc)
        return None
    service.start(stop)
    return service
