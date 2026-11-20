from pathlib import Path

from app.hdhomerun import build_discover_request
from app.emulation import reply_to_discover
from app.hub import TunersBusy

FIXTURE = Path(__file__).parent / "fixtures" / "sample.m3u"


def _import(client):
    response = client.post(
        "/api/playlists/upload",
        data={"name": "Sample"},
        files={"file": ("sample.m3u", FIXTURE.read_bytes(), "audio/mpegurl")},
    )
    assert response.status_code == 201, response.text


def test_emulation_is_off_until_enabled_and_hides_adult(client):
    assert client.get("/discover.json").status_code == 404
    _import(client)
    enabled = client.patch("/api/settings", json={"hdhr_emulation": True, "emulated_tuner_count": 2})
    assert enabled.status_code == 200
    discover = client.get("/discover.json")
    assert discover.status_code == 200
    body = discover.json()
    assert body["ModelNumber"] == "HOME-IPTV"
    assert len(body["DeviceID"]) == 8
    assert body["TunerCount"] == 2
    assert body["LineupURL"].endswith("/lineup.json")
    lineup = client.get("/lineup.json").json()
    names = [entry["GuideName"] for entry in lineup]
    assert "Midnight XXX" not in names
    assert "Adult Swim East" in names
    assert all(entry["URL"].rsplit("/", 1)[-1].startswith("v") for entry in lineup)
    status = client.get("/lineup_status.json")
    assert status.status_code == 200
    assert status.json()["ScanInProgress"] == 0


def test_discover_reply_follows_the_request_packet():
    reply = reply_to_discover(
        build_discover_request(),
        enabled=True,
        device_id=0xABCDEF01,
        tuner_count=4,
        base_url="http://lan:8080",
    )
    assert reply is not None
    assert reply_to_discover(build_discover_request(), False, 1, 1, "http://lan:8080") is None


def test_watch_reports_busy_tuners_and_hides_adult(client, monkeypatch):
    _import(client)
    calls = []

    def ensure(channel, device, origin):
        calls.append((channel["id"], origin))
        if len(calls) > 1:
            raise TunersBusy()
        return {
            "channel_id": channel["id"],
            "path": f"ch{channel['id']}",
            "whep_url": f"http://lan:8889/ch{channel['id']}/whep",
            "llhls_url": f"http://lan:8888/ch{channel['id']}/index.m3u8",
            "transcode": "audio",
            "idle_timeout_seconds": 60,
            "upstream_pulls": 1,
        }

    monkeypatch.setattr(client.app.state.hub, "ensure", ensure)
    channels = client.get("/api/channels", params={"include_adult": "true"}).json()["channels"]
    visible = next(channel for channel in channels if channel["title"] == "City News Updated")
    adult = next(channel for channel in channels if channel["title"] == "Midnight XXX")
    assert client.post(f"/api/channels/{adult['id']}/watch").status_code == 404
    watched = client.post(f"/api/channels/{visible['id']}/watch")
    assert watched.status_code == 200
    assert watched.json()["whep_url"].endswith("/whep")
    assert watched.json()["llhls_url"].endswith("/index.m3u8")
    busy = client.post(f"/api/channels/{visible['id']}/watch")
    assert busy.status_code == 503
    assert busy.json()["detail"] == "all tuners busy"

    client.patch("/api/settings", json={"hdhr_emulation": True})
    monkeypatch.setattr("app.emulation.mpegts_chunks", lambda url: iter([b"\x47" * 188]))
    calls.clear()
    streamed = client.get(f"/auto/v{visible['guide_number']}")
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("video/mp2t")
    assert streamed.content.startswith(b"\x47")
    assert calls[0][1] == "emulation"
