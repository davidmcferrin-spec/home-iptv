import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.catalog import import_lineup_channels
from app.db import connect, init_db
from app.hdhomerun import (
    build_discover_reply,
    build_discover_request,
    device_id_text,
    fetch_device,
    parse_discover_json,
    parse_lineup,
    parse_packet,
    tuner_slot_open,
)

LINEUP = json.loads((Path(__file__).parent / "fixtures" / "lineup.json").read_text(encoding="utf-8"))


def test_lineup_parser_keeps_guide_numbers():
    channels = parse_lineup(LINEUP)
    assert [channel.guide_number for channel in channels] == ["2.1", "11.1"]
    assert channels[0].guide_name == "Local News"
    assert channels[0].url.endswith("/auto/v2.1")
    assert channels[0].hd is True
    assert channels[1].hd is False


def test_discover_json_parser():
    info = parse_discover_json(
        {
            "FriendlyName": "HDHomeRun CONNECT",
            "ModelNumber": "HDHR4-2US",
            "DeviceID": "103a1b2c",
            "TunerCount": 2,
            "BaseURL": "http://10.0.0.8:80",
            "LineupURL": "http://10.0.0.8:80/lineup.json",
        }
    )
    assert info["device_id"] == "103A1B2C"
    assert info["tuner_count"] == 2
    assert info["base_url"] == "http://10.0.0.8:80"


def test_discovery_packet_roundtrip():
    request = parse_packet(build_discover_request())
    assert request is not None
    assert request.packet_type == 0x0002
    assert request.device_type == 1
    assert request.device_id == 0xFFFFFFFF
    reply = build_discover_reply(
        device_id=0x103A1B2C,
        tuner_count=2,
        base_url="http://10.0.0.8:80",
        lineup_url="http://10.0.0.8:80/lineup.json",
    )
    parsed = parse_packet(reply)
    assert parsed is not None
    assert parsed.packet_type == 0x0003
    assert device_id_text(parsed.device_id) == "103A1B2C"
    assert parsed.tuner_count == 2
    assert parsed.base_url == "http://10.0.0.8:80"
    assert parsed.lineup_url.endswith("/lineup.json")
    tampered = bytearray(reply)
    tampered[6] ^= 0xFF
    assert parse_packet(bytes(tampered)) is None


def test_tuner_slot_is_shared_per_channel():
    assert tuner_slot_open(2, {1, 2}, 3) is False
    assert tuner_slot_open(2, {1, 2}, 2) is True
    assert tuner_slot_open(2, {1}, 3) is True


def test_lineup_import_tags_ota_and_keeps_guide_numbers(tmp_path):
    database = tmp_path / "t.sqlite"
    init_db(str(database))
    conn = connect(str(database))
    try:
        conn.execute(
            """
            INSERT INTO hdhomerun_devices(host, tuner_count, manual, last_seen_at)
            VALUES ('http://10.0.0.8', 2, 1, '2026-01-01T00:00:00Z')
            """
        )
        device_id = conn.execute("SELECT id FROM hdhomerun_devices").fetchone()["id"]
        summary = import_lineup_channels(conn, device_id, parse_lineup(LINEUP))
        conn.commit()
        assert summary["imported"] == 2
        rows = conn.execute("SELECT guide_number, title FROM channels ORDER BY guide_sort").fetchall()
        assert [row["guide_number"] for row in rows] == ["2.1", "11.1"]
        tags = conn.execute(
            """
            SELECT c.guide_number FROM channels c
            JOIN channel_tags ct ON ct.channel_id = c.id
            JOIN tags t ON t.id = ct.tag_id
            WHERE t.name = 'OTA'
            ORDER BY c.guide_sort
            """
        ).fetchall()
        assert [row["guide_number"] for row in tags] == ["2.1", "11.1"]
    finally:
        conn.close()


def test_fetch_device_from_manual_host():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/discover.json":
                body = json.dumps(
                    {
                        "DeviceID": "ABCDEF01",
                        "FriendlyName": "Test Tuner",
                        "ModelNumber": "HDHR-TEST",
                        "TunerCount": 2,
                        "BaseURL": f"http://127.0.0.1:{self.server.server_address[1]}",
                        "LineupURL": f"http://127.0.0.1:{self.server.server_address[1]}/lineup.json",
                    }
                ).encode()
            elif self.path == "/lineup.json":
                body = json.dumps(LINEUP).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{server.server_address[1]}"
        canonical, info, channels = fetch_device(host)
        assert canonical.startswith("http://127.0.0.1:")
        assert info["tuner_count"] == 2
        assert len(channels) == 2
    finally:
        server.shutdown()
        server.server_close()
